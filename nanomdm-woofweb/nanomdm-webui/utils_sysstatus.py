# -*- coding: utf-8 -*-
"""
系統狀態監控:
- 系統資源(CPU/記憶體/磁碟/作業系統版本)
- Docker容器狀態、log檢視、重啟
- systemd服務狀態、log檢視、重啟
- MySQL資料庫/資料表/資料筆數統計

全部用Python標準函式庫實作(解析/proc、呼叫docker/systemctl/journalctl指令),
不額外增加套件依賴,避免部署時還要另外pip install。
"""
import datetime
import json
import os
import platform
import shutil
import subprocess
import time


def run_cmd(args, timeout=15):
    """簡單包裝subprocess呼叫,回傳(returncode, stdout, stderr)。
    明確把stdin指向DEVNULL:systemd服務底下沒有互動式終端機可以輸入,
    如果被呼叫的指令(例如certbot)因為某種原因嘗試讀取標準輸入等待使用者確認,
    明確導向DEVNULL會讓它立刻收到EOF、直接失敗或跳過,而不是無限期卡住等一個
    永遠不會出現的輸入,導致最後只能靠逾時機制硬generate出來。
    """
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"指令逾時: {e}"
    except FileNotFoundError as e:
        return -1, "", f"找不到指令: {e}"
    except Exception as e:
        return -1, "", f"執行失敗: {e}"


# ---------------------------------------------------------------------------
# 系統資源
# ---------------------------------------------------------------------------
def _read_proc_stat_cpu_lines():
    """讀取/proc/stat裡每個核心的cpu時間統計,回傳 {core_name: [user,nice,system,idle,iowait,irq,softirq,steal,...]}"""
    result = {}
    with open("/proc/stat", "r") as f:
        for line in f:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            core_name = parts[0]
            if core_name == "cpu" and len(parts) < 2:
                continue
            # cpu 或 cpu0/cpu1/... 開頭才是核心資料行,其他(intr/ctxt/btime等)不要
            if not (core_name == "cpu" or (core_name[3:].isdigit())):
                continue
            values = [int(x) for x in parts[1:]]
            result[core_name] = values
    return result


def get_cpu_usage_percpu(sample_interval=0.3):
    """計算每個核心的CPU使用率百分比,用兩次/proc/stat快照相減計算(標準做法)。
    回傳 [{"core": "cpu0", "percent": 12.3}, ...],第一筆是"cpu"代表全部核心平均。
    """
    snap1 = _read_proc_stat_cpu_lines()
    time.sleep(sample_interval)
    snap2 = _read_proc_stat_cpu_lines()

    results = []
    for core_name, values2 in snap2.items():
        values1 = snap1.get(core_name)
        if not values1:
            continue
        # /proc/stat欄位順序: user nice system idle iowait irq softirq steal guest guest_nice
        idle1 = values1[3] + (values1[4] if len(values1) > 4 else 0)
        idle2 = values2[3] + (values2[4] if len(values2) > 4 else 0)
        total1 = sum(values1)
        total2 = sum(values2)

        total_delta = total2 - total1
        idle_delta = idle2 - idle1
        percent = 0.0
        if total_delta > 0:
            percent = round((1 - idle_delta / total_delta) * 100, 1)
        results.append({"core": core_name, "percent": percent})

    # 排序:cpu(平均)排最前面,其餘依核心編號排序
    results.sort(key=lambda r: (r["core"] != "cpu", int(r["core"][3:]) if r["core"] != "cpu" else -1))
    return results


def get_memory_usage():
    """解析/proc/meminfo,回傳記憶體使用狀況(單位MB)"""
    info = {}
    with open("/proc/meminfo", "r") as f:
        for line in f:
            key, _, rest = line.partition(":")
            value_kb = rest.strip().split()[0]
            info[key] = int(value_kb)

    total_kb = info.get("MemTotal", 0)
    available_kb = info.get("MemAvailable", info.get("MemFree", 0))
    used_kb = total_kb - available_kb

    return {
        "total_mb": round(total_kb / 1024, 1),
        "used_mb": round(used_kb / 1024, 1),
        "available_mb": round(available_kb / 1024, 1),
        "percent": round((used_kb / total_kb) * 100, 1) if total_kb > 0 else 0,
    }


def get_disk_usage(path="/"):
    """用標準函式庫shutil.disk_usage取得磁碟空間(單位GB)"""
    total, used, free = shutil.disk_usage(path)
    gb = 1024 ** 3
    return {
        "total_gb": round(total / gb, 1),
        "used_gb": round(used / gb, 1),
        "free_gb": round(free / gb, 1),
        "percent": round((used / total) * 100, 1) if total > 0 else 0,
    }


def get_os_info():
    """取得作業系統版本與核心版本"""
    pretty_name = platform.system()
    try:
        with open("/etc/os-release", "r") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    pretty_name = line.split("=", 1)[1].strip().strip('"')
                    break
    except FileNotFoundError:
        pass
    return {
        "os_name": pretty_name,
        "kernel_version": platform.release(),
    }


def get_system_status():
    """整合系統狀態的入口函式,回傳前端要顯示的完整資料"""
    return {
        "current_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "os_info": get_os_info(),
        "cpu": get_cpu_usage_percpu(),
        "memory": get_memory_usage(),
        "disk": get_disk_usage("/"),
    }


# ---------------------------------------------------------------------------
# Docker 容器狀態
# ---------------------------------------------------------------------------
# Docker容器用途說明(這些都是整個系統建置過程中實際確認過的服務,不是查證別人的文件)
CONTAINER_PURPOSES = {
    "nanomdm-server": "MDM主服務,處理Apple MDM協定的裝置註冊、指令派送與佇列、APNs推播",
    "nanoaxm-server": "跟Apple School/Business Manager新版API(ABM/ASM)溝通,負責裝置改派、VPP軟體授權等操作",
    "nanodep-server": "跟Apple舊版DEP(Device Enrollment Program)API溝通,提供ADE自動註冊設定檔管理",
    "nanomdm-scep": "SCEP(Simple Certificate Enrollment Protocol)伺服器,負責發放裝置在MDM註冊過程中需要的身分憑證",
    "nanomdm-mysql": "MySQL資料庫,儲存nanomdm/nanodep/nanoaxm三個服務的所有持久化資料",
}


def _describe_container(name):
    return CONTAINER_PURPOSES.get(name, "用途尚未特別記錄")


def list_docker_containers():
    """用 docker ps --format json 取得目前所有容器狀態(含已停止的,-a),
    每行輸出一個JSON物件,比解析docker ps的人類可讀表格文字可靠。
    """
    rc, out, err = run_cmd(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=15)
    if rc != 0:
        return None, (err or out or "docker ps 執行失敗")

    containers = []
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append({
            "name": data.get("Names", ""),
            "purpose": _describe_container(data.get("Names", "")),
            "image": data.get("Image", ""),
            "status": data.get("Status", ""),
            "state": data.get("State", ""),
            "ports": data.get("Ports", ""),
            "created": data.get("CreatedAt", ""),
        })
    containers.sort(key=lambda c: c["name"])
    return containers, None


def get_docker_logs(container_name, tail=200):
    """取得docker容器的log,帶正確時區的時間戳記(docker預設是UTC,轉成GMT+8)。
    用 --timestamps 讓每行log前面帶上RFC3339格式的時間戳記,方便逐行轉換時區。
    stdout跟stderr都要接起來合併處理:Go寫的服務(nanomdm-server/nanoaxm-server/
    nanomdm-scep都是同一個micromdm家族的Go程式)習慣把結構化log寫到stderr而不是stdout,
    只接stdout會漏掉這幾個容器幾乎全部的log內容。
    """
    rc, out, err = run_cmd(["docker", "logs", "--timestamps", "--tail", str(tail), container_name], timeout=20)
    if rc != 0:
        return None, (err or "讀取log失敗")

    combined = (out or "") + (err or "")
    lines = []
    for line in combined.splitlines():
        converted = _convert_rfc3339_prefix_to_gmt8(line)
        lines.append(converted)
    return "\n".join(lines), None


def restart_docker_container(container_name):
    rc, out, err = run_cmd(["docker", "restart", container_name], timeout=30)
    if rc != 0:
        return False, (err or out or "重啟失敗")
    return True, None


def _convert_rfc3339_prefix_to_gmt8(line):
    """docker/journalctl log行開頭常是RFC3339格式時間戳記(UTC),嘗試解析並轉成GMT+8顯示,
    解析失敗就照原樣回傳整行(不要讓格式怪異的log行讓整個功能掛掉)。
    """
    parts = line.split(" ", 1)
    if len(parts) != 2:
        return line
    ts_text, rest = parts
    try:
        # docker的--timestamps輸出格式類似 2026-08-07T10:18:13.123456789Z
        ts_text_clean = ts_text.rstrip("Z")
        if "." in ts_text_clean:
            main_part, frac_part = ts_text_clean.split(".", 1)
            frac_part = frac_part[:6]  # python只支援到微秒(6位),多的位數截斷
            ts_text_clean = f"{main_part}.{frac_part}"
            dt = datetime.datetime.strptime(ts_text_clean, "%Y-%m-%dT%H:%M:%S.%f")
        else:
            dt = datetime.datetime.strptime(ts_text_clean, "%Y-%m-%dT%H:%M:%S")
        dt_utc = dt.replace(tzinfo=datetime.timezone.utc)
        dt_gmt8 = dt_utc.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
        return f"{dt_gmt8.strftime('%Y-%m-%d %H:%M:%S')} {rest}"
    except (ValueError, IndexError):
        return line


# ---------------------------------------------------------------------------
# systemd 服務狀態
# ---------------------------------------------------------------------------
# systemd服務用途說明(這些都是整個系統建置過程中實際確認過的服務)
SERVICE_PURPOSES = {
    "webhook-automation.service": "接收nanomdm的webhook事件(裝置完成註冊時觸發),自動執行改名、baseline描述檔推送、群組專屬App安裝",
    "enroll-server.service": "動態產生ADE註冊描述檔,依裝置序號查出對應群組、回傳正確的enroll json給Apple DEP服務使用",
    "nanodep-syncer.service": "持續從Apple DEP API同步已指派給本組織的裝置清單到本地資料庫",
    "nanomdm-webui.service": "目前你正在使用的這套Web管理介面本身",
    "nginx.service": "反向代理伺服器,把外部HTTPS請求依路徑轉發到nanomdm-webui、nanomdm-server等內部服務",
}


def _describe_service(name):
    return SERVICE_PURPOSES.get(name, "用途尚未特別記錄")


def get_systemd_service_status(service_name):
    """查詢單一systemd服務的狀態。用 systemctl show --property=... 拿機器可讀的key=value輸出,
    比解析 systemctl status 的排版化文字可靠。
    """
    rc, out, err = run_cmd(
        ["systemctl", "show", service_name,
         "--property=ActiveState,SubState,ActiveEnterTimestamp,MainPID,Description"],
        timeout=10,
    )
    if rc != 0:
        return {"service": service_name, "purpose": _describe_service(service_name), "active_state": "unknown", "error": err or out}

    info = {"service": service_name}
    for line in out.strip().splitlines():
        key, _, value = line.partition("=")
        info[key] = value

    return {
        "service": service_name,
        "purpose": _describe_service(service_name),
        "active_state": info.get("ActiveState", "unknown"),
        "sub_state": info.get("SubState", ""),
        "active_since": info.get("ActiveEnterTimestamp", ""),
        "main_pid": info.get("MainPID", ""),
        "description": info.get("Description", ""),
    }


def get_systemd_logs(service_name, lines=200):
    """取得systemd服務的journal log。journalctl預設用系統本地時區顯示時間戳記
    (跟docker內部固定用UTC不一樣),不需要額外做時區轉換,直接沿用-o short-iso的輸出。
    """
    rc, out, err = run_cmd(
        ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager", "-o", "short-iso"],
        timeout=20,
    )
    if rc != 0:
        return None, (err or "讀取log失敗(可能需要較高權限,或該服務名稱不存在)")
    return out, None


def restart_systemd_service(service_name):
    rc, out, err = run_cmd(["systemctl", "restart", service_name], timeout=30)
    if rc != 0:
        return False, (err or out or "重啟失敗")
    return True, None


def reload_systemd_service(service_name):
    """優雅重新載入設定(不是完整重啟),不會中斷現有的連線。
    nginx這類服務適用:reload只會讓worker process重新讀取設定/憑證,
    現有連線(包括用來顯示這個操作進度本身的SSE連線)不會被砍斷,
    跟restart(整個服務程序砍掉重開,會直接中斷所有現有連線)不一樣。
    """
    rc, out, err = run_cmd(["systemctl", "reload", service_name], timeout=30)
    if rc != 0:
        return False, (err or out or "重新載入失敗")
    return True, None


# ---------------------------------------------------------------------------
# MySQL 資料庫狀態
# ---------------------------------------------------------------------------
# 資料表用途說明。只收錄有查證過官方schema.sql或官方文件確認的項目,
# nanomdm的8張表是直接從官方schema.sql(github.com/micromdm/nanomdm)查證確認的。
# nanodep目前只查證到dep_names這張表確實存在(來自官方sqlc.yaml的欄位設定),
# 用途依官方operations-guide.md的描述合理推斷。
# nanoaxm目前查不到公開的schema文件(這是較新的專案,還沒有完整文件),
# 查不到的表格一律誠實標示「用途尚未特別記錄」,不編造內容。
TABLE_PURPOSES = {
    # nanomdm(已查證官方schema.sql: github.com/micromdm/nanomdm/blob/main/storage/mysql/schema.sql)
    "devices": "裝置基本資料:識別憑證、序號、UnlockToken、最近一次Authenticate/TokenUpdate的原始內容",
    "users": "macOS使用者層級的MDM註冊資料(iOS/iPadOS部署通常用不到,只有macOS的使用者註冊才會用)",
    "enrollments": "裝置與使用者的MDM註冊紀錄:APNs推播三要素(topic/push_magic/token)、啟用狀態、最後連線時間",
    "commands": "所有MDM指令的內容(原始plist),不分裝置,是指令本身的定義",
    "command_results": "裝置對MDM指令的回應結果(Acknowledged/Error/NotNow等狀態與回應內容)",
    "enrollment_queue": "每個裝置的指令佇列:哪些指令排給哪個裝置、優先順序",
    "push_certs": "APNs推播憑證(每個topic對應的憑證與金鑰)",
    "cert_auth_associations": "裝置憑證與enrollment的關聯,用於SCEP憑證身分認證流程",
    # nanodep(已查證dep_names表確實存在: github.com/micromdm/nanodep/blob/main/storage/mysql/sqlc.yaml)
    "dep_names": "DEP/ABM「伺服器名稱」的設定資料,包含跟Apple交換OAuth token用的PKI憑證與金鑰",
    # nanoaxm(已由使用者實際貼出DESCRIBE結果確認欄位結構)
    "axm_names": "AXM(Apple School/Business Manager新版API)的「伺服器名稱」設定資料。跟舊版DEP的伺服器token驗證方式不同,"
                 "新版API改用OAuth 2.0 + JWT簽章的驗證方式:client_id/key_id是註冊在ABM/ASM後台的用戶端識別碼,"
                 "priv_key_pem是用來簽署JWT驗證請求的私鑰,ca_token/ca_validity_sec/ca_expiry_unix則是快取住的"
                 "OAuth access token與到期時間(這樣不用每次呼叫API都重新走一次OAuth驗證流程)",
}


def _describe_table(table_name):
    return TABLE_PURPOSES.get(table_name, "用途尚未特別記錄")


def _query_one_mysql_database(docker_container, db_user, db_password, timeout=20):
    """查詢單一組帳密能看到的資料庫/資料表/估計筆數。
    用 information_schema.tables 的 TABLE_ROWS(InnoDB引擎下是估計值,不是精確COUNT(*),
    但取得速度快很多,對於儀表板總覽用途來說已經足夠)。
    """
    sql = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS, "
        "ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS SIZE_MB "
        "FROM information_schema.tables "
        "WHERE TABLE_SCHEMA NOT IN ('information_schema','mysql','performance_schema','sys') "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME;"
    )
    args = [
        "docker", "exec", docker_container, "mysql",
        f"-u{db_user}", f"-p{db_password}",
        "-N", "-B", "--raw", "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=timeout)
    if rc != 0:
        return None, (err or out or "查詢失敗")

    databases = {}
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        schema, table, rows, size_mb = parts[0], parts[1], parts[2], parts[3]
        db_entry = databases.setdefault(schema, {"database": schema, "tables": [], "total_rows": 0})
        row_count = int(rows) if rows.isdigit() else 0
        db_entry["tables"].append({
            "table": table,
            "purpose": _describe_table(table),
            "rows": row_count,
            "size_mb": float(size_mb) if size_mb not in ("NULL", "") else 0,
        })
        db_entry["total_rows"] += row_count

    return list(databases.values()), None


def get_mysql_database_stats(db_configs, timeout=20):
    """db_configs: [{"label": "nanomdm", "docker_container": ..., "db_user": ..., "db_password": ...}, ...]
    每組帳密的權限可能是各自獨立、互相看不到彼此資料庫的(常見的資安慣例:各服務用各自的
    帳號只能存取自己的資料庫),所以要逐組分別查詢再合併,不能只靠單一帳號連線一次就看到全部。
    單一組帳密查詢失敗不影響其他組繼續查(例如nanoaxm的帳密還沒設定好,不該讓nanomdm/nanodep
    的資料也連帶顯示不出來)。
    回傳 (資料庫清單, 錯誤訊息清單) — 就算部分失敗,已經查到的資料庫還是會回傳。
    """
    all_databases = []
    errors = []
    seen_schemas = set()

    for cfg in db_configs:
        result, err = _query_one_mysql_database(cfg["docker_container"], cfg["db_user"], cfg["db_password"], timeout=timeout)
        if err:
            errors.append(f"{cfg.get('label', cfg['db_user'])}: {err}")
            continue
        for db in result:
            if db["database"] in seen_schemas:
                continue  # 避免同一個資料庫被不同帳密重複查到、重複顯示
            seen_schemas.add(db["database"])
            all_databases.append(db)

    return all_databases, errors


# ---------------------------------------------------------------------------
# 靜態檔案檢視
# ---------------------------------------------------------------------------
def _file_check(path, description):
    """檢查單一檔案是否存在,順便附上大小跟最後修改時間,方便一眼看出是不是空的或太久沒更新"""
    exists = os.path.exists(path)
    info = {"path": path, "description": description, "exists": exists}
    if exists:
        try:
            stat = os.stat(path)
            info["size_bytes"] = stat.st_size
            info["modified_at"] = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass
    return info


def get_static_files_status(cfg):
    """列出這套系統所有已知會用到的靜態檔案(script/csv/env),檢查是否存在;
    json註冊檔跟mobileconfig描述檔是動態清單(每個群組各自一份),直接掃描實際目錄內容;
    最後掃描部署目錄,找出不在已知清單裡的檔案,提醒使用者可能有遺漏未列入管理的檔案。
    """
    paths = cfg.get("paths", {})
    # 部署目錄用devices.csv的所在目錄推導,跟其他檔案的判斷方式保持一致,
    # 不要把webhook-server.py等檔案的路徑寫死成絕對路徑,否則部署目錄一旦跟寫死的路徑對不上,
    # 會出現「同一個檔案既被判斷成不存在、又被列進遺漏清單」這種自相矛盾的結果。
    deployment_dir = os.path.dirname(os.path.abspath(paths.get("devices_csv", "/opt/nanomdm-deployment/x"))) or "/opt/nanomdm-deployment"

    scripts = [
        _file_check(os.path.join(deployment_dir, "webhook-server.py"),
                    "webhook自動化伺服器:接收裝置完成MDM註冊的通知(TokenUpdate事件),自動執行改名、"
                    "推送baseline描述檔、安裝群組App、指派VPP授權"),
        _file_check(os.path.join(deployment_dir, "enroll-server.py"),
                    "動態產生ADE註冊描述檔的伺服器:依裝置序號查出對應群組,回傳正確的enroll json給Apple DEP服務"),
        _file_check(paths.get("dep_account_detail_script", ""),
                    "nanodep官方工具:查詢DEP帳號資訊(組織名稱等),用在頁面頂部的ASM連線狀態顯示"),
        _file_check(paths.get("dep_device_details_script", ""),
                    "nanodep官方工具:查詢單一裝置的DEP詳細資料"),
        _file_check(paths.get("check_vpp_license_script", ""),
                    "查詢VPP軟體授權數量的腳本,用在「ASM 軟體資訊」頁"),
        _file_check(paths.get("cmdr_script", ""),
                    "nanomdm官方的command-line工具,可以用指令列手動送MDM指令。"
                    "⚠️ config.py裡雖然有設定這個路徑,但目前這套webui的程式碼裡沒有任何地方實際呼叫它"
                    "(派送命令功能是直接呼叫nanomdm的API,沒有透過這個腳本),留著應該是供手動除錯用"),
    ]

    csv_files = [
        _file_check(paths.get("devices_csv", ""),
                    "裝置名稱/群組/WiFi MAC對照表,這套系統自己維護的核心資料來源"),
        _file_check(cfg.get("devices_status_cache", {}).get("csv_path", ""),
                    "裝置狀態快取:電量/容量/系統版本/可更新版本/IP位址/遺失模式定位等,背景排程每10分鐘自動更新"),
        _file_check(paths.get("vpp_cache_csv", ""),
                    "VPP軟體授權清單快取:adamId/BundleID/名稱/總量/剩餘量"),
        _file_check(cfg.get("asm_devices_cache", {}).get("servers_csv", ""),
                    "ASM(Apple School/Business Manager)MDM伺服器清單快取"),
        _file_check(cfg.get("asm_devices_cache", {}).get("devices_csv", ""),
                    "ASM所有裝置清單快取(含已指派/未指派狀態)"),
    ]

    env_file = _file_check(paths.get("env_file", ""),
                            "所有服務共用的環境變數設定檔,包含API金鑰、資料庫密碼等敏感資訊")

    fixed_json_files = [
        _file_check(paths.get("groups_json", ""),
                    "群組定義:每個群組的描述、綁定的App清單、對應的enroll json與mobileconfig檔名"),
        _file_check(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_config.json"),
                    "這套系統自己的設定檔:管理者帳號、IP白名單、品牌設定、系統狀態監控清單等"),
        _file_check(paths.get("udid_serial_cache", ""),
                    "webhook-server.py用的UDID↔裝置序號暫存對照,裝置完成Authenticate後、TokenUpdate前的過渡期資料"),
    ]

    enroll_profiles = []
    dep_profiles_dir = paths.get("dep_profiles_dir", "")
    if dep_profiles_dir and os.path.isdir(dep_profiles_dir):
        for fname in sorted(os.listdir(dep_profiles_dir)):
            full_path = os.path.join(dep_profiles_dir, fname)
            if os.path.isfile(full_path):
                enroll_profiles.append(_file_check(full_path, "群組專屬的ADE註冊設定檔(enroll json)"))

    mobileconfig_files = []
    mobileconfig_dir = paths.get("mobileconfig_dir", "")
    protected_descriptions = {
        "baseline.mobileconfig": "所有裝置完成註冊後都會推送的基礎描述檔(baseline),由webhook-server.py自動推送",
        "enroll-template.mobileconfig": "系統預設的精簡註冊描述檔,enroll-server.py實際讀取使用的檔案",
    }
    if mobileconfig_dir and os.path.isdir(mobileconfig_dir):
        for fname in sorted(os.listdir(mobileconfig_dir)):
            full_path = os.path.join(mobileconfig_dir, fname)
            if os.path.isfile(full_path):
                description = protected_descriptions.get(fname, "群組專屬的描述檔(mobileconfig)")
                mobileconfig_files.append(_file_check(full_path, description))

    # 掃描部署目錄,抓出不在上面任何清單裡的檔案,提醒可能有遺漏
    known_paths = set()
    for group in [scripts, csv_files, fixed_json_files, mobileconfig_files, [env_file]]:
        for item in group:
            known_paths.add(os.path.abspath(item["path"]))
    for item in enroll_profiles:
        known_paths.add(os.path.abspath(item["path"]))

    unaccounted_files = []
    if os.path.isdir(deployment_dir):
        for fname in sorted(os.listdir(deployment_dir)):
            full_path = os.path.join(deployment_dir, fname)
            if not os.path.isfile(full_path):
                continue  # 只列檔案,子目錄(dep-profiles/mobileconfig/logs/env_backups等)不列入,那些另外處理
            if os.path.abspath(full_path) not in known_paths:
                unaccounted_files.append(full_path)

    return {
        "scripts": scripts,
        "csv_files": csv_files,
        "env_file": env_file,
        "json_files": {"fixed": fixed_json_files, "enroll_profiles": enroll_profiles},
        "mobileconfig_files": mobileconfig_files,
        "unaccounted_files": unaccounted_files,
    }
