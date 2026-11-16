"""MySQL InnoDB buffer pool(記憶體快取)的狀態查詢與調整,給「系統參數」頁面的 MySQL 設定區塊使用。

調整方式是 MySQL 8.0 內建的 SET PERSIST:
  - 執行當下就線上調整大小(不用重新啟動 MySQL),調整過程在背景進行,可以查詢進度
  - 同時寫入資料目錄裡的 mysqld-auto.cnf,MySQL 重新啟動後仍然有效
  - 不需要修改 docker-compose.yml
恢復預設用 RESET PERSIST:只清除 mysqld-auto.cnf 裡的設定,目前執行中的大小不變,
下次重新啟動 MySQL 後才會回到預設值(128 MB)。

修改系統變數需要 MySQL root 權限,密碼從 .env 讀取,透過環境變數 MYSQL_PWD 傳給 mysql client,
不放在指令列參數裡(避免出現在 ps 的程序清單中)。
"""
import os
import subprocess

MB = 1024 * 1024
DEFAULT_BUFFER_POOL_BYTES = 128 * MB   # MySQL 8.0 的預設值
MIN_BUFFER_POOL_BYTES = 128 * MB
MAX_HOST_MEMORY_RATIO = 0.5            # 最多只能設定到主機記憶體的一半,避免主機記憶體不足

# performance_schema.variables_info 的 VARIABLE_SOURCE 對應的說明
_SOURCE_LABELS = {
    "COMPILED": "預設值",
    "PERSISTED": "已手動設定(SET PERSIST)",
    "COMMAND_LINE": "MySQL 啟動參數",
    "GLOBAL": "MySQL 設定檔",
    "SERVER": "MySQL 設定檔",
    "EXPLICIT": "MySQL 設定檔",
    "EXTRA": "MySQL 設定檔",
    "USER": "MySQL 設定檔",
    "LOGIN": "MySQL 設定檔",
}


def _run_root_sql(docker_container, root_password, sql, timeout=20):
    """用 root 身分執行 SQL,回傳 (rc, stdout, stderr)。密碼走環境變數,不出現在指令列。"""
    env = dict(os.environ)
    env["MYSQL_PWD"] = root_password
    args = ["docker", "exec", "-e", "MYSQL_PWD", docker_container,
            "mysql", "-uroot", "-N", "-B", "-e", sql]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "MySQL 查詢逾時"
    except Exception as e:
        return -1, "", f"執行失敗: {e}"


def _host_memory_bytes():
    with open("/proc/meminfo", "r") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    return 0


def _round_to_unit(value, unit):
    return max(unit, int(round(value / unit)) * unit)


def get_buffer_pool_status(docker_container, root_password, schemas=("nanomdm",)):
    """查詢 buffer pool 目前的狀態。回傳 (status_dict, error)。"""
    schema_list = ",".join("'" + s.replace("'", "''") + "'" for s in schemas)
    sql = (
        "SELECT @@innodb_buffer_pool_size, @@innodb_buffer_pool_chunk_size, @@innodb_buffer_pool_instances, "
        "(SELECT VARIABLE_SOURCE FROM performance_schema.variables_info WHERE VARIABLE_NAME = 'innodb_buffer_pool_size'), "
        "IFNULL((SELECT VARIABLE_VALUE FROM performance_schema.persisted_variables WHERE VARIABLE_NAME = 'innodb_buffer_pool_size'), ''), "
        f"IFNULL((SELECT SUM(data_length + index_length) FROM information_schema.tables WHERE table_schema IN ({schema_list})), 0);"
        "SELECT VARIABLE_NAME, VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME IN ("
        "'Innodb_buffer_pool_reads', 'Innodb_buffer_pool_read_requests', 'Innodb_buffer_pool_pages_free', "
        "'Innodb_buffer_pool_pages_total', 'Innodb_buffer_pool_resize_status', 'Uptime');"
    )
    rc, out, err = _run_root_sql(docker_container, root_password, sql)
    if rc != 0:
        return None, (err or out or "查詢失敗").strip()

    lines = out.strip().splitlines()
    try:
        size, chunk, instances, source, persisted, data_bytes = lines[0].split("\t")
        size, chunk, instances, data_bytes = int(size), int(chunk), int(instances), int(float(data_bytes or 0))
    except (IndexError, ValueError):
        return None, "無法解析 MySQL 回傳的資料"
    status = {}
    for line in lines[1:]:
        name, _, value = line.partition("\t")
        status[name] = value

    def _int(name):
        try:
            return int(status.get(name) or 0)
        except ValueError:
            return 0

    read_requests, reads = _int("Innodb_buffer_pool_read_requests"), _int("Innodb_buffer_pool_reads")
    pages_total, pages_free = _int("Innodb_buffer_pool_pages_total"), _int("Innodb_buffer_pool_pages_free")
    unit = chunk * instances
    host_memory = _host_memory_bytes()
    min_bytes = max(MIN_BUFFER_POOL_BYTES, unit)
    max_bytes = max(min_bytes, int(host_memory * MAX_HOST_MEMORY_RATIO) // unit * unit)
    persisted_bytes = int(persisted) if persisted.isdigit() else None

    # SET PERSIST 之後,執行中的來源會顯示 DYNAMIC(重新啟動後才變成 PERSISTED),
    # 所以只要 mysqld-auto.cnf 裡有設定,就一律當作「已手動設定」
    if persisted_bytes is not None:
        source_label = _SOURCE_LABELS["PERSISTED"]
    elif source in ("DYNAMIC", "PERSISTED"):
        source_label = "手動設定已清除,目前大小維持到下次重新啟動 MySQL"
    else:
        source_label = _SOURCE_LABELS.get(source, source)

    return {
        "size_mb": size // MB,
        "source": source,
        "source_label": source_label,
        "persisted_mb": persisted_bytes // MB if persisted_bytes is not None else None,
        "default_mb": DEFAULT_BUFFER_POOL_BYTES // MB,
        "unit_mb": unit // MB,
        "min_mb": min_bytes // MB,
        "max_mb": max_bytes // MB,
        "host_memory_mb": host_memory // MB,
        "data_mb": round(data_bytes / MB, 1),
        "hit_ratio": round((1 - reads / read_requests) * 100, 2) if read_requests else None,
        "free_ratio": round(pages_free / pages_total * 100, 1) if pages_total else None,
        "uptime_seconds": _int("Uptime"),
        "resize_status": status.get("Innodb_buffer_pool_resize_status", ""),
    }, None


def normalize_buffer_pool_mb(requested_mb, status):
    """把使用者輸入的大小換成 MySQL 調整單位的倍數,並限制在最小值~最大值之間。回傳 MB。"""
    unit = status["unit_mb"]
    value = _round_to_unit(int(requested_mb), unit)
    return min(max(value, status["min_mb"]), status["max_mb"])


def apply_buffer_pool_size(docker_container, root_password, size_mb):
    """SET PERSIST:立即開始線上調整,並保留到 MySQL 重新啟動之後。回傳 (ok, error)。"""
    size_bytes = int(size_mb) * MB
    rc, out, err = _run_root_sql(docker_container, root_password,
                                 f"SET PERSIST innodb_buffer_pool_size = {size_bytes};")
    if rc != 0:
        return False, (err or out or "設定失敗").strip()
    return True, None


def reset_buffer_pool_size(docker_container, root_password):
    """RESET PERSIST:清除手動設定,目前大小不變,下次重新啟動 MySQL 後回到預設值。回傳 (ok, error)。"""
    rc, out, err = _run_root_sql(docker_container, root_password,
                                 "RESET PERSIST IF EXISTS innodb_buffer_pool_size;")
    if rc != 0:
        return False, (err or out or "恢復預設失敗").strip()
    return True, None
