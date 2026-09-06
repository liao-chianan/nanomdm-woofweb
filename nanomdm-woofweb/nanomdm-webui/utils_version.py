"""
版本與更新功能的核心邏輯。
用GitHub Tags API當作「有哪些版本」的主要依據(這個一定存在,不需要使用者額外建立正式Release),
Release的版本說明當作選配的加分資訊,有的話顯示,沒有的話優雅顯示「沒有額外說明」。
"""
import datetime
import os
import re
import shutil
import time

import requests

GITHUB_API_BASE = "https://api.github.com"


def _github_headers(github_token=None):
    """組出GitHub API請求的標頭。有帶token的話用來認證,把速率限制從
    未登入的60次/小時提升到5000次/小時。這個限制在實際使用時真的會遇到
    (實測過,共用對外IP很容易撞到),所以這裡支援選填token,不是純理論考量。
    """
    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    return headers


def _friendly_error_from_response(resp):
    """把GitHub API的錯誤回應,轉換成使用者看得懂、知道該怎麼辦的訊息,
    特別辨識「速率限制」這種常見情況,不是單純顯示HTTP狀態碼。
    """
    if resp.status_code == 403:
        try:
            body = resp.json()
        except Exception:
            body = {}
        message = body.get("message", "")
        if "rate limit" in message.lower():
            reset_ts = resp.headers.get("x-ratelimit-reset")
            reset_hint = ""
            if reset_ts:
                try:
                    reset_time = time.strftime("%H:%M", time.localtime(int(reset_ts)))
                    reset_hint = f",預估 {reset_time} 之後恢復"
                except Exception:
                    pass
            return f"GitHub API 請求次數已達上限(未登入每小時60次){reset_hint}。如果常遇到這個問題,可以在 .env 加上 GITHUB_TOKEN 設定,額度會提升到5000次/小時"
        return f"GitHub API 拒絕存取: {message or 'HTTP 403'}"
    if resp.status_code == 404:
        return "GitHub API 找不到指定的資源(請確認repo名稱、版本標籤是否正確)"
    return f"GitHub API回應異常: HTTP {resp.status_code}"


def version_sort_key(tag_name):
    """把類似v0.9、v0.98、v0.972這種版本標籤,轉換成可以正確排序的數值。

    這個專案的版本命名慣例,是把小數點後面的整串數字當成「十進位小數」比較
    (例如v0.9 < v0.92 < v0.97 < v0.972 < v0.98,完全比照小數大小排序),
    不是像semver那樣把小數點後面拆成獨立的整數版號比較——這是實際發生過的問題:
    v0.972因為972這個數字大於98,被舊邏輯誤判成比v0.98還新的版本,導致GitHub上
    明明已經發布了v0.98,「檢測更新」卻還是顯示v0.972是最新版本。

    做法:直接抓出版本字串開頭的「數字.數字」這一段,當成一個Python浮點數解析,
    完全符合這個專案版本號「越後面的小數越大代表越新」的實際慣例。
    格式不符預期(無法解析成數字)時,退回0.0,至少不會讓程式出錯,只是排序沒有意義。
    """
    match = re.match(r"^[vV]?(\d+(?:\.\d+)?)", tag_name)
    if match:
        return float(match.group(1))
    return 0.0


def get_current_version(version_file):
    """讀取本地記錄的目前安裝版本。找不到檔案時回傳None(代表版本未知,
    通常是這個功能上線之前就已經安裝好的舊環境,需要使用者手動確認一次)。
    """
    if not os.path.exists(version_file):
        return None
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            v = f.read().strip()
        return v or None
    except Exception:
        return None


def set_current_version(version_file, tag):
    """寫入目前安裝版本記錄"""
    os.makedirs(os.path.dirname(version_file), exist_ok=True)
    tmp_path = version_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(tag.strip())
    os.replace(tmp_path, version_file)


def fetch_github_tags(owner, repo, limit=30, timeout=15, github_token=None):
    """取得repo的所有tag清單。
    回傳 (tags, error)。tags是 [{"name": "v0.9", "sha": "..."}]的清單。
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/tags"
    try:
        resp = requests.get(url, params={"per_page": limit}, headers=_github_headers(github_token), timeout=timeout)
    except requests.RequestException as e:
        return None, f"連線失敗: {e}"
    if resp.status_code != 200:
        return None, _friendly_error_from_response(resp)
    try:
        data = resp.json()
    except Exception:
        return None, "GitHub API回應格式無法解析"
    tags = [{"name": t["name"], "sha": t["commit"]["sha"]} for t in data]
    return tags, None


def fetch_github_releases_map(owner, repo, timeout=15, github_token=None):
    """取得repo已發布的Release清單(只包含正式發布、非draft的),整理成
    {tag_name: {name, body, published_at}}的對照字典,給版本清單顯示「更新說明」用。
    沒有任何Release時回傳空字典,不算錯誤(不強制要求使用者一定要建立Release)。
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/releases"
    try:
        resp = requests.get(url, params={"per_page": 50}, headers=_github_headers(github_token), timeout=timeout)
    except requests.RequestException:
        return {}
    if resp.status_code != 200:
        return {}
    try:
        data = resp.json()
    except Exception:
        return {}
    result = {}
    for r in data:
        if r.get("draft"):
            continue
        result[r["tag_name"]] = {
            "name": r.get("name") or r["tag_name"],
            "body": r.get("body") or "",
            "published_at": r.get("published_at"),
        }
    return result


def map_repo_path_to_local(relative_path, cfg):
    """把「已經去除repo_subfolder前綴」的相對路徑,對應到本地實際檔案路徑。
    例如 "nanomdm-webui/app.py" -> "/opt/nanomdm-webui/app.py"
    對應不到任何已知目錄時回傳None。
    """
    for repo_prefix, local_root in cfg["path_map"].items():
        prefix = repo_prefix.rstrip("/") + "/"
        if relative_path.startswith(prefix):
            rest = relative_path[len(prefix):]
            return os.path.join(local_root, rest)
    return None


def compare_versions(owner, repo, base_tag, head_tag, cfg, timeout=20, github_token=None):
    """比對兩個版本之間,有哪些「符合條件」的檔案發生變化(新增/修改/刪除)。
    「符合條件」的定義:
      1. 路徑必須在repo_subfolder(例如"nanomdm-woofweb/")底下
      2. 副檔名必須在eligible_extensions清單裡(明確排除.csv/.json等資料檔案)

    回傳 (files, error)。files是清單,每個元素:
      {"repo_path": "nanomdm-webui/app.py"(已去除subfolder前綴),
       "local_path": "/opt/nanomdm-webui/app.py",
       "status": "added"/"modified"/"removed"}
    """
    subfolder = cfg["repo_subfolder"].rstrip("/") + "/"
    eligible_ext = tuple(cfg["eligible_extensions"])

    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/compare/{base_tag}...{head_tag}"
    try:
        resp = requests.get(url, headers=_github_headers(github_token), timeout=timeout)
    except requests.RequestException as e:
        return None, f"連線失敗: {e}"
    if resp.status_code == 404:
        return None, (
            f"比對版本失敗,GitHub 找不到「{base_tag}」或「{head_tag}」其中一個版本標籤。"
            f"請到 https://github.com/{owner}/{repo}/tags 確認這兩個標籤的名稱是否完全一致(注意大小寫、有無多餘空格)。"
        )
    if resp.status_code != 200:
        return None, _friendly_error_from_response(resp)
    try:
        data = resp.json()
    except Exception:
        return None, "GitHub API回應格式無法解析"

    files = []
    for f in data.get("files", []):
        filename = f.get("filename", "")
        if not filename.startswith(subfolder):
            continue  # 不在我們管理的資料夾範圍內(例如README.md、LICENSE),略過
        if not filename.lower().endswith(eligible_ext):
            continue  # 副檔名不符合(明確排除.csv/.json等資料檔案)

        relative_path = filename[len(subfolder):]
        local_path = map_repo_path_to_local(relative_path, cfg)
        if not local_path:
            continue  # 對應不到任何本地目錄,略過(理論上不該發生,防禦性寫法)

        status = f.get("status", "modified")
        # GitHub Compare API 對修改過的檔案,本身就會附上patch欄位(標準unified diff格式的
        # 逐行差異內容),不需要額外呼叫或自己重新計算diff。二進位檔案、或diff內容過大時,
        # GitHub不會提供這個欄位(官方文件明確說明),這裡優雅處理成None,呼叫端可以據此
        # 顯示「無法顯示差異」的提示,不會因為缺少這個欄位就出錯。
        patch = f.get("patch")
        files.append({"repo_path": relative_path, "local_path": local_path, "status": status, "patch": patch})

    return files, None


def fetch_raw_file_content(owner, repo, ref, repo_path_with_subfolder, timeout=15, github_token=None):
    """從GitHub抓取指定版本(ref可以是tag名稱)、指定路徑的檔案原始內容(bytes)。
    repo_path_with_subfolder要包含repo_subfolder前綴(完整的repo內路徑)。
    回傳 (content_bytes, error)。
    """
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{repo_path_with_subfolder}"
    headers = {}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        return None, f"連線失敗: {e}"
    if resp.status_code != 200:
        return None, f"下載失敗: HTTP {resp.status_code}"
    return resp.content, None


def list_update_history(backup_dir):
    """掃描更新備份目錄底下的子目錄,解析出過去每一次更新的時間跟目標版本,
    給[版本與更新]頁面的「更新歷程記錄」使用。

    每個子目錄的命名格式是「{YYYYMMDD_HHMMSS}_to_{target_tag}」(apply_version_update()
    建立備份時的命名慣例),從這個名稱反推出「什麼時候更新到了哪個版本」,不需要另外
    維護一份獨立的歷程記錄資料庫。

    回傳一個list,每個元素是{"timestamp", "target_version", "folder_name"},
    依時間新到舊排序。目錄不存在或解析失敗的項目會被跳過,不會讓整個函式出錯。
    """
    if not os.path.exists(backup_dir):
        return []

    history = []
    for folder_name in os.listdir(backup_dir):
        full_path = os.path.join(backup_dir, folder_name)
        if not os.path.isdir(full_path):
            continue
        match = re.match(r"^(\d{8}_\d{6})_to_(.+)$", folder_name)
        if not match:
            continue
        timestamp_raw, target_version = match.groups()
        try:
            dt = datetime.datetime.strptime(timestamp_raw, "%Y%m%d_%H%M%S")
            timestamp_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        history.append({
            "timestamp": timestamp_display,
            "target_version": target_version,
            "folder_name": folder_name,
        })

    history.sort(key=lambda h: h["timestamp"], reverse=True)
    return history


def apply_version_update(owner, repo, target_tag, files, cfg, backup_dir, github_token=None):
    """實際套用更新:對每個檔案,先備份現有內容(如果存在),再寫入新版本的內容。
    files是compare_versions()回傳的清單格式。
    對於status="removed"的檔案(新版本裡已經被刪除),本地也會跟著刪除(先備份)。

    回傳 (results, overall_ok, this_backup_dir)。results是每個檔案的結果清單:
      {"repo_path", "local_path", "ok", "error"}
    """
    subfolder = cfg["repo_subfolder"].rstrip("/") + "/"
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    this_backup_dir = os.path.join(backup_dir, f"{timestamp}_to_{target_tag}")
    os.makedirs(this_backup_dir, exist_ok=True)

    results = []
    overall_ok = True
    for f in files:
        repo_path = f["repo_path"]
        local_path = f["local_path"]
        status = f["status"]
        result = {"repo_path": repo_path, "local_path": local_path, "ok": False, "error": None}

        try:
            old_mode = None
            if os.path.exists(local_path):
                old_mode = os.stat(local_path).st_mode  # 更新前先記住舊檔案的權限設定,寫入新內容後要套用回去
                backup_target = os.path.join(this_backup_dir, repo_path.replace("/", "__"))
                shutil.copy2(local_path, backup_target)

            if status == "removed":
                if os.path.exists(local_path):
                    os.remove(local_path)
                result["ok"] = True
            else:
                full_repo_path = subfolder + repo_path
                content, err = fetch_raw_file_content(owner, repo, target_tag, full_repo_path, github_token=github_token)
                if content is None:
                    result["error"] = err
                    overall_ok = False
                    results.append(result)
                    continue
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                tmp_path = local_path + ".update_tmp"
                with open(tmp_path, "wb") as out_f:
                    out_f.write(content)
                os.replace(tmp_path, local_path)

                # 從GitHub重新抓下來的檔案內容,寫入時一律是系統預設權限(通常沒有執行位元),
                # 不會保留原本檔案的權限設定——這是實際發生過的問題:每次用這個更新機制拉取
                # 新版本後,shell腳本(.sh)會沒有執行權限,得靠其他地方的防禦性補救才能繼續運作。
                # 這裡改成:一般檔案先套用舊檔案的權限設定(如果本地本來就有這個檔案);
                # 不管是既有檔案還是這次更新才新增的全新檔案,只要是.sh結尾,一律無條件
                # 補上執行位元——每次更新都重新確保一次,不依賴「有沒有保留到舊權限」這件事。
                if old_mode is not None:
                    os.chmod(local_path, old_mode)
                if local_path.endswith(".sh"):
                    current_mode = os.stat(local_path).st_mode
                    os.chmod(local_path, current_mode | 0o111)

                result["ok"] = True
        except Exception as e:
            result["error"] = str(e)
            overall_ok = False

        results.append(result)

    return results, overall_ok, this_backup_dir


def determine_services_to_restart(local_paths, cfg):
    """依照這次實際變更到的本地檔案路徑,判斷需要重啟哪些服務。
    只重啟真的受影響的服務,不會每次更新都全部重啟。
    """
    service_map = cfg["service_map"]
    webui_root = cfg["path_map"].get("nanomdm-webui", "/opt/nanomdm-webui")
    services = set()
    for path in local_paths:
        basename = os.path.basename(path)
        if basename in service_map:
            services.add(service_map[basename])
            continue
        if path.startswith(webui_root):
            services.add(service_map.get("nanomdm-webui", "nanomdm-webui.service"))
    return sorted(services)
