#!/usr/bin/env python3
"""
NanoMDM Webhook 自動化伺服器
1. 接收 NanoMDM 的 mdm.Authenticate 事件，解析序號並暫存（UDID -> 序號）
2. 接收 mdm.TokenUpdate 事件（代表裝置剛完成註冊），從暫存取出序號
3. 用序號查 devices.csv 取得裝置名稱與群組
4. 依序送出：改名指令 -> baseline profile -> 群組專屬 App 安裝
"""
import http.server
import json
import csv
import subprocess
import uuid
import time
import os
import base64
import plistlib
import datetime
import fcntl

# ===== 設定區：請依實際環境調整 =====
NANOMDM_API_KEY = os.environ.get("NANOMDM_API_KEY", "")
# 改讀.env的NANOMDM_BASE_URL,不再寫死對外網域。
# 這樣可以直接打本機端口,不用繞經nginx/DNS/TLS,也不會因為對外網域或nginx設定變動而連帶壞掉,
# 而且跟webui用的是同一個設定值來源,不會兩邊不同步。備援預設值用標準本機端口(這是
# docker-compose.yml裡nanomdm-server慣例對外暴露的端口),沒設定.env時至少還有機會直接可用。
NANOMDM_URL = os.environ.get("NANOMDM_BASE_URL", "http://127.0.0.1:9000").rstrip("/")
BASELINE_PLIST = "/opt/nanomdm-deployment/mobileconfig/baseline.mobileconfig"
DEVICES_CSV = "/opt/nanomdm-deployment/devices.csv"
GROUPS_JSON = "/opt/nanomdm-deployment/groups.json"
MOBILECONFIG_DIR = "/opt/nanomdm-deployment/mobileconfig"
# VPP Token檔案路徑改成可從.env的VPP_TOKEN_PATH覆寫,沒設定的話才用這個通用檔名當後備,
# 避免這裡跟webui的config.py各自寫死一份、日後可能各自被改動卻沒有同步更新。
VPP_TOKEN_FILE = os.environ.get("VPP_TOKEN_PATH", "/opt/nanomdm-deployment/vpp_token.vpptoken")
LOG_PATH = "/opt/nanomdm-deployment/webhook-automation.log"
# 暫存「UDID -> 序號」對應關係的本機檔案（在 Authenticate 事件時寫入，
# 在 TokenUpdate 事件時讀取），不需要查詢任何資料庫
SERIAL_CACHE_FILE = "/opt/nanomdm-deployment/udid-serial-cache.json"
SERIAL_CACHE_LOCK_FILE = "/opt/nanomdm-deployment/udid-serial-cache.lock"
# =====================================


def log(msg):
    line = f"{datetime.datetime.now()} | {msg}\n"
    print(line, end="")
    with open(LOG_PATH, "a") as f:
        f.write(line)


def load_serial_cache() -> dict:
    if not os.path.exists(SERIAL_CACHE_FILE):
        return {}
    try:
        with open(SERIAL_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_serial_cache(cache: dict):
    with open(SERIAL_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def update_serial_cache(udid: str, serial: str):
    """把「讀取現有暫存 -> 加入這台裝置的序號 -> 寫回」整個流程包在檔案鎖裡執行。
    多台裝置同時觸發 Authenticate 事件時,如果各自獨立呼叫 load_serial_cache()
    再各自呼叫 save_serial_cache(),會發生「後寫入的把先寫入的蓋掉」的競爭問題
    (A讀到{} -> B也讀到{} -> A寫回{A} -> B拿著舊的{}寫回{B},A的紀錄就這樣不見了),
    這正是「多台裝置同時重置註冊時,部分裝置查無序號、被直接略過自動化」的根本原因。
    用 fcntl 檔案鎖確保同一時間只有一個執行緒能做這個「讀取-修改-寫入」的動作。
    """
    with open(SERIAL_CACHE_LOCK_FILE, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            cache = load_serial_cache()
            cache[udid] = serial
            save_serial_cache(cache)
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def handle_authenticate(raw_payload_b64: str, udid: str):
    """解析 Authenticate 事件的 raw_payload，取出序號並暫存"""
    try:
        raw = base64.b64decode(raw_payload_b64)
        parsed = plistlib.loads(raw)
        serial = parsed.get("SerialNumber")
        if serial and udid:
            update_serial_cache(udid, serial)
            log(f"Authenticate 事件：udid={udid} 對應序號={serial}，已暫存")
    except Exception as e:
        log(f"解析 Authenticate raw_payload 失敗: {e}")


def get_serial_by_udid(udid: str) -> str:
    """從本機暫存檔案取得序號，不查詢任何資料庫"""
    cache = load_serial_cache()
    return cache.get(udid)


def load_device_mapping(serial: str):
    """
    查詢 devices.csv 取得裝置名稱與群組。
    用 .get() 取代 row[...]，即使欄位缺漏或名稱不完全相符，
    也只會回傳空字串，不會讓整個 webhook 處理程序崩潰。
    """
    with open(DEVICES_CSV, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_serial = (row.get("serial_number") or "").strip()
            if row_serial == serial:
                device_name = (row.get("device_name") or "").strip()
                group = (row.get("group") or "").strip()
                return device_name, group
    return None, None


def load_group_apps(group: str):
    """
    讀取 groups.json 取得該群組要安裝的 App 清單。
    新格式為巢狀結構：{"group_a": {"description": "...", "apps": ["adamId1", "adamId2"]}}
    """
    if not group:
        return []
    try:
        with open(GROUPS_JSON, encoding="utf-8") as f:
            groups = json.load(f)

        group_data = groups.get(group)
        if not group_data:
            return []

        # 相容新舊兩種格式：
        # 新格式：{"apps": [...]}（巢狀 dict）
        # 舊格式：直接是 [...]（純陣列），避免舊資料還沒轉換時直接壞掉
        if isinstance(group_data, dict):
            apps = group_data.get("apps", [])
        elif isinstance(group_data, list):
            apps = group_data
        else:
            apps = []

        return apps
    except Exception as e:
        log(f"讀取 groups.json 失敗: {e}")
        return []


def load_group_mobileconfig(group: str):
    """
    讀取 groups.json 取得該群組綁定的描述檔(mobileconfig)檔名,再讀出實際檔案內容。
    找不到群組、群組沒有綁定描述檔、或檔案不存在,都回傳 None(呼叫端會直接略過這個步驟)。
    """
    if not group:
        return None
    try:
        with open(GROUPS_JSON, encoding="utf-8") as f:
            groups = json.load(f)

        group_data = groups.get(group)
        if not isinstance(group_data, dict):
            return None

        filename = group_data.get("mobileconfig")
        if not filename:
            return None

        full_path = os.path.join(MOBILECONFIG_DIR, filename)
        if not os.path.exists(full_path):
            log(f"群組 {group} 綁定的描述檔 {filename} 實際上不存在於 {MOBILECONFIG_DIR}")
            return None

        with open(full_path, "rb") as f:
            return f.read()
    except Exception as e:
        log(f"讀取群組 {group} 的描述檔失敗: {e}")
        return None


def enqueue_command(udid: str, plist_content: str):
    """把 plist 內容直接送到 NanoMDM enqueue endpoint"""
    result = subprocess.run(
        ["curl", "-s", "-u", f"nanomdm:{NANOMDM_API_KEY}",
         "-T", "-", f"{NANOMDM_URL}/v1/enqueue/{udid}"],
        input=plist_content.encode("utf-8"),
        capture_output=True
    )
    log(f"enqueue 回應: {result.stdout.decode(errors='replace')}")
    return result.returncode == 0


def build_rename_plist(device_name: str) -> str:
    cmd_uuid = str(uuid.uuid4()).upper()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Command</key>
    <dict>
        <key>RequestType</key>
        <string>Settings</string>
        <key>Settings</key>
        <array>
            <dict>
                <key>Item</key>
                <string>DeviceName</string>
                <key>DeviceName</key>
                <string>{device_name}</string>
            </dict>
        </array>
    </dict>
    <key>CommandUUID</key>
    <string>{cmd_uuid}</string>
</dict>
</plist>"""


def build_install_app_plist(adam_id: str) -> str:
    cmd_uuid = str(uuid.uuid4()).upper()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Command</key>
    <dict>
        <key>RequestType</key>
        <string>InstallApplication</string>
        <key>iTunesStoreID</key>
        <integer>{adam_id}</integer>
        <key>Options</key>
        <dict>
            <key>PurchaseMethod</key>
            <integer>1</integer>
        </dict>
    </dict>
    <key>CommandUUID</key>
    <string>{cmd_uuid}</string>
</dict>
</plist>"""


def build_install_profile_plist(profile_content: bytes) -> str:
    cmd_uuid = str(uuid.uuid4()).upper()
    encoded = base64.b64encode(profile_content).decode("ascii")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Command</key>
    <dict>
        <key>RequestType</key>
        <string>InstallProfile</string>
        <key>Payload</key>
        <data>{encoded}</data>
    </dict>
    <key>CommandUUID</key>
    <string>{cmd_uuid}</string>
</dict>
</plist>"""


def assign_vpp_license(serial: str, adam_id: str):
    with open(VPP_TOKEN_FILE, "r") as f:
        stoken = f.read().strip()

    result = subprocess.run(
        ["curl", "-s", "-X", "POST",
         "https://vpp.itunes.apple.com/mdm/manageVPPLicensesByAdamIdSrv",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({
             "sToken": stoken,
             "adamIdStr": adam_id,
             "pricingParam": "STDQ",
             "associateSerialNumbers": [serial]
         })],
        capture_output=True
    )
    log(f"VPP 授權指派 (adamId={adam_id}, serial={serial}): {result.stdout.decode(errors='replace')}")


def process_enrollment(udid: str):
    log(f"開始處理裝置註冊自動化: udid={udid}")

    # 稍微延遲，確保 Authenticate 事件已經處理完成、序號已寫入暫存檔案
    time.sleep(2)

    serial = get_serial_by_udid(udid)
    if not serial:
        log(f"查無序號對應，udid={udid}，略過自動化")
        return

    device_name, group = load_device_mapping(serial)
    if not device_name:
        log(f"序號 {serial} 不在 devices.csv 對照表中，略過自動化")
        return

    log(f"序號={serial}, 名稱={device_name}, 群組={group}")

    # 1. 改名
    enqueue_command(udid, build_rename_plist(device_name))
    time.sleep(2)

    # 2. 派送 baseline
    with open(BASELINE_PLIST, "rb") as f:
        baseline_content = f.read()
    enqueue_command(udid, build_install_profile_plist(baseline_content))
    time.sleep(2)

    # 3. 群組專屬描述檔(webclip/WiFi/限制設定等,如果群組有綁定的話)
    group_mobileconfig = load_group_mobileconfig(group)
    if group_mobileconfig:
        enqueue_command(udid, build_install_profile_plist(group_mobileconfig))
        time.sleep(2)
    else:
        log(f"群組 {group} 沒有綁定描述檔,或找不到對應檔案,略過這個步驟")

    # 4. 群組專屬 App：先指派 VPP 授權，再送安裝指令
    apps = load_group_apps(group)
    log(f"群組 {group} 對應 {len(apps)} 個 App: {apps}")
    for adam_id in apps:
        assign_vpp_license(serial, adam_id)
        time.sleep(1)
        enqueue_command(udid, build_install_app_plist(adam_id))
        time.sleep(2)

    log(f"裝置 {serial} ({device_name}) 自動化流程完成")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""

        try:
            payload = json.loads(body)
        except Exception as e:
            log(f"webhook body 解析失敗: {e}")
            self.send_response(200)
            self.end_headers()
            return

        topic = payload.get("topic")
        checkin = payload.get("checkin_event") or {}
        udid = checkin.get("udid")
        tally = checkin.get("token_update_tally")
        raw_payload_b64 = checkin.get("raw_payload")

        log(f"收到 webhook: topic={topic}, udid={udid}, tally={tally}")

        try:
            # Authenticate 事件：解析出序號，暫存起來（此時裝置尚未 ready，不觸發自動化）
            if topic == "mdm.Authenticate" and udid and raw_payload_b64:
                handle_authenticate(raw_payload_b64, udid)

            # 只在「第一次」TokenUpdate（代表剛完成註冊）觸發自動化
            elif topic == "mdm.TokenUpdate" and str(tally) == "1" and udid:
                process_enrollment(udid)
        except Exception as e:
            # 保護整個 handler，避免任何未預期的錯誤讓 webhook 伺服器整個崩潰
            log(f"處理 webhook 時發生未預期錯誤: {e}")

        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # 關閉預設的雜訊 log，改用自訂的 log()


if __name__ == "__main__":
    # 用 ThreadingHTTPServer(而不是單執行緒的 HTTPServer):
    # process_enrollment() 裡有好幾個 time.sleep() 加上多次網路呼叫,單一裝置處理起來
    # 可能要十幾秒。如果伺服器是單執行緒,這段時間內另一台裝置的 webhook(不管是
    # Authenticate 還是 TokenUpdate)完全無法被接收,只能卡在 TCP 連線佇列裡等,
    # 等到 nanomdm 端逾時放棄就直接消失、不會重試。改成多執行緒後,每台裝置的
    # webhook 各自在自己的執行緒裡處理,不會互相卡住。
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8092), Handler)
    log("Webhook 自動化伺服器啟動於 127.0.0.1:8092 (多執行緒模式)")
    server.serve_forever()
