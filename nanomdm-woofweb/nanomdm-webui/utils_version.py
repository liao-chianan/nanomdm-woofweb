"""
版本與更新功能的核心邏輯。
用GitHub Tags API當作「有哪些版本」的主要依據(這個一定存在,不需要使用者額外建立正式Release),
Release的版本說明當作選配的加分資訊,有的話顯示,沒有的話優雅顯示「沒有額外說明」。
"""
import datetime
import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import time
import urllib.parse

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


_COMMIT_TRAILER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")


def _strip_commit_trailers(message):
    """去掉commit訊息最後一段的署名區塊(例如Co-Authored-By、Claude-Session),
    規則跟git判斷trailer一樣:最後一段(空行之後)每一行都是「英文欄位名: 內容」才算。"""
    paragraphs = message.strip().split("\n\n")
    if len(paragraphs) > 1 and all(_COMMIT_TRAILER_RE.match(l) for l in paragraphs[-1].splitlines()):
        paragraphs.pop()
    return "\n\n".join(paragraphs).strip()


def fetch_commits_between(owner, repo, base_tag, head_tag, tag_by_sha=None, timeout=20, github_token=None):
    """列出從base_tag(目前版本)到head_tag(最新版本)之間、還沒套用的所有commit,
    給「檢測更新」顯示每次修改的說明文字用。不需要另外建立GitHub Release也看得到。

    tag_by_sha:{commit sha: tag名稱},有帶的話會標出每個commit對應的版本標籤。
    commit訊息最後的署名區塊(Co-Authored-By等)不顯示。
    回傳 (commits, total, error)。commits由新到舊排列,每個元素:
      {"sha": 前7碼, "tag": 版本標籤或空字串, "date": "YYYY-MM-DD", "message": 說明文字}
    GitHub的比對API最多回傳250個commit,total是實際總數(超過時呼叫端可以提示)。
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/compare/{base_tag}...{head_tag}"
    try:
        resp = requests.get(url, headers=_github_headers(github_token), timeout=timeout)
    except requests.RequestException as e:
        return None, 0, f"連線失敗: {e}"
    if resp.status_code != 200:
        return None, 0, _friendly_error_from_response(resp)
    try:
        data = resp.json()
    except Exception:
        return None, 0, "GitHub API回應格式無法解析"

    commits = [_commit_entry(c, tag_by_sha or {}) for c in data.get("commits") or []]
    commits.reverse()  # GitHub由舊到新排列,畫面上最新的放最上面
    return commits, data.get("total_commits", len(commits)), None


_CO_AUTHOR_RE = re.compile(r"^co-authored-by:\s*(.+?)\s*(<[^>]*>)?\s*$", re.IGNORECASE)


def _commit_entry(c, tag_by_sha):
    """把GitHub API的單筆commit整理成畫面要用的欄位。
    author:作者的GitHub帳號(對應不到帳號時用commit裡的作者名稱);
    co_authors:commit訊息裡Co-Authored-By列出的共同作者名稱(不含email)。"""
    info = c.get("commit") or {}
    raw_message = info.get("message") or ""
    co_authors = []
    for line in raw_message.splitlines():
        m = _CO_AUTHOR_RE.match(line.strip())
        if m and m.group(1) not in co_authors:
            co_authors.append(m.group(1))
    return {
        "sha": (c.get("sha") or "")[:7],
        "tag": tag_by_sha.get(c.get("sha") or "", ""),
        "date": ((info.get("committer") or {}).get("date") or "")[:10],
        "message": _strip_commit_trailers(raw_message),
        "author": (c.get("author") or {}).get("login") or (info.get("author") or {}).get("name") or "",
        "author_is_account": bool((c.get("author") or {}).get("login")),
        "co_authors": co_authors,
    }


def fetch_recent_commits(owner, repo, limit=50, tag_by_sha=None, timeout=20, github_token=None):
    """列出repo預設分支最近的commit(由新到舊),給「更新歷程記錄」顯示所有修改用,
    不只是有建立Release的版本。回傳 (commits, error),commits格式同fetch_commits_between。"""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
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
    return [_commit_entry(c, tag_by_sha or {}) for c in data], None


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
        if not is_eligible_path(filename, cfg):
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


# ---------------------------------------------------------------------------
# 版本校驗:比對本地端程式碼,跟GitHub上「同一個版本」的內容是否一致
# ---------------------------------------------------------------------------
# 掃描本地端「多出來的檔案」時要略過的目錄:套件目錄、快取目錄,以及所有「.」開頭的隱藏目錄
# (例如更新功能自己存放備份的.update_backups,裡面的檔案不是目前執行中的程式碼)
VERIFY_SKIP_DIR_NAMES = {"venv", "node_modules", "__pycache__", "app_image"}


def git_blob_sha1(data):
    """計算跟git完全相同的檔案雜湊值(git blob SHA-1):sha1("blob <位元組數>\0" + 內容)。
    GitHub的Trees API會直接給每個檔案的這個值,所以只要在本地端用同樣的算法算一次,
    就能判斷內容是否一致,不需要把每個檔案的內容都下載回來比對。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def fetch_tag_tree_entries(owner, repo, tag, cfg, timeout=20, github_token=None):
    """用GitHub的Git Trees API(recursive,只需要1次API呼叫),取得這個版本標籤底下、
    repo_subfolder裡面符合副檔名條件的所有檔案,以及每個檔案的git blob SHA。

    回傳 (entries, truncated, error)。entries是list of {"path": 去除repo_subfolder前綴的相對路徑,
    "sha": blob SHA};truncated表示GitHub因為檔案太多把清單截斷了(這個專案的檔案數量遠低於
    門檻,理論上不會發生,但還是要讓使用者知道結果不完整,不要悄悄漏掉檔案)。
    """
    subfolder = cfg["repo_subfolder"].rstrip("/") + "/"
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(tag, safe='')}?recursive=1"
    try:
        resp = requests.get(url, headers=_github_headers(github_token), timeout=timeout)
    except requests.RequestException as e:
        return None, False, f"連線失敗: {e}"
    if resp.status_code == 404:
        return None, False, (
            f"GitHub 上找不到版本標籤「{tag}」,沒辦法校驗。"
            f"請確認目前記錄的版本號,跟 GitHub 上的標籤名稱是否一致"
        )
    if resp.status_code != 200:
        return None, False, _friendly_error_from_response(resp)
    try:
        data = resp.json()
    except Exception:
        return None, False, "GitHub API回應格式無法解析"

    entries = []
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue  # 只要檔案,不要目錄
        path = item.get("path", "")
        if not path.startswith(subfolder) or not is_eligible_path(path, cfg):
            continue
        entries.append({"path": path[len(subfolder):], "sha": item.get("sha", "")})
    return entries, bool(data.get("truncated")), None


def scan_local_eligible_files(cfg):
    """掃描本地端各個部署目錄(path_map),找出符合副檔名條件的檔案。
    回傳 {"nanomdm-webui/app.py": "/opt/nanomdm-webui/app.py", ...},key的格式跟
    GitHub那邊(去除repo_subfolder前綴之後)的相對路徑一致,方便兩邊對照。"""
    found = {}
    for repo_prefix, local_root in cfg["path_map"].items():
        if not os.path.isdir(local_root):
            continue
        prefix = repo_prefix.rstrip("/")
        for dirpath, dirnames, filenames in os.walk(local_root):
            dirnames[:] = [d for d in dirnames if d not in VERIFY_SKIP_DIR_NAMES and not d.startswith(".")]
            for filename in filenames:
                if not is_eligible_path(filename, cfg):
                    continue
                full_path = os.path.join(dirpath, filename)
                rel = os.path.relpath(full_path, local_root).replace(os.sep, "/")
                found[f"{prefix}/{rel}"] = full_path
    return found


def _classify_difference(local_bytes, repo_sha):
    """本地檔案的雜湊值跟GitHub不同時,進一步判斷是「只有換行符號(CRLF/LF)不同」,
    還是「內容真的不同」。兩個方向都要試:本地被轉成LF、而GitHub存的是CRLF,
    或是相反。網頁介面執行.sh之前會自動把CRLF轉成LF,所以.sh檔案很容易出現這種情況。"""
    as_lf = local_bytes.replace(b"\r\n", b"\n")
    as_crlf = as_lf.replace(b"\n", b"\r\n")
    if git_blob_sha1(as_lf) == repo_sha or git_blob_sha1(as_crlf) == repo_sha:
        return "line_endings_only"
    return "modified"


def fetch_file_last_commit_time(owner, repo, tag, full_repo_path, timeout=20, github_token=None):
    """取得這個檔案在這個版本標籤底下,最後一次被修改的commit時間。
    回傳 (datetime, error),datetime帶時區。"""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
    params = {"sha": tag, "path": full_repo_path, "per_page": 1}
    try:
        resp = requests.get(url, headers=_github_headers(github_token), params=params, timeout=timeout)
    except requests.RequestException as e:
        return None, f"連線失敗: {e}"
    if resp.status_code != 200:
        return None, _friendly_error_from_response(resp)
    try:
        commits = resp.json()
        date_text = commits[0]["commit"]["committer"]["date"]
        return datetime.datetime.fromisoformat(date_text.replace("Z", "+00:00")), None
    except (ValueError, KeyError, IndexError, TypeError):
        return None, "GitHub API回應格式無法解析"


def _annotate_modified_freshness(owner, repo, tag, cfg, files, summary, timeout=20, github_token=None):
    """替「內容不同」的檔案判斷哪一邊比較新:比較本地檔案的修改時間,跟這個檔案在GitHub這個版本
    最後一次commit的時間。本地較新視為開發中的檔案,GitHub較新視為待更新的檔案。
    每個檔案需要1次API呼叫,所以只查內容不同的檔案;一旦查詢失敗(例如撞到速率限制)就不再繼續查,
    剩下的檔案標示為無法判斷,不影響校驗結果本身。"""
    subfolder = cfg["repo_subfolder"].rstrip("/") + "/"
    fetch_err = None
    for f in files:
        if f["status"] != "modified":
            continue
        github_time = None
        if not fetch_err:
            github_time, fetch_err = fetch_file_last_commit_time(
                owner, repo, tag, subfolder + f["repo_path"], timeout=timeout, github_token=github_token,
            )
        try:
            local_time = datetime.datetime.fromtimestamp(os.path.getmtime(f["local_path"])).astimezone()
        except OSError as e:
            local_time = None
            f["freshness_error"] = str(e)
        if github_time is None or local_time is None:
            f["freshness"] = "unknown"
            if fetch_err:
                f["freshness_error"] = fetch_err
            summary["freshness_unknown"] += 1
            continue
        f["freshness"] = "local_newer" if local_time > github_time else "github_newer"
        f["local_time"] = local_time.strftime("%Y-%m-%d %H:%M")
        f["github_time"] = github_time.astimezone().strftime("%Y-%m-%d %H:%M")
        summary[f["freshness"]] += 1


VERIFY_STATUS_ORDER = {"modified": 0, "line_endings_only": 1, "missing_local": 2, "extra_local": 3, "error": 4}


def verify_version_files(owner, repo, tag, cfg, timeout=20, github_token=None):
    """比對本地端檔案,跟GitHub上「同一個版本標籤」(tag)的內容,列出不一致的地方。

    做法:取得GitHub這個版本每個檔案的git blob SHA(1次API呼叫),在本地端用同樣算法算出雜湊值
    直接比對,不用下載任何檔案內容。只有「內容不同」的檔案,才需要另外下載內容來顯示逐行差異
    (見build_verify_diff,使用者展開那個檔案時才會去抓),以及每個檔案多1次API呼叫判斷哪一邊
    比較新(見_annotate_modified_freshness)。

    每個檔案的狀態:
      modified           內容不同
      line_endings_only  內容相同,只有換行符號(CRLF/LF)不同
      missing_local      GitHub上有,本地端找不到
      extra_local        本地端有,GitHub這個版本上沒有(手動新增、舊版殘留等)
      error              本地檔案讀不出來
    內容完全相同的檔案不列入清單,只計入summary的identical。
    GitHub上有、但沒有對應本地部署位置(不在path_map裡)的檔案不比對,跟更新功能的範圍一致。

    回傳 (result_dict, error)。
    """
    entries, truncated, err = fetch_tag_tree_entries(owner, repo, tag, cfg, timeout=timeout, github_token=github_token)
    if entries is None:
        return None, err

    summary = {"compared": 0, "identical": 0, "modified": 0, "line_endings_only": 0,
               "missing_local": 0, "extra_local": 0, "error": 0,
               "local_newer": 0, "github_newer": 0, "freshness_unknown": 0}
    files = []
    repo_paths = set()

    for entry in entries:
        path = entry["path"]
        local_path = map_repo_path_to_local(path, cfg)
        if not local_path:
            continue
        repo_paths.add(path)
        summary["compared"] += 1

        if not os.path.isfile(local_path):
            files.append({"repo_path": path, "local_path": local_path, "status": "missing_local"})
            summary["missing_local"] += 1
            continue
        try:
            with open(local_path, "rb") as f:
                local_bytes = f.read()
        except OSError as e:
            files.append({"repo_path": path, "local_path": local_path, "status": "error", "error": str(e)})
            summary["error"] += 1
            continue

        if git_blob_sha1(local_bytes) == entry["sha"]:
            summary["identical"] += 1
            continue
        status = _classify_difference(local_bytes, entry["sha"])
        summary[status] += 1
        files.append({"repo_path": path, "local_path": local_path, "status": status})

    for rel_path, local_path in scan_local_eligible_files(cfg).items():
        if rel_path not in repo_paths:
            files.append({"repo_path": rel_path, "local_path": local_path, "status": "extra_local"})
            summary["extra_local"] += 1

    _annotate_modified_freshness(owner, repo, tag, cfg, files, summary, timeout=timeout, github_token=github_token)

    files.sort(key=lambda x: (VERIFY_STATUS_ORDER.get(x["status"], 99), x["repo_path"]))
    return {
        "tag": tag, "summary": summary, "files": files, "truncated": truncated,
        "extensions": list(cfg["eligible_extensions"]),
    }, None


VERIFY_DIFF_MAX_LINES = 1500


def resolve_safe_local_path(repo_path, cfg):
    """檢查前端傳來的相對路徑,回傳 (正規化後的相對路徑, 本地路徑, error)。
    拒絕「..」、絕對路徑、不在部署目錄(path_map)底下的路徑、副檔名不在比對範圍內的檔案,
    而且用 realpath 確認實際位置沒有透過符號連結跑到部署目錄外面(檔案還不存在時檢查它的上層目錄)。"""
    normalized = (repo_path or "").replace("\\", "/")
    if (not normalized or normalized.startswith("/") or ".." in normalized.split("/")
            or not is_eligible_path(normalized, cfg)):
        return None, None, "不合法的檔案路徑"
    local_path = map_repo_path_to_local(normalized, cfg)
    if not local_path:
        return None, None, "這個檔案不在部署目錄範圍內"
    allowed_root = None
    for repo_prefix, local_root in cfg["path_map"].items():
        if normalized.startswith(repo_prefix.rstrip("/") + "/"):
            allowed_root = os.path.realpath(local_root)
            break
    real_local = os.path.realpath(local_path)
    if allowed_root is None or not (real_local == allowed_root or real_local.startswith(allowed_root + os.sep)):
        return None, None, "這個檔案的實際位置不在部署目錄範圍內"
    return normalized, local_path, None


def build_verify_diff(owner, repo, tag, repo_path, cfg, timeout=20, github_token=None):
    """產生單一檔案「GitHub同版本 vs 本地端」的逐行差異(使用者在校驗結果裡展開那個檔案時才呼叫)。
    diff的方向:「-」開頭的行只存在GitHub、「+」開頭的行只存在本地端。
    比對前兩邊都先把CRLF統一成LF,這樣顯示的才會是「真正的內容差異」,不會因為換行符號不同,
    整份檔案的每一行都被標示成不同。

    repo_path是前端傳來的,不能直接信任:要拒絕「..」、絕對路徑、不在部署目錄(path_map)底下的路徑、
    副檔名不在比對範圍內的檔案,而且用realpath確認實際位置沒有透過符號連結跑到部署目錄外面。
    回傳 (result_dict, error),result_dict是 {"patch": 差異文字或None, "note": 說明文字}。
    """
    normalized, local_path, err = resolve_safe_local_path(repo_path, cfg)
    if err:
        return None, err

    if not os.path.isfile(local_path):
        return None, "本地端沒有這個檔案"
    try:
        with open(local_path, "rb") as f:
            local_bytes = f.read()
    except OSError as e:
        return None, f"讀取本地檔案失敗: {e}"

    github_bytes, fetch_err = fetch_raw_file_content(
        owner, repo, tag, cfg["repo_subfolder"].rstrip("/") + "/" + normalized,
        timeout=timeout, github_token=github_token,
    )
    if github_bytes is None:
        return None, f"無法取得 GitHub {tag} 上的檔案內容: {fetch_err}"

    try:
        github_text = github_bytes.decode("utf-8")
        local_text = local_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {"patch": None, "note": "這個檔案不是文字檔,無法顯示逐行差異"}, None

    github_text = github_text.replace("\r\n", "\n")
    local_text = local_text.replace("\r\n", "\n")
    if github_text == local_text:
        return {"patch": None, "note": "內容相同,只有換行符號(CRLF/LF)不同"}, None

    diff_lines = list(difflib.unified_diff(
        github_text.splitlines(keepends=True), local_text.splitlines(keepends=True),
        fromfile=f"GitHub {tag}", tofile="本地端", n=3,
    ))
    note = ""
    if len(diff_lines) > VERIFY_DIFF_MAX_LINES:
        diff_lines = diff_lines[:VERIFY_DIFF_MAX_LINES]
        note = f"差異內容太長,只顯示前 {VERIFY_DIFF_MAX_LINES} 行"
    return {"patch": "".join(diff_lines), "note": note}, None


# ---------------------------------------------------------------------------
# 版本校驗補檔:從 GitHub 下載指定版本的檔案,補上本地缺少或內容不同的檔案
# ---------------------------------------------------------------------------
REPAIR_MAX_FILES = 200


def get_incomplete_update(backup_dir, current_version):
    """最近一次透過這個頁面的更新如果沒有完整套用(有檔案下載或寫入失敗),回傳那次更新的資訊。
    這種情況下目前版本還停在舊版,但部分檔案已經是新版,用「目前版本」校驗看不出缺了哪些新版檔案,
    要改用那次更新的目標版本校驗。目前版本已經等於目標版本(例如已經補檔完成)就不算未完成。
    回傳 None 或 {"folder", "from_version", "to_version", "applied_at", "failed": [repo_path, ...]}。"""
    updates = [h for h in list_update_history(backup_dir) if h["kind"] == "update" and not h.get("rolled_back_at")]
    if not updates:
        return None
    latest = updates[0]
    manifest = _read_manifest(os.path.join(backup_dir, latest["folder_name"]))
    if not manifest or manifest.get("complete", True):
        return None
    if manifest.get("to_version") == current_version:
        return None
    return {
        "folder": latest["folder_name"],
        "from_version": manifest.get("from_version"),
        "to_version": manifest.get("to_version"),
        "applied_at": manifest.get("applied_at") or latest["timestamp"],
        "failed": [e["repo_path"] for e in manifest.get("files") or [] if not e.get("ok")],
    }


def repair_files(owner, repo, tag, repo_paths, cfg, backup_dir, github_token=None, timeout=20):
    """從 GitHub 的 tag 版本下載 repo_paths 這些檔案,補上本地缺少或內容不同的檔案。

    - 每個路徑都重新檢查一次(不信任前端):必須在部署目錄範圍內、GitHub 這個版本上要有這個檔案
    - 本地內容已經跟 GitHub 相同的檔案略過,不重寫
    - 下載後先比對 git blob SHA,內容不完整(例如下載中斷)就不寫入
    - 覆蓋既有檔案前先備份到 .update_backups/{時間}_repair_{tag}/,並寫 manifest.json
      (這個目錄名稱不會出現在更新歷程,也不會影響退版判斷)
    - 保留原本的檔案權限,.sh 一律補上執行權限

    回傳 (info, error):info = {"results": [...], "changed_local_paths": [...], "backup_dir", "skipped": [...]}
    results 每個元素 {"repo_path", "local_path", "action": "created"/"replaced", "ok", "error"}。"""
    if not repo_paths:
        return None, "沒有選擇任何檔案"
    if len(repo_paths) > REPAIR_MAX_FILES:
        return None, f"一次最多補 {REPAIR_MAX_FILES} 個檔案"
    entries, _, err = fetch_tag_tree_entries(owner, repo, tag, cfg, timeout=timeout, github_token=github_token)
    if entries is None:
        return None, err
    sha_by_path = {e["path"]: e["sha"] for e in entries}

    checked = []
    for repo_path in dict.fromkeys(repo_paths):
        normalized, local_path, path_err = resolve_safe_local_path(repo_path, cfg)
        if path_err:
            return None, f"{repo_path}: {path_err}"
        if normalized not in sha_by_path:
            return None, f"{normalized}: GitHub {tag} 上沒有這個檔案"
        checked.append((normalized, local_path, sha_by_path[normalized]))

    subfolder = cfg["repo_subfolder"].rstrip("/") + "/"
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    this_backup_dir = os.path.join(backup_dir, f"{timestamp}_repair_{tag}")
    os.makedirs(this_backup_dir, exist_ok=True)
    manifest = {"kind": "repair", "tag": tag,
                "applied_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "files": []}

    results, changed, skipped = [], [], []
    for repo_path, local_path, sha in checked:
        existed = os.path.isfile(local_path)
        if existed:
            try:
                with open(local_path, "rb") as f:
                    if git_blob_sha1(f.read()) == sha:
                        skipped.append(repo_path)
                        continue
            except OSError:
                pass
        result = {"repo_path": repo_path, "local_path": local_path,
                  "action": "replaced" if existed else "created", "ok": False, "error": None}
        entry = {"repo_path": repo_path, "local_path": local_path, "action": result["action"],
                 "backup": None, "ok": False}
        manifest["files"].append(entry)
        try:
            content, fetch_err = fetch_raw_file_content(owner, repo, tag, subfolder + repo_path,
                                                        timeout=timeout, github_token=github_token)
            if content is None:
                result["error"] = fetch_err
            elif git_blob_sha1(content) != sha:
                result["error"] = "下載的內容跟 GitHub 記錄的不一致(可能下載不完整),沒有寫入"
            else:
                old_mode = None
                if existed:
                    old_mode = os.stat(local_path).st_mode
                    backup_name = repo_path.replace("/", "__")
                    shutil.copy2(local_path, os.path.join(this_backup_dir, backup_name))
                    entry["backup"] = backup_name
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                tmp_path = local_path + ".repair_tmp"
                with open(tmp_path, "wb") as out_f:
                    out_f.write(content)
                os.replace(tmp_path, local_path)
                if old_mode is not None:
                    os.chmod(local_path, old_mode)
                if local_path.endswith(".sh"):
                    os.chmod(local_path, os.stat(local_path).st_mode | 0o111)
                result["ok"] = True
                changed.append(local_path)
        except Exception as e:
            result["error"] = str(e)
        entry["ok"] = result["ok"]
        results.append(result)

    _write_manifest(this_backup_dir, manifest)
    if not manifest["files"]:
        shutil.rmtree(this_backup_dir, ignore_errors=True)
        this_backup_dir = None
    return {"results": results, "changed_local_paths": changed, "backup_dir": this_backup_dir,
            "skipped": skipped}, None


UPDATE_MANIFEST_NAME = "manifest.json"
NO_ROLLBACK_MARKER = "【無法退版】"


def _read_manifest(folder_path):
    try:
        with open(os.path.join(folder_path, UPDATE_MANIFEST_NAME), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_manifest(folder_path, manifest):
    tmp = os.path.join(folder_path, UPDATE_MANIFEST_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, os.path.join(folder_path, UPDATE_MANIFEST_NAME))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def list_update_history(backup_dir):
    """掃描更新備份目錄底下的子目錄,解析出過去每一次更新/退版的時間跟版本,
    給[版本與更新]頁面的「更新歷程記錄」使用。

    子目錄命名:
      更新:「{YYYYMMDD_HHMMSS}_to_{target_tag}」(apply_version_update() 建立)
      退版:「{YYYYMMDD_HHMMSS}_rollback_{from_tag}_to_{target_tag}」(rollback_update() 建立)
    有 manifest.json 的(退版功能加入之後的更新)會多帶 from_version / rolled_back_at。

    回傳 list,每個元素是 {"timestamp", "target_version", "folder_name", "kind", "from_version", "rolled_back_at"},
    依時間新到舊排序。目錄不存在或解析失敗的項目會被跳過,不會讓整個函式出錯。
    """
    if not os.path.exists(backup_dir):
        return []

    history = []
    for folder_name in os.listdir(backup_dir):
        full_path = os.path.join(backup_dir, folder_name)
        if not os.path.isdir(full_path):
            continue
        match = re.match(r"^(\d{8}_\d{6})_(?:rollback_(.+?)_)?to_(.+)$", folder_name)
        if not match:
            continue
        timestamp_raw, rollback_from, target_version = match.groups()
        try:
            dt = datetime.datetime.strptime(timestamp_raw, "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        manifest = _read_manifest(full_path) or {}
        history.append({
            "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "target_version": target_version,
            "folder_name": folder_name,
            "kind": "rollback" if rollback_from else "update",
            "from_version": rollback_from or manifest.get("from_version"),
            "rolled_back_at": manifest.get("rolled_back_at"),
        })

    history.sort(key=lambda h: h["timestamp"], reverse=True)
    return history


def is_managed_compose_file(path, cfg):
    """專案自己管理的 docker-compose 設定檔(例如 docker-compose.woofweb.yml):
    副檔名雖然不在 eligible_extensions 裡,但一樣透過更新機制更新,更新後由 compose_manager.py 重新套用。"""
    return path.replace("\\", "/").rsplit("/", 1)[-1] in cfg.get("managed_compose_files", [])


def is_eligible_path(path, cfg):
    """這個檔案是否在更新/校驗範圍內:副檔名符合 eligible_extensions,或是專案管理的 compose 設定檔。"""
    return path.lower().endswith(tuple(cfg["eligible_extensions"])) or is_managed_compose_file(path, cfg)


def _is_manual_change_file(relative_path, cfg):
    """更新機制不會處理、需要管理者手動處理的檔案(例如 docker-compose.yml、資料庫結構)"""
    name = relative_path.rsplit("/", 1)[-1]
    if is_managed_compose_file(name, cfg):
        return False
    return any(fnmatch.fnmatch(name, pat) for pat in cfg.get("manual_change_patterns", []))


def analyze_update_risk(owner, repo, base_tag, head_tag, cfg, timeout=20, github_token=None):
    """判斷從 base_tag 更新到 head_tag 之後還能不能退版,需不需要提醒管理者先自行備份系統。
    兩種情況要提醒:
      1. 更新範圍內有更新機制不會處理的檔案(manual_change_patterns,例如 docker-compose.yml、.sql),
         代表這次更新還需要手動調整,單純退回程式檔案無法復原
      2. commit 說明裡有「【無法退版】原因」這一行(發布時手動標記)
    回傳 (risk, error):risk = {"requires_backup", "manual_files", "no_rollback_reasons"}。
    """
    subfolder = cfg["repo_subfolder"].rstrip("/") + "/"
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/compare/{base_tag}...{head_tag}"
    try:
        resp = requests.get(url, headers=_github_headers(github_token), timeout=timeout)
    except requests.RequestException as e:
        return None, f"連線失敗: {e}"
    if resp.status_code != 200:
        return None, _friendly_error_from_response(resp)
    try:
        data = resp.json()
    except Exception:
        return None, "GitHub API回應格式無法解析"

    manual_files, compose_files = [], []
    for f in data.get("files") or []:
        filename = f.get("filename", "")
        if not filename.startswith(subfolder):
            continue
        relative = filename[len(subfolder):]
        if _is_manual_change_file(relative, cfg):
            manual_files.append(relative)
        elif is_managed_compose_file(relative, cfg):
            compose_files.append(relative)

    reasons = []
    for c in data.get("commits") or []:
        message = (c.get("commit") or {}).get("message") or ""
        title = message.strip().splitlines()[0] if message.strip() else ""
        for line in message.splitlines():
            if NO_ROLLBACK_MARKER in line:
                reason = line.split(NO_ROLLBACK_MARKER, 1)[1].strip(" :：") or "(未註明原因)"
                reasons.append({"sha": (c.get("sha") or "")[:7], "title": title, "reason": reason})

    return {
        "requires_backup": bool(manual_files or reasons),
        "manual_files": manual_files,
        "no_rollback_reasons": reasons,
        # 專案管理的 compose 設定檔有變動:更新後要重新套用 docker compose(會重建受影響的容器)
        "compose_files": compose_files,
    }, None


def apply_version_update(owner, repo, target_tag, files, cfg, backup_dir, github_token=None,
                         from_version=None, rollback_block_reasons=None):
    """實際套用更新:對每個檔案,先備份現有內容(如果存在),再寫入新版本的內容。
    files是compare_versions()回傳的清單格式。
    對於status="removed"的檔案(新版本裡已經被刪除),本地也會跟著刪除(先備份)。

    同時在備份目錄寫入 manifest.json(退版用):從哪一版更新到哪一版、每個檔案是新增/修改/刪除、
    更新後的內容雜湊值(之後用來判斷有沒有被手動改過)、以及這次更新不能退版的原因(如果有)。

    回傳 (results, overall_ok, this_backup_dir)。results是每個檔案的結果清單:
      {"repo_path", "local_path", "ok", "error"}
    """
    subfolder = cfg["repo_subfolder"].rstrip("/") + "/"
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    this_backup_dir = os.path.join(backup_dir, f"{timestamp}_to_{target_tag}")
    os.makedirs(this_backup_dir, exist_ok=True)

    manifest = {
        "kind": "update",
        "from_version": from_version,
        "to_version": target_tag,
        "applied_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "complete": False,
        "rollback_block_reasons": rollback_block_reasons or [],
        "rolled_back_at": None,
        "files": [],
    }
    _write_manifest(this_backup_dir, manifest)

    results = []
    overall_ok = True
    for f in files:
        repo_path = f["repo_path"]
        local_path = f["local_path"]
        status = f["status"]
        result = {"repo_path": repo_path, "local_path": local_path, "ok": False, "error": None}
        backup_name = repo_path.replace("/", "__")
        existed = os.path.exists(local_path)
        entry = {"repo_path": repo_path, "local_path": local_path,
                 "action": "removed" if status == "removed" else ("modified" if existed else "created"),
                 "backup": backup_name if existed else None, "sha256_after": None, "ok": False}
        manifest["files"].append(entry)

        try:
            old_mode = None
            if existed:
                old_mode = os.stat(local_path).st_mode  # 更新前先記住舊檔案的權限設定,寫入新內容後要套用回去
                backup_target = os.path.join(this_backup_dir, backup_name)
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
                entry["sha256_after"] = _sha256_file(local_path)
        except Exception as e:
            result["error"] = str(e)
            overall_ok = False

        entry["ok"] = result["ok"]
        results.append(result)

    manifest["complete"] = overall_ok
    _write_manifest(this_backup_dir, manifest)
    return results, overall_ok, this_backup_dir


def get_rollback_candidate(backup_dir, current_version):
    """找出「現在可以退回的那一次更新」並檢查能不能退。一次只退一版:
    取最近一次還沒被退版的更新,而且它的目標版本必須等於目前版本。

    回傳 dict:
      available: 能不能退版;reason: 不能退的原因
      folder, from_version, to_version, applied_at
      restore: 要還原的檔案(修改/刪除過的);delete: 要刪除的檔案(那次新增的)
      modified_since: 更新後被手動改過的檔案(可以退,但要提醒)
    """
    info = {"available": False, "reason": None, "folder": None, "from_version": None, "to_version": None,
            "applied_at": None, "restore": [], "delete": [], "modified_since": []}
    updates = [h for h in list_update_history(backup_dir) if h["kind"] == "update" and not h.get("rolled_back_at")]
    if not updates:
        info["reason"] = "目前沒有可以退版的更新紀錄(只有透過這個頁面套用的更新才能退版)"
        return info

    latest = updates[0]
    folder_path = os.path.join(backup_dir, latest["folder_name"])
    manifest = _read_manifest(folder_path)
    info.update(folder=latest["folder_name"], to_version=latest["target_version"], applied_at=latest["timestamp"])
    if not manifest:
        info["reason"] = (f"最近一次更新(到 {latest['target_version']})是在退版功能加入之前執行的,"
                          "沒有完整的更新紀錄,無法退版")
        return info
    info["from_version"] = manifest.get("from_version")
    if not info["from_version"]:
        info["reason"] = "更新紀錄裡沒有原本的版本號,無法退版"
        return info
    if manifest.get("to_version") != current_version:
        info["reason"] = (f"目前版本({current_version or '未知'})跟最近一次更新的目標版本({manifest.get('to_version')})不同,"
                          "可能有手動修改過版本,為了安全不提供退版")
        return info
    blocks = manifest.get("rollback_block_reasons") or []
    if blocks:
        info["reason"] = "這次更新被標記為無法退版:" + ";".join(blocks)
        return info

    missing = []
    for entry in manifest.get("files") or []:
        local_path = entry["local_path"]
        if entry["action"] in ("modified", "removed"):
            if not entry.get("backup") or not os.path.exists(os.path.join(folder_path, entry["backup"])):
                missing.append(entry["repo_path"])
                continue
            info["restore"].append(entry["repo_path"])
        elif entry["action"] == "created":
            info["delete"].append(entry["repo_path"])
        # 更新後被手動改過(內容雜湊值跟更新當下不同)
        if entry["action"] != "removed" and entry.get("sha256_after") and os.path.exists(local_path):
            if _sha256_file(local_path) != entry["sha256_after"]:
                info["modified_since"].append(entry["repo_path"])
    if missing:
        info["reason"] = "備份檔案不完整,無法退版(缺少:" + "、".join(missing[:5]) + ("…" if len(missing) > 5 else "") + ")"
        return info

    info["available"] = True
    return info


def rollback_update(backup_dir, folder_name, current_version):
    """退回 folder_name 這次更新之前的版本:還原修改/刪除過的檔案、刪除那次新增的檔案。
    動手前先把目前的檔案備份到「{時間}_rollback_{目前版本}_to_{原版本}」目錄,退錯了還能再更新回來。
    資料庫、.env、docker-compose.yml、webui_config.json 不在退版範圍內。

    回傳 (ok, error, info):info = {"from_version", "changed_local_paths", "results", "backup_dir"}
    """
    candidate = get_rollback_candidate(backup_dir, current_version)
    if not candidate["available"]:
        return False, candidate["reason"], None
    if candidate["folder"] != folder_name:
        return False, "可以退版的更新已經改變,請重新整理頁面後再試一次", None

    folder_path = os.path.join(backup_dir, folder_name)
    manifest = _read_manifest(folder_path)
    target_version = manifest["from_version"]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    this_backup_dir = os.path.join(backup_dir, f"{timestamp}_rollback_{current_version}_to_{target_version}")
    os.makedirs(this_backup_dir, exist_ok=True)

    rollback_manifest = {"kind": "rollback", "from_version": current_version, "to_version": target_version,
                         "applied_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         "rolled_back_update": folder_name, "files": []}
    results, changed, ok = [], [], True
    for entry in manifest.get("files") or []:
        local_path = entry["local_path"]
        result = {"repo_path": entry["repo_path"], "local_path": local_path, "ok": False, "error": None}
        try:
            if os.path.exists(local_path):
                backup_name = entry["repo_path"].replace("/", "__")
                shutil.copy2(local_path, os.path.join(this_backup_dir, backup_name))
                rollback_manifest["files"].append({"repo_path": entry["repo_path"], "local_path": local_path,
                                                   "backup": backup_name})
            if entry["action"] == "created":
                if os.path.exists(local_path):
                    os.remove(local_path)
            else:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                tmp_path = local_path + ".rollback_tmp"
                shutil.copy2(os.path.join(folder_path, entry["backup"]), tmp_path)
                os.replace(tmp_path, local_path)
            result["ok"] = True
            changed.append(local_path)
        except Exception as e:
            result["error"] = str(e)
            ok = False
        results.append(result)

    _write_manifest(this_backup_dir, rollback_manifest)
    if ok:
        manifest["rolled_back_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _write_manifest(folder_path, manifest)
    return ok, None if ok else "部分檔案還原失敗", {
        "from_version": target_version, "changed_local_paths": changed,
        "results": results, "backup_dir": this_backup_dir,
    }


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
