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

# 安裝更新的模式。一般更新會安裝新的相依套件(例如新版核心),但絕不移除套件
UPGRADE_MODES = {
    "upgrade": {"name": "一般更新", "apt_args": ["upgrade", "--with-new-pkgs"]},
    "full-upgrade": {"name": "完整更新(full-upgrade)", "apt_args": ["dist-upgrade"]},
}
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


def _parse_section(out, header_prefix):
    """apt 輸出裡「The following packages have been kept back:」這類段落下面縮排的套件名稱。"""
    names, inside = [], False
    for line in out.splitlines():
        if line.startswith(header_prefix):
            inside = True
            continue
        if inside:
            if line.startswith("  "):
                names += line.split()
            else:
                inside = False
    return names


def _simulate_upgrade(mode="full-upgrade", prefs_dir=None):
    args = ["apt-get", "-s", "-o", "Debug::NoLocking=1"]
    if prefs_dir:
        args += ["-o", f"Dir::Etc::PreferencesParts={prefs_dir}"]
    args += UPGRADE_MODES[mode]["apt_args"]
    rc, out, err = _run_apt(args, 300)
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
    kept_back = _parse_section(out, "The following packages have been kept back") + \
        _parse_section(out, "The following package has been kept back")
    return {"packages": packages, "removals": removals, "kept_back": kept_back}, None


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
    if get_running_upgrade():
        return False, "正在安裝更新,請等安裝完成後再試"
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


def get_status(log_fn=None):
    runs = list_upgrade_runs(log_fn)
    latest = runs[0] if runs else None
    if latest:
        latest = dict(latest)
        latest["log_tail"] = read_upgrade_log(latest["id"], tail_bytes=24000)
    return {
        "sources": get_sources_info(),
        "mirrors": MIRRORS,
        "reboot": get_reboot_info(),
        "last_check": load_last_check(),
        "lists_updated_at": _last_update_time(),
        "job": get_job(),
        "upgrade_modes": {k: v["name"] for k, v in UPGRADE_MODES.items()},
        "upgrade_runs": runs[:10],
        "upgrade_latest": latest,
        "upgrade_running": bool(latest and latest["state"] == "running"),
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


# ---------------------------------------------------------------------------
# 安裝更新(第二階段)
#
# 保護措施:
# - apt 用 systemd-run 跑成獨立的 systemd 服務,WebUI 重啟或斷線都不會中斷安裝
# - 不會出現問答畫面:DEBIAN_FRONTEND=noninteractive,設定檔一律保留舊的(--force-confold)
# - 預設是一般更新(upgrade --with-new-pkgs),不會移除任何套件;完整更新要另外選,並先列出會移除的套件
# - 可以選擇這次不更新 Docker 套件:用暫時的 apt 偏好設定把 Docker 套件固定在目前版本
# - 安裝前檢查:沒有其他 apt/dpkg 在執行、上次安裝沒有中斷、磁碟空間足夠,
#   並重新模擬一次,要安裝的套件跟使用者確認時看到的一樣才開始
# - 安裝完不會自動重新開機,重新開機是另外的按鈕
# ---------------------------------------------------------------------------
import fcntl
import hashlib
import shlex

UPGRADE_DIR = os.path.join(STATE_DIR, "upgrades")
UPGRADE_KEEP = 20
UNIT_PREFIX = "woofweb-apt-upgrade-"
APT_GET = "/usr/bin/apt-get"          # 測試時可以換成假的 apt-get
SYSTEMD_RUN = "systemd-run"
MIN_ROOT_FREE = 1024 ** 3             # / 至少要有 1GB
MIN_BOOT_FREE = 150 * 1024 ** 2       # 有核心更新時 /boot 至少要有 150MB
LISTS_STALE_DAYS = 3
_RUN_ID_RE = re.compile(r"^\d{8}_\d{6}$")
_finalize_lock = threading.Lock()


def _unit_name(run_id):
    return UNIT_PREFIX + run_id.replace("_", "-")


def _unit_active(unit):
    try:
        proc = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return proc.stdout.strip() in ("active", "activating", "reloading")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _prefs_dir_with_docker_pins(dest):
    """複製 /etc/apt/preferences.d 原有的設定,再加上把 Docker 套件固定在目前已安裝版本的設定。
    回傳被固定的套件清單。"""
    os.makedirs(dest, mode=0o700, exist_ok=True)
    src = os.path.join(APT_ETC, "preferences.d")
    if os.path.isdir(src):
        for name in os.listdir(src):
            path = os.path.join(src, name)
            if os.path.isfile(path):
                shutil.copy2(path, os.path.join(dest, name))
    pinned, lines = [], []
    for pkg in sorted(DOCKER_PACKAGES):
        try:
            proc = subprocess.run(["dpkg-query", "-W", "-f=${Status}\t${Version}", pkg],
                                  capture_output=True, text=True, timeout=15)
        except Exception:
            continue
        status, _, version = proc.stdout.partition("\t")
        if proc.returncode == 0 and status.endswith(" installed") and version:
            pinned.append(pkg)
            lines.append(f"Package: {pkg}\nPin: version {version}\nPin-Priority: 1001\n")
    with open(os.path.join(dest, "zz-woofweb-keep-docker.pref"), "w", encoding="utf-8") as f:
        f.write("# WoofWeb:這次安裝更新不更新 Docker 套件\n\n" + "\n".join(lines))
    return pinned


def _plan_hash(mode, exclude_docker, sim):
    key = {
        "mode": mode, "exclude_docker": bool(exclude_docker),
        "inst": sorted((p["name"], p["new_version"]) for p in sim["packages"]),
        "remv": sorted(r["name"] for r in sim["removals"]),
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def _simulate_for(mode, exclude_docker):
    if mode not in UPGRADE_MODES:
        return None, "更新模式不正確"
    if not exclude_docker:
        return _simulate_upgrade(mode)
    tmp = os.path.join(STATE_DIR, f"plan_prefs_{os.getpid()}_{threading.get_ident()}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _prefs_dir_with_docker_pins(tmp)
        return _simulate_upgrade(mode, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _apt_locked():
    """apt/dpkg 的鎖是否被其他程式(例如 unattended-upgrades、有人正在 SSH 執行 apt)佔用。"""
    for path in ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock"):
        try:
            fd = os.open(path, os.O_RDWR)
        except OSError:
            continue
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.lockf(fd, fcntl.LOCK_UN)
        except OSError:
            return True
        finally:
            os.close(fd)
    return False


def _dpkg_interrupted():
    """上次安裝有沒有中斷、還沒修好(需要 dpkg --configure -a)。回傳說明文字或空字串。"""
    updates = "/var/lib/dpkg/updates"
    try:
        if any(name.isdigit() for name in os.listdir(updates)):
            return "dpkg 有中斷的安裝紀錄"
    except OSError:
        pass
    try:
        proc = subprocess.run(["dpkg", "--audit"], capture_output=True, text=True, timeout=30)
        out = proc.stdout.strip()
    except Exception:
        out = ""
    return out[:600] if out else ""


def _free_bytes(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


def upgrade_prechecks(sim=None):
    """回傳 [{"name","level": ok/warn/fail,"message"}]。有 fail 就不能安裝。"""
    checks = []

    def add(name, level, message):
        checks.append({"name": name, "level": level, "message": message})

    info = get_sources_info()
    if not info["supported"]:
        add("系統", "fail", info["unsupported_reason"])
    if get_running_upgrade():
        add("其他工作", "fail", "已經有一個安裝更新的工作正在執行")
    elif get_job().get("state") == "running":
        add("其他工作", "fail", "正在檢查更新或切換鏡像站,請等完成後再試")
    else:
        add("其他工作", "ok", "沒有其他作業系統更新的工作在執行")

    if _apt_locked():
        add("apt 鎖定", "fail", "apt/dpkg 正在被其他程式使用(可能是自動更新或有人正在主機上執行 apt),請稍後再試")
    else:
        add("apt 鎖定", "ok", "沒有其他 apt/dpkg 在執行")

    interrupted = _dpkg_interrupted()
    if interrupted:
        add("上次安裝", "fail", "上次的安裝沒有正常完成,請先 SSH 進主機執行 dpkg --configure -a 修復。" + interrupted)
    else:
        add("上次安裝", "ok", "沒有中斷沒修好的安裝")

    root_free = _free_bytes("/")
    if root_free is None:
        add("磁碟空間", "warn", "無法取得 / 的剩餘空間")
    elif root_free < MIN_ROOT_FREE:
        add("磁碟空間", "fail", f"/ 只剩 {root_free / 1024 ** 3:.2f} GB,至少需要 1 GB")
    else:
        add("磁碟空間", "ok", f"/ 剩餘 {root_free / 1024 ** 3:.1f} GB")
    if sim and any(p["kernel"] for p in sim["packages"]) and os.path.ismount("/boot"):
        boot_free = _free_bytes("/boot")
        if boot_free is not None and boot_free < MIN_BOOT_FREE:
            add("/boot 空間", "fail", f"/boot 只剩 {boot_free / 1024 ** 2:.0f} MB,安裝新核心至少需要 150 MB")
        elif boot_free is not None:
            add("/boot 空間", "ok", f"/boot 剩餘 {boot_free / 1024 ** 2:.0f} MB")

    updated = _last_update_time()
    if updated:
        age = datetime.datetime.now() - datetime.datetime.strptime(updated, "%Y-%m-%d %H:%M:%S")
        if age.days >= LISTS_STALE_DAYS:
            add("套件清單", "warn", f"套件清單是 {updated} 更新的,已經超過 {age.days} 天,建議先按「檢查更新」")
        else:
            add("套件清單", "ok", f"套件清單 {updated} 更新")
    else:
        add("套件清單", "warn", "還沒更新過套件清單,建議先按「檢查更新」")
    return checks


def build_upgrade_plan(mode, exclude_docker):
    """模擬安裝,回傳要安裝/移除的套件與安裝前檢查結果。不會改動系統。"""
    sim, err = _simulate_for(mode, exclude_docker)
    if err:
        return None, err
    checks = upgrade_prechecks(sim)
    docker_available = []
    if exclude_docker:
        full, _ = _simulate_upgrade(mode)
        if full:
            docker_available = [p["name"] for p in full["packages"] if p["docker"]]
    pkgs = sim["packages"]
    return {
        "mode": mode, "mode_name": UPGRADE_MODES[mode]["name"], "exclude_docker": bool(exclude_docker),
        "packages": pkgs, "removals": sim["removals"], "kept_back": sim["kept_back"],
        "docker_excluded": docker_available,
        "summary": {
            "total": len(pkgs), "security": sum(p["security"] for p in pkgs),
            "kernel": sum(p["kernel"] for p in pkgs), "docker": sum(p["docker"] for p in pkgs),
            "new_install": sum(p["new_install"] for p in pkgs), "removals": len(sim["removals"]),
            "kept_back": len(sim["kept_back"]),
        },
        "checks": checks,
        "can_apply": bool(pkgs) and not any(c["level"] == "fail" for c in checks),
        "plan_hash": _plan_hash(mode, exclude_docker, sim),
    }, None


_RUN_SCRIPT = """#!/bin/bash
# WoofWeb 作業系統更新:由 systemd-run 在背景執行,跟 WebUI 無關,WebUI 重啟也不會中斷
export LC_ALL=C DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none
export NEEDRESTART_SUSPEND=1 NEEDRESTART_MODE=l
RUN_DIR={run_dir}
exec >>"$RUN_DIR/upgrade.log" 2>&1 </dev/null
finish() {{
  rc=$?
  echo "### $(date '+%F %T') 結束,結束代碼 $rc"
  printf '{{"rc": %d, "finished_at": "%s"}}\\n' "$rc" "$(date '+%F %T')" > "$RUN_DIR/result.json.tmp"
  mv "$RUN_DIR/result.json.tmp" "$RUN_DIR/result.json"
}}
trap finish EXIT
trap 'exit 143' TERM INT HUP
echo "### $(date '+%F %T') 開始安裝更新:{mode_name}{docker_note}"
{apt_get} -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \\
  -o Dpkg::Use-Pty=0 -o Dir::Etc::PreferencesParts="$RUN_DIR/preferences.d" {apt_args}
"""


def start_upgrade(mode, exclude_docker, plan_hash, username, ip):
    """開始安裝更新。回傳 (run_id, error)。"""
    if mode not in UPGRADE_MODES:
        return None, "更新模式不正確"
    sim, err = _simulate_for(mode, exclude_docker)
    if err:
        return None, err
    checks = upgrade_prechecks(sim)
    failed = [c for c in checks if c["level"] == "fail"]
    if failed:
        return None, "安裝前檢查沒有通過:" + ";".join(f"{c['name']}:{c['message']}" for c in failed)
    if not sim["packages"]:
        return None, "沒有需要安裝的更新"
    if plan_hash != _plan_hash(mode, exclude_docker, sim):
        return None, "要安裝的套件跟剛才確認的不一樣(套件清單可能剛更新過),請重新開啟安裝視窗確認"

    os.makedirs(UPGRADE_DIR, mode=0o700, exist_ok=True)
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(UPGRADE_DIR, run_id)
    if os.path.exists(run_dir):
        return None, "剛剛才開始一次安裝,請稍候再試"
    os.makedirs(run_dir, mode=0o700)
    prefs = os.path.join(run_dir, "preferences.d")
    if exclude_docker:
        _prefs_dir_with_docker_pins(prefs)
    else:
        os.makedirs(prefs, mode=0o700)
        src = os.path.join(APT_ETC, "preferences.d")
        if os.path.isdir(src):
            for name in os.listdir(src):
                if os.path.isfile(os.path.join(src, name)):
                    shutil.copy2(os.path.join(src, name), os.path.join(prefs, name))

    unit = _unit_name(run_id)
    meta = {
        "id": run_id, "unit": unit, "mode": mode, "mode_name": UPGRADE_MODES[mode]["name"],
        "exclude_docker": bool(exclude_docker), "username": username, "ip": ip,
        "started_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "packages": [{"name": p["name"], "old_version": p["old_version"], "new_version": p["new_version"]}
                     for p in sim["packages"]],
        "removals": [r["name"] for r in sim["removals"]],
        "kernel": any(p["kernel"] for p in sim["packages"]),
        "docker": any(p["docker"] for p in sim["packages"]),
    }
    _write_json(os.path.join(run_dir, "meta.json"), meta)
    script = os.path.join(run_dir, "run.sh")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_RUN_SCRIPT.format(
            run_dir=shlex.quote(run_dir), apt_get=shlex.quote(APT_GET),
            apt_args=" ".join(shlex.quote(a) for a in UPGRADE_MODES[mode]["apt_args"]),
            mode_name=UPGRADE_MODES[mode]["name"].replace('"', ""),
            docker_note=",這次不更新 Docker 套件" if exclude_docker else "",
        ))
    os.chmod(script, 0o700)

    try:
        proc = subprocess.run(
            [SYSTEMD_RUN, f"--unit={unit}", "--collect", "--description=WoofWeb 作業系統更新",
             "/bin/bash", script],
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        proc = None
        start_err = str(e)
    if proc is None or proc.returncode != 0:
        start_err = start_err if proc is None else (proc.stderr or proc.stdout).strip()
        _write_json(os.path.join(run_dir, "result.json"),
                    {"rc": -1, "finished_at": meta["started_at"], "start_error": start_err})
        return None, f"無法啟動安裝程序:{start_err}"
    _prune_upgrade_runs()
    return run_id, None


def _prune_upgrade_runs():
    try:
        runs = sorted(d for d in os.listdir(UPGRADE_DIR) if _RUN_ID_RE.match(d))
    except OSError:
        return
    for old in runs[:-UPGRADE_KEEP]:
        path = os.path.join(UPGRADE_DIR, old)
        if os.path.exists(os.path.join(path, "result.json")):
            shutil.rmtree(path, ignore_errors=True)


def _progress_from_log(run_dir, total):
    unpacked = configured = 0
    try:
        with open(os.path.join(run_dir, "upgrade.log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Unpacking "):
                    unpacked += 1
                elif line.startswith("Setting up "):
                    configured += 1
    except OSError:
        pass
    return {"unpacked": min(unpacked, total), "configured": min(configured, total), "total": total}


def _run_summary(run_id, log_fn=None):
    run_dir = os.path.join(UPGRADE_DIR, run_id)
    meta = _read_json(os.path.join(run_dir, "meta.json"))
    if not meta:
        return None
    result = _read_json(os.path.join(run_dir, "result.json"))
    if result is not None:
        state = "succeeded" if result.get("rc") == 0 else "failed"
    elif _unit_active(meta.get("unit", "")):
        state = "running"
    else:
        # 沒有結果檔、服務也不在了:通常是安裝途中主機被重新開機或程序被強制結束
        state = "interrupted"
    summary = {
        "id": run_id, "state": state, "mode": meta.get("mode"), "mode_name": meta.get("mode_name"),
        "exclude_docker": meta.get("exclude_docker"), "username": meta.get("username"),
        "started_at": meta.get("started_at"), "finished_at": (result or {}).get("finished_at", ""),
        "rc": (result or {}).get("rc"), "start_error": (result or {}).get("start_error", ""),
        "package_count": len(meta.get("packages", [])), "removals": meta.get("removals", []),
        "kernel": meta.get("kernel"), "docker": meta.get("docker"),
        "progress": _progress_from_log(run_dir, len(meta.get("packages", []))),
        "dpkg_audit": meta.get("dpkg_audit", ""),
    }
    if state != "running" and not meta.get("finalized"):
        _finalize_run(run_dir, meta, summary, log_fn)
        summary["dpkg_audit"] = meta.get("dpkg_audit", "")
    return summary


def _finalize_run(run_dir, meta, summary, log_fn):
    """安裝結束後第一次被查詢時:檢查 dpkg 狀態、重新計算可更新的套件、寫操作紀錄。只做一次。"""
    with _finalize_lock:
        fresh = _read_json(os.path.join(run_dir, "meta.json")) or meta
        if fresh.get("finalized"):
            meta.update(fresh)
            return
        audit = _dpkg_interrupted()
        meta.update({"finalized": True, "dpkg_audit": audit})
        _write_json(os.path.join(run_dir, "meta.json"), meta)
    if summary["state"] != "running" and not _apt_locked():
        sim, err = _simulate_upgrade()
        if not err:
            pkgs = sim["packages"]
            _save_last_check({
                "checked_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "packages": pkgs, "removals": sim["removals"], "warnings": [],
                "summary": {
                    "total": len(pkgs), "security": sum(p["security"] for p in pkgs),
                    "kernel": sum(p["kernel"] for p in pkgs), "docker": sum(p["docker"] for p in pkgs),
                    "new_install": sum(p["new_install"] for p in pkgs), "removals": len(sim["removals"]),
                },
            })
    if log_fn:
        state_text = {"succeeded": "完成", "failed": f"失敗(結束代碼 {summary['rc']})",
                      "interrupted": "中斷(沒有結束紀錄,可能途中重新開機)"}[summary["state"]]
        detail = (f"模式={meta.get('mode_name')}, 不更新 Docker={'是' if meta.get('exclude_docker') else '否'}, "
                  f"套件={len(meta.get('packages', []))} 個, 開始={meta.get('started_at')}, "
                  f"結束={summary.get('finished_at') or '-'}, 結果={state_text}, 紀錄={run_dir}/upgrade.log"
                  + (f", dpkg 狀態異常={audit}" if audit else ""))
        try:
            log_fn(meta.get("username"), meta.get("ip"), "系統維護-安裝作業系統更新(結果)",
                   summary["state"] == "succeeded" and not audit, detail)
        except Exception:
            pass


def list_upgrade_runs(log_fn=None):
    try:
        ids = sorted((d for d in os.listdir(UPGRADE_DIR) if _RUN_ID_RE.match(d)), reverse=True)
    except OSError:
        return []
    runs = []
    for run_id in ids[:UPGRADE_KEEP]:
        s = _run_summary(run_id, log_fn)
        if s:
            runs.append(s)
    return runs


def get_running_upgrade():
    try:
        ids = sorted((d for d in os.listdir(UPGRADE_DIR) if _RUN_ID_RE.match(d)), reverse=True)
    except OSError:
        return None
    for run_id in ids[:3]:
        run_dir = os.path.join(UPGRADE_DIR, run_id)
        if os.path.exists(os.path.join(run_dir, "result.json")):
            continue
        meta = _read_json(os.path.join(run_dir, "meta.json"))
        if meta and _unit_active(meta.get("unit", "")):
            return run_id
    return None


def read_upgrade_log(run_id, tail_bytes=None):
    if not run_id or not _RUN_ID_RE.match(run_id):
        return None
    path = os.path.join(UPGRADE_DIR, run_id, "upgrade.log")
    try:
        with open(path, "rb") as f:
            if tail_bytes:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - tail_bytes))
                data = f.read()
                if size > tail_bytes:
                    data = data.split(b"\n", 1)[-1]
            else:
                data = f.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 重新開機(另外的按鈕,安裝更新不會自動重開)
# ---------------------------------------------------------------------------
REBOOT_DELAY_SECONDS = 10


def start_reboot():
    """REBOOT_DELAY_SECONDS 秒後重新開機(讓網頁先收到回應)。回傳 error 或 None。"""
    if get_running_upgrade():
        return "正在安裝更新,安裝完成前不能重新開機"
    if get_job().get("state") == "running":
        return "正在檢查更新或切換鏡像站,請等完成後再重新開機"
    if _apt_locked():
        return "apt/dpkg 正在被其他程式使用,請等它完成後再重新開機"
    unit = "woofweb-reboot-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        proc = subprocess.run(
            [SYSTEMD_RUN, f"--unit={unit}", f"--on-active={REBOOT_DELAY_SECONDS}",
             "--description=WoofWeb 重新開機", "/usr/bin/systemctl", "reboot"],
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        return f"無法排定重新開機:{e}"
    if proc.returncode != 0:
        return f"無法排定重新開機:{(proc.stderr or proc.stdout).strip()}"
    return None
