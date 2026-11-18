"""「版本與更新」頁的「作業系統更新」區塊:檢查 Debian 套件更新、是否需要重新開機,以及切換 apt 的鏡像站。

只做檢查,不安裝任何套件:
- 檢查更新 = apt-get update(更新套件清單)+ apt-get -s dist-upgrade(模擬,不會真的安裝或移除)
- 切換鏡像站只改 Debian 主要套件庫(main 的 <代號>、<代號>-updates、<代號>-backports)的網址,
  security.debian.org 與 Docker 等其他套件庫不動。改之前備份原檔,改完後 apt-get update 失敗就自動還原。

apt 指令在背景執行緒跑,同一時間只允許一個工作;頁面每 2 秒查詢一次進度。
"""
import concurrent.futures
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time

import requests

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".os_update")
LAST_CHECK_FILE = os.path.join(STATE_DIR, "last_check.json")
BACKUP_DIR = os.path.join(STATE_DIR, "sources_backups")
BACKUP_KEEP = 10

APT_ETC = "/etc/apt"
OS_RELEASE = "/etc/os-release"
REBOOT_REQUIRED = "/var/run/reboot-required"

# 台灣常用的 Debian 鏡像站
MIRRORS = [
    {"id": "debian-cdn", "name": "Debian 官方 CDN", "url": "http://deb.debian.org/debian",
     "note": "全球 CDN,會自動導向較近的節點"},
    {"id": "debian-tw", "name": "Debian 官方台灣鏡像", "url": "http://ftp.tw.debian.org/debian",
     "note": "由國網中心維護,Debian 安裝程式選「台灣」的預設值"},
    {"id": "nchc", "name": "國網中心 (NCHC)", "url": "http://free.nchc.org.tw/debian", "note": ""},
    {"id": "ntu", "name": "臺灣大學資工系", "url": "http://debian.csie.ntu.edu.tw/debian", "note": ""},
    {"id": "ncku", "name": "成功大學資工系", "url": "http://debian.csie.ncku.edu.tw/debian", "note": ""},
    {"id": "nycu", "name": "陽明交通大學資工系", "url": "http://debian.cs.nycu.edu.tw/debian", "note": ""},
    {"id": "twds", "name": "TWDS 台灣數位串流", "url": "http://mirror.twds.com.tw/debian", "note": ""},
]

DOCKER_PACKAGES = {"docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin",
                   "docker-buildx-plugin", "docker-ce-rootless-extras", "docker-model-plugin"}
KERNEL_PREFIXES = ("linux-image", "linux-headers", "linux-modules", "linux-kbuild")

APT_ENV = {"LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"}
APT_UPDATE_TIMEOUT = 600

_job_lock = threading.Lock()
_job = {"state": "idle"}   # idle / running / done / failed


# ---------------------------------------------------------------------------
# 基本資訊
# ---------------------------------------------------------------------------
def read_os_release():
    info = {}
    try:
        with open(OS_RELEASE, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep:
                    info[key] = value.strip().strip('"')
    except OSError:
        pass
    return info


def _norm_uri(uri):
    return (uri or "").strip().rstrip("/")


def _mirror_for_uri(uri):
    uri = _norm_uri(uri)
    for m in MIRRORS:
        if uri in (m["url"], m["url"].replace("http://", "https://", 1)):
            return m
    return None


def _natural_key(s):
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", s) if p]


# ---------------------------------------------------------------------------
# apt 套件來源(sources.list 單行格式與 deb822 .sources 格式)
# ---------------------------------------------------------------------------
def _is_archive_entry(uris, suites, components, codename):
    """Debian 主要套件庫:套件庫代號是 <代號>、<代號>-updates、-backports、-proposed-updates,
    而且包含 main。security 與 Docker(元件是 stable)等其他套件庫都不算。"""
    if not codename or "main" not in components:
        return False
    allowed = {codename, f"{codename}-updates", f"{codename}-backports", f"{codename}-proposed-updates"}
    if not suites or any(s.rstrip("/") not in allowed for s in suites):
        return False
    return all("security" not in u for u in uris)


def _source_files():
    files = []
    main = os.path.join(APT_ETC, "sources.list")
    if os.path.isfile(main):
        files.append(main)
    files += sorted(glob.glob(os.path.join(APT_ETC, "sources.list.d", "*.list")))
    files += sorted(glob.glob(os.path.join(APT_ETC, "sources.list.d", "*.sources")))
    return files


_ONE_LINE_RE = re.compile(r"^(\s*deb(?:-src)?\s+)(\[[^\]]*\]\s+)?(\S+)(\s+)(\S+)(.*)$")


def _scan_one_line(text, codename, new_uri=None):
    """回傳 (改寫後的文字, [符合條件的網址])。new_uri 為 None 時只掃描、不改寫。"""
    found = []
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = _ONE_LINE_RE.match(body)
        if m:
            uri, suite, rest = m.group(3), m.group(5), m.group(6)
            components = rest.split("#", 1)[0].split()
            if _is_archive_entry([uri], [suite], components, codename):
                found.append(_norm_uri(uri))
                if new_uri is not None:
                    new_body = m.group(1) + (m.group(2) or "") + new_uri + "/" + m.group(4) + suite + rest
                    line = new_body + line[len(body):]
        out.append(line)
    return "".join(out), found


def _scan_deb822(text, codename, new_uri=None):
    found = []
    out_stanzas = []
    for stanza in re.split(r"(\n\s*\n)", text):
        if not stanza.strip() or stanza.strip("\n").strip() == "":
            out_stanzas.append(stanza)
            continue
        fields = {}
        for line in stanza.splitlines():
            if line.startswith("#") or ":" not in line or line[:1].isspace():
                continue
            key, _, value = line.partition(":")
            fields[key.strip().lower()] = value.strip()
        enabled = fields.get("enabled", "yes").lower() not in ("no", "false", "0")
        uris = fields.get("uris", "").split()
        if (enabled and uris and _is_archive_entry(
                uris, fields.get("suites", "").split(), fields.get("components", "").split(), codename)):
            found += [_norm_uri(u) for u in uris]
            if new_uri is not None:
                stanza = re.sub(r"(?im)^(URIs:[ \t]*)\S.*$", lambda m: m.group(1) + new_uri + "/", stanza, count=1)
        out_stanzas.append(stanza)
    return "".join(out_stanzas), found


def _scan_file(path, codename, new_uri=None):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None, []
    if path.endswith(".sources"):
        return _scan_deb822(text, codename, new_uri)
    return _scan_one_line(text, codename, new_uri)


def get_sources_info():
    osr = read_os_release()
    codename = osr.get("VERSION_CODENAME", "")
    supported = osr.get("ID") == "debian" and bool(codename)
    files = []
    uris = []
    if supported:
        for path in _source_files():
            _, found = _scan_file(path, codename)
            if found:
                files.append(path)
                for u in found:
                    if u not in uris:
                        uris.append(u)
    current = _mirror_for_uri(uris[0]) if len(uris) == 1 else None
    return {
        "os_name": osr.get("PRETTY_NAME", ""),
        "codename": codename,
        "supported": supported,
        "unsupported_reason": "" if supported else "目前只支援 Debian",
        "files": files,
        "uris": uris,
        "current_mirror_id": current["id"] if current else "",
    }


# ---------------------------------------------------------------------------
# 需要重新開機
# ---------------------------------------------------------------------------
def get_reboot_info():
    reasons = []
    if os.path.exists(REBOOT_REQUIRED):
        pkgs = []
        try:
            with open(REBOOT_REQUIRED + ".pkgs", encoding="utf-8") as f:
                pkgs = sorted({line.strip() for line in f if line.strip()})
        except OSError:
            pass
        reasons.append("系統標記需要重新開機" + (f"(套件:{'、'.join(pkgs)})" if pkgs else ""))
    running = os.uname().release
    installed = [os.path.basename(p)[len("vmlinuz-"):] for p in glob.glob("/boot/vmlinuz-*")]
    newest = max(installed, key=_natural_key) if installed else ""
    if newest and newest != running and _natural_key(newest) > _natural_key(running):
        reasons.append(f"已安裝較新的核心 {newest},目前執行的是 {running}")
    return {"required": bool(reasons), "reasons": reasons, "running_kernel": running, "newest_kernel": newest}


# ---------------------------------------------------------------------------
# 檢查更新
# ---------------------------------------------------------------------------
def _run_apt(args, timeout):
    env = dict(os.environ)
    env.update(APT_ENV)
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, "", f"執行超過 {timeout} 秒,已停止"
    except Exception as e:
        return 1, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def _apt_update():
    """回傳 (成功與否, 錯誤/警告訊息清單)。"""
    rc, out, err = _run_apt(["apt-get", "update"], APT_UPDATE_TIMEOUT)
    problems = [line.strip() for line in (out + "\n" + err).splitlines()
                if line.startswith(("Err:", "E:", "W:"))]
    if rc != 0 and not problems:
        problems = [(err or out).strip() or f"apt-get update 失敗(代碼 {rc})"]
    return rc == 0 and not any(p.startswith(("Err:", "E:")) for p in problems), problems


_INST_RE = re.compile(r"^Inst (\S+) (?:\[([^\]]*)\] )?\((\S+) (.*) \[[^\]]*\]\)")
_REMV_RE = re.compile(r"^Remv (\S+)(?: \[([^\]]*)\])?")


def _simulate_upgrade():
    rc, out, err = _run_apt(["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"], 300)
    if rc != 0:
        return None, (err or out).strip() or f"模擬更新失敗(代碼 {rc})"
    packages, removals = [], []
    for line in out.splitlines():
        m = _INST_RE.match(line)
        if m:
            name, old, new, origin = m.groups()
            packages.append({
                "name": name, "old_version": old or "", "new_version": new, "origin": origin,
                "new_install": not old,
                "security": "security" in origin.lower(),
                "docker": name in DOCKER_PACKAGES or name.startswith("docker-"),
                "kernel": name.startswith(KERNEL_PREFIXES),
            })
            continue
        m = _REMV_RE.match(line)
        if m:
            removals.append({"name": m.group(1), "old_version": m.group(2) or ""})
    packages.sort(key=lambda p: (not p["security"], not p["kernel"], not p["docker"], p["name"]))
    return {"packages": packages, "removals": removals}, None


def _last_update_time():
    """套件清單最後一次更新的時間。apt-get update 會把 Release 檔的修改時間設成鏡像站上的時間,
    所以改用 ctime(apt 每次更新都會重設這個檔案的時間,ctime 就會變成當下)。"""
    stamps = glob.glob("/var/lib/apt/lists/*_InRelease") + glob.glob("/var/lib/apt/lists/*_Release")
    if not stamps:
        return ""
    t = max(os.path.getctime(p) for p in stamps)
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def _save_last_check(data):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = LAST_CHECK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LAST_CHECK_FILE)


def load_last_check():
    try:
        with open(LAST_CHECK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _do_check(progress):
    progress("正在更新套件清單(apt-get update)...")
    ok, problems = _apt_update()
    if not ok:
        return None, "更新套件清單失敗:" + ";".join(problems[:5])
    progress("正在模擬更新,計算可更新的套件...")
    result, err = _simulate_upgrade()
    if err:
        return None, err
    pkgs = result["packages"]
    data = {
        "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "packages": pkgs,
        "removals": result["removals"],
        "warnings": problems,
        "summary": {
            "total": len(pkgs),
            "security": sum(p["security"] for p in pkgs),
            "kernel": sum(p["kernel"] for p in pkgs),
            "docker": sum(p["docker"] for p in pkgs),
            "new_install": sum(p["new_install"] for p in pkgs),
            "removals": len(result["removals"]),
        },
    }
    _save_last_check(data)
    return data, None


# ---------------------------------------------------------------------------
# 切換鏡像站
# ---------------------------------------------------------------------------
def _backup_sources(paths):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, ts)
    os.makedirs(dest, mode=0o700, exist_ok=True)
    manifest = []
    for i, path in enumerate(paths):
        name = f"{i}_{os.path.basename(path)}"
        shutil.copy2(path, os.path.join(dest, name))
        manifest.append({"path": path, "backup": name})
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    backups = sorted(d for d in os.listdir(BACKUP_DIR) if os.path.isdir(os.path.join(BACKUP_DIR, d)))
    for old in backups[:-BACKUP_KEEP]:
        shutil.rmtree(os.path.join(BACKUP_DIR, old), ignore_errors=True)
    return dest, manifest


def _write_keep_mode(path, text):
    st = os.stat(path)
    tmp = path + ".woofweb-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, st.st_mode & 0o7777)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except OSError:
        pass
    os.replace(tmp, path)


def _restore_sources(dest, manifest):
    for item in manifest:
        shutil.copy2(os.path.join(dest, item["backup"]), item["path"])


def _do_switch(mirror, progress):
    info = get_sources_info()
    if not info["supported"]:
        return None, info["unsupported_reason"]
    if not info["files"]:
        return None, "找不到 Debian 主要套件庫的設定,無法切換"
    old_uris = info["uris"]
    new_uri = mirror["url"]

    progress("正在備份並修改套件來源設定...")
    dest, manifest = _backup_sources(info["files"])
    changed = []
    for path in info["files"]:
        new_text, found = _scan_file(path, info["codename"], new_uri)
        if found and new_text is not None:
            _write_keep_mode(path, new_text)
            changed.append(path)

    progress(f"正在從 {mirror['name']} 更新套件清單(apt-get update)...")
    ok, problems = _apt_update()
    if not ok:
        progress("新的鏡像站無法使用,正在還原原本的設定...")
        _restore_sources(dest, manifest)
        _apt_update()
        return None, (f"{mirror['name']} 無法使用,已還原成原本的設定({'、'.join(old_uris)})。錯誤:"
                      + ";".join(problems[:5]))

    progress("正在模擬更新,計算可更新的套件...")
    check, err = _simulate_upgrade()
    data = {"old_uris": old_uris, "new_uri": new_uri, "files": changed, "backup_dir": dest}
    if not err:
        pkgs = check["packages"]
        _save_last_check({
            "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "packages": pkgs, "removals": check["removals"], "warnings": problems,
            "summary": {
                "total": len(pkgs), "security": sum(p["security"] for p in pkgs),
                "kernel": sum(p["kernel"] for p in pkgs), "docker": sum(p["docker"] for p in pkgs),
                "new_install": sum(p["new_install"] for p in pkgs), "removals": len(check["removals"]),
            },
        })
    return data, None


def find_mirror(mirror_id):
    return next((m for m in MIRRORS if m["id"] == mirror_id), None)


# ---------------------------------------------------------------------------
# 背景工作
# ---------------------------------------------------------------------------
def get_job():
    with _job_lock:
        return dict(_job)


def start_job(kind, on_finish=None, mirror=None):
    """kind: "check" 或 "switch"。回傳 (是否已開始, 錯誤訊息)。on_finish(job) 在工作結束時呼叫(用來寫操作紀錄)。"""
    with _job_lock:
        if _job.get("state") == "running":
            return False, "已經有一個作業系統更新的工作正在執行,請稍候"
        _job.clear()
        _job.update({"state": "running", "kind": kind, "started_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "message": "準備中...", "mirror": mirror["name"] if mirror else ""})

    def progress(msg):
        with _job_lock:
            _job["message"] = msg

    def run():
        started = time.time()
        try:
            if kind == "switch":
                result, err = _do_switch(mirror, progress)
            else:
                result, err = _do_check(progress)
        except Exception as e:
            result, err = None, f"執行時發生錯誤:{e}"
        with _job_lock:
            _job.update({
                "state": "failed" if err else "done",
                "message": err or "完成",
                "result": result if kind == "switch" else None,
                "finished_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "seconds": round(time.time() - started),
            })
            snapshot = dict(_job)
        if on_finish:
            try:
                on_finish(snapshot)
            except Exception:
                pass

    threading.Thread(target=run, daemon=True, name=f"os-update-{kind}").start()
    return True, None


def get_status():
    return {
        "sources": get_sources_info(),
        "mirrors": MIRRORS,
        "reboot": get_reboot_info(),
        "last_check": load_last_check(),
        "lists_updated_at": _last_update_time(),
        "job": get_job(),
    }


# ---------------------------------------------------------------------------
# 鏡像站測速
# ---------------------------------------------------------------------------
def speed_test(codename, timeout=6):
    """對每個鏡像站下載一次 dists/<代號>/Release(約幾十 KB),回傳花費的毫秒數。"""
    def probe(m):
        url = f"{m['url']}/dists/{codename}/Release"
        start = time.time()
        try:
            r = requests.get(url, timeout=timeout)
            ms = round((time.time() - start) * 1000)
            if r.status_code != 200:
                return {"id": m["id"], "ok": False, "error": f"HTTP {r.status_code}"}
            return {"id": m["id"], "ok": True, "ms": ms}
        except requests.RequestException as e:
            return {"id": m["id"], "ok": False, "error": "連線逾時" if isinstance(e, requests.Timeout) else "無法連線"}

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(MIRRORS)) as pool:
        return list(pool.map(probe, MIRRORS))
