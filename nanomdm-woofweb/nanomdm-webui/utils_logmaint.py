"""系統狀態頁「磁碟空間」下方的明細:列出容易佔用磁碟空間、會持續累積的資料目前的大小。
只讀取檔案大小與設定,不會修改或刪除任何東西。
"""
import datetime
import glob
import json
import os
import re
import subprocess
import time

_TIME_RE = re.compile(rb'"time":"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})')


# ---------------------------------------------------------------------------
# Docker 容器日誌
# ---------------------------------------------------------------------------
def _docker_inspect(container, fmt):
    try:
        proc = subprocess.run(["docker", "inspect", "-f", fmt, container],
                              capture_output=True, text=True, timeout=15)
    except Exception as e:
        return None, str(e)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip() or "docker inspect 失敗"
    return proc.stdout.strip(), None


def get_container_log_path(container):
    """回傳 (log_path, error)。只支援 json-file(Docker 預設的日誌格式)。"""
    out, err = _docker_inspect(container, "{{.HostConfig.LogConfig.Type}}\t{{.LogPath}}")
    if err:
        return None, err
    log_type, _, path = out.partition("\t")
    if log_type != "json-file":
        return None, f"容器日誌格式是 {log_type},只支援 json-file"
    if not path or not os.path.isfile(path):
        return None, "找不到容器日誌檔案"
    return path, None


def _parse_time(line):
    m = _TIME_RE.search(line)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1).decode(), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _first_line_from(f, pos):
    """從 pos 之後第一個完整行開始,回傳 (行首位置, 這行的 UTC 時間);找不到可解析的行回傳 (None, None)。"""
    f.seek(pos)
    if pos > 0:
        f.readline()  # 丟掉被切斷的半行
    while True:
        start = f.tell()
        line = f.readline()
        if not line:
            return None, None
        t = _parse_time(line)
        if t is not None:
            return start, t


def _oldest_time(path):
    try:
        with open(path, "rb") as f:
            _, t = _first_line_from(f, 0)
            return t
    except OSError:
        return None


def _utc_to_local_str(t):
    if t is None:
        return None
    local = t.replace(tzinfo=datetime.timezone.utc).astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 系統狀態頁:容易佔用磁碟空間的資料
# ---------------------------------------------------------------------------
def _dir_disk_bytes(paths):
    total = 0
    for root_path in paths:
        for root, _, files in os.walk(root_path):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_blocks * 512
                except OSError:
                    pass
    return total


def _mysql_data_dir(mysql_container):
    out, err = _docker_inspect(
        mysql_container,
        '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Source}}{{end}}{{end}}')
    return out if not err and out else None


def _binlog_expire_days(mysql_cfg, db_password):
    env = dict(os.environ)
    env["MYSQL_PWD"] = db_password or ""
    try:
        proc = subprocess.run(
            ["docker", "exec", "-e", "MYSQL_PWD", mysql_cfg["docker_container"], "mysql",
             "-u" + mysql_cfg["db_user"], "-N", "-B", "-e", "SELECT @@log_bin, @@binlog_expire_logs_seconds"],
            capture_output=True, text=True, timeout=15, env=env, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            return None, None
        log_bin, seconds = proc.stdout.strip().split("\t")
        return log_bin == "1", int(seconds) / 86400
    except Exception:
        return None, None


def _journald_limit_text():
    """journald 的 SystemMaxUse 設定;沒有設定時是 systemd 的預設值(檔案系統的 10%,最多 4GB)。"""
    value = None
    for path in ["/etc/systemd/journald.conf"] + sorted(glob.glob("/etc/systemd/journald.conf.d/*.conf")):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("SystemMaxUse="):
                        value = line.split("=", 1)[1].strip() or None
        except OSError:
            pass
    return f"上限 {value}" if value else "未設定上限,systemd 預設最多使用檔案系統的 10%(最多 4GB)"


def _container_log_limit_text(container):
    """容器日誌的大小上限(docker-compose 的 logging 設定);沒有設定時 Docker 不會限制大小。"""
    out, err = _docker_inspect(container, "{{json .HostConfig.LogConfig.Config}}")
    try:
        opts = json.loads(out) if not err and out else {}
    except ValueError:
        opts = {}
    max_size = (opts or {}).get("max-size")
    if not max_size:
        return "未設定上限(Docker 預設不限制大小)"
    return f"上限 {max_size} x {(opts or {}).get('max-file', '1')} 個檔案"


_storage_cache = {"at": 0, "data": None}


def get_storage_usage(cfg, db_password, cache_seconds=60):
    """系統狀態頁「磁碟空間」下方列出的資料用量。結果快取 cache_seconds 秒(頁面每 2 秒更新一次)。"""
    now = time.time()
    if _storage_cache["data"] is not None and now - _storage_cache["at"] < cache_seconds:
        return _storage_cache["data"]

    items = []

    # MySQL binlog
    data_dir = _mysql_data_dir(cfg["mysql"]["docker_container"])
    if data_dir:
        files = [p for p in glob.glob(os.path.join(data_dir, "binlog.[0-9]*")) if os.path.isfile(p)]
        log_bin, expire_days = _binlog_expire_days(cfg["mysql"], db_password)
        if log_bin is False:
            note = "MySQL 未開啟 binlog"
        elif expire_days is not None:
            note = f"{len(files)} 個檔案,MySQL 自動保留 {expire_days:g} 天"
        else:
            note = f"{len(files)} 個檔案"
        items.append({"name": "MySQL binlog", "path": os.path.join(data_dir, "binlog.*"),
                      "bytes": sum(os.path.getsize(p) for p in files), "note": note})
    else:
        items.append({"name": "MySQL binlog", "path": "", "bytes": None, "note": "找不到 MySQL 資料目錄"})

    # systemd journal
    journal_dirs = [d for d in ("/var/log/journal", "/run/log/journal") if os.path.isdir(d)]
    items.append({"name": "systemd journal", "path": ", ".join(journal_dirs),
                  "bytes": _dir_disk_bytes(journal_dirs), "note": _journald_limit_text()})

    # Docker 容器日誌
    for container in cfg.get("storage_usage", {}).get("containers", []):
        path, err = get_container_log_path(container)
        if err:
            items.append({"name": f"{container} 容器日誌", "path": "", "bytes": None, "note": err})
            continue
        oldest = _utc_to_local_str(_oldest_time(path))
        policy = _container_log_limit_text(container)
        note = (f"最早紀錄 {oldest},{policy}" if oldest else policy)
        items.append({"name": f"{container} 容器日誌", "path": path,
                      "bytes": os.path.getsize(path), "note": note})

    # webhook-automation.log(含輪替出來的 .1 ~ .3)
    webhook_log = cfg.get("storage_usage", {}).get("webhook_log_path", "")
    if webhook_log:
        files = [p for p in [webhook_log] + sorted(glob.glob(webhook_log + ".[0-9]*")) if os.path.isfile(p)]
        items.append({"name": "webhook-automation.log", "path": webhook_log,
                      "bytes": sum(os.path.getsize(p) for p in files) if files else 0,
                      "note": f"{len(files)} 個檔案,超過 50MB 自動輪替,最多保留 3 份舊檔"})

    data = {"items": items, "checked_at": datetime.datetime.now().strftime("%H:%M:%S")}
    _storage_cache.update({"at": now, "data": data})
    return data
