# -*- coding: utf-8 -*-
"""
ASM 裝置 / MDM Server 管理:
- 透過 nanoAXM 呼叫 Apple Business/School Manager API(新版 2025 API,OAuth2/Key驗證,
  由 nanoAXM 的 reverse proxy 轉發),跟 nanodep(classic DEP)、nanomdm 完全是不同系統。
- 認證方式是 HTTP Basic,使用者名稱固定是 "nanoaxm"(依照使用者提供的實際運作範例)。
"""
import os
import time

import requests

POLL_INTERVAL_SECONDS = 2
POLL_MAX_ATTEMPTS = 30  # 最多等 60 秒


def normalize_mac(raw):
    """把MAC地址正規化成標準格式 XX:XX:XX:XX:XX:XX(大寫、冒號分隔)。
    Apple回傳的格式可能用冒號、破折號或完全沒有分隔符,這裡一律重新格式化,
    格式不對(長度不是12個hex字元)就原樣回傳,不強行處理避免顯示錯誤資料。
    """
    if not raw:
        return ""
    hex_only = "".join(c for c in raw if c in "0123456789abcdefABCDEF")
    if len(hex_only) != 12:
        return raw  # 格式不符預期,保留原始值,不要顯示可能誤導的假資料
    pairs = [hex_only[i:i + 2] for i in range(0, 12, 2)]
    return ":".join(pairs).upper()


class AsmError(Exception):
    pass


def _auth(api_key):
    return ("nanoaxm", api_key)


def _get(url, api_key, params=None, timeout=30):
    try:
        resp = requests.get(url, auth=_auth(api_key), params=params, timeout=timeout)
    except requests.RequestException as e:
        raise AsmError(f"連線失敗: {e}")
    if resp.status_code >= 400:
        raise AsmError(f"HTTP {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except ValueError:
        raise AsmError(f"回應不是合法JSON: {resp.text}")


def _post(url, api_key, json_body, timeout=30):
    try:
        resp = requests.post(url, auth=_auth(api_key), json=json_body,
                              headers={"Content-Type": "application/json"}, timeout=timeout)
    except requests.RequestException as e:
        raise AsmError(f"連線失敗: {e}")
    if resp.status_code >= 400:
        raise AsmError(f"HTTP {resp.status_code}: {resp.text}")
    try:
        return resp.json()
    except ValueError:
        raise AsmError(f"回應不是合法JSON: {resp.text}")


def fetch_all_pages(base_url, api_key, path):
    """依照使用者提供範例的分頁邏輯(cursor-based),抓取某個 v1 端點的所有 data"""
    all_items = []
    cursor = None
    url = f"{base_url.rstrip('/')}{path}"

    while True:
        params = {"cursor": cursor} if cursor else None
        data = _get(url, api_key, params=params)
        items = data.get("data", [])
        all_items.extend(items)

        cursor = data.get("meta", {}).get("paging", {}).get("nextCursor")
        if not cursor:
            break

    return all_items


def _proxy_path(org_type, axm_name, suffix):
    return f"/proxy/{org_type}/{axm_name}{suffix}"


def fetch_mdm_servers(base_url, api_key, org_type, axm_name):
    return fetch_all_pages(base_url, api_key, _proxy_path(org_type, axm_name, "/v1/mdmServers"))


def fetch_org_devices(base_url, api_key, org_type, axm_name):
    return fetch_all_pages(base_url, api_key, _proxy_path(org_type, axm_name, "/v1/orgDevices"))


def get_server_detail(base_url, api_key, org_type, axm_name, server_id):
    url = f"{base_url.rstrip('/')}{_proxy_path(org_type, axm_name, f'/v1/mdmServers/{server_id}')}"
    return _get(url, api_key)


def get_device_assigned_server(base_url, api_key, org_type, axm_name, device_id):
    url = f"{base_url.rstrip('/')}{_proxy_path(org_type, axm_name, f'/v1/orgDevices/{device_id}/relationships/assignedServer')}"
    return _get(url, api_key)


def unassign_devices(base_url, api_key, org_type, axm_name, current_server_id, device_ids):
    """建立一個 orgDeviceActivities 解除指派作業(裝置留在ASM名冊裡,只是不再指派給任何MDM伺服器,
    跟「從組織釋出」是兩回事,可逆)。
    activityType用「UNASSIGN_DEVICES」已經實際送出去、確認Apple有認得這個字串(沒有回報activityType
    不合法的錯誤);但Apple會回報 ENTITY_ERROR.RELATIONSHIP.REQUIRED,要求就算是解除指派也要附上
    目前裝置指派所在的 mdmServer 關聯(可能是用來確認要從哪一台伺服器解除),這裡照實際錯誤訊息補上。
    current_server_id 是裝置目前指派所在的MDM伺服器ID(從ASM裝置快取查表取得)。
    回傳 Apple 建立的 activity 資源(含id與初始狀態),非同步作業,要另外用 activity id 輪詢。
    """
    url = f"{base_url.rstrip('/')}{_proxy_path(org_type, axm_name, '/v1/orgDeviceActivities')}"
    body = {
        "data": {
            "type": "orgDeviceActivities",
            "attributes": {"activityType": "UNASSIGN_DEVICES"},
            "relationships": {
                "mdmServer": {"data": {"type": "mdmServers", "id": current_server_id}},
                "devices": {"data": [{"type": "orgDevices", "id": d} for d in device_ids]},
            },
        }
    }
    return _post(url, api_key, body)


def reassign_devices(base_url, api_key, org_type, axm_name, target_server_id, device_ids):
    """建立一個 orgDeviceActivities 改派作業,回傳 Apple 建立的 activity 資源(含id與初始狀態)。
    這是非同步作業,實際完成狀態要另外用 activity id 輪詢。
    """
    url = f"{base_url.rstrip('/')}{_proxy_path(org_type, axm_name, '/v1/orgDeviceActivities')}"
    body = {
        "data": {
            "type": "orgDeviceActivities",
            "attributes": {"activityType": "ASSIGN_DEVICES"},
            "relationships": {
                "mdmServer": {"data": {"type": "mdmServers", "id": target_server_id}},
                "devices": {"data": [{"type": "orgDevices", "id": d} for d in device_ids]},
            },
        }
    }
    return _post(url, api_key, body)


def get_activity_status(base_url, api_key, org_type, axm_name, activity_id):
    url = f"{base_url.rstrip('/')}{_proxy_path(org_type, axm_name, f'/v1/orgDeviceActivities/{activity_id}')}"
    return _get(url, api_key)


def is_activity_in_progress(status_text):
    if not status_text:
        return True
    return any(kw in status_text.upper() for kw in ("PROCESSING", "PENDING", "IN_PROGRESS", "IN-PROGRESS", "RUNNING"))


def poll_activity_until_done(base_url, api_key, org_type, axm_name, activity_id,
                              max_attempts=POLL_MAX_ATTEMPTS, interval=POLL_INTERVAL_SECONDS):
    """輪詢直到狀態不再是「處理中」,逐次yield目前狀態,供SSE串流使用。
    Apple 回傳的確切狀態字串枚舉值我們沒有 100% 把握(官方文件無法直接抓到內容),
    所以判斷邏輯用關鍵字寬鬆比對「還在處理中」,其餘一律視為已結束,
    並把 Apple 原始回應內容整包顯示給使用者自行判讀,不做過度武斷的成功/失敗宣告。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            data = get_activity_status(base_url, api_key, org_type, axm_name, activity_id)
        except AsmError as e:
            yield {"attempt": attempt, "error": str(e), "done": False}
            time.sleep(interval)
            continue

        status_text = data.get("data", {}).get("attributes", {}).get("status", "")
        in_progress = is_activity_in_progress(status_text)
        yield {"attempt": attempt, "status": status_text, "data": data, "done": not in_progress}

        if not in_progress:
            return
        time.sleep(interval)

    yield {"attempt": max_attempts, "status": "TIMEOUT", "done": True,
           "message": f"輪詢 {max_attempts} 次後仍未結束,請直接到 ASM 網站確認實際結果"}


def fetch_server_device_ids(base_url, api_key, org_type, axm_name, server_id):
    """查詢某個 MDM Server 底下所有裝置的 id 清單(GET /v1/mdmServers/{id}/relationships/devices,
    支援cursor分頁)。這是效率最好的方式:對每個「伺服器」查一次,而不是對每個「裝置」查一次。
    回傳結果只有 resource identifier({"type":"orgDevices","id":...}),不含完整屬性。
    """
    path = _proxy_path(org_type, axm_name, f"/v1/mdmServers/{server_id}/relationships/devices")
    items = fetch_all_pages(base_url, api_key, path)
    return [item["id"] for item in items if "id" in item]


def build_asm_overview_stream(base_url, api_key, org_type, axm_name):
    """跟 build_asm_overview 邏輯一樣,但用 generator 逐步 yield 進度訊息,
    最後一次 yield 附上完整結果,供 SSE 手動重新整理時使用。
    """
    yield {"message": "正在抓取 MDM Server 清單..."}
    servers = fetch_mdm_servers(base_url, api_key, org_type, axm_name)
    yield {"message": f"共 {len(servers)} 台 MDM Server,正在抓取所有裝置清單..."}

    devices = fetch_org_devices(base_url, api_key, org_type, axm_name)
    device_by_id = {d["id"]: d for d in devices}
    yield {"message": f"共 {len(devices)} 台裝置,開始逐一查詢每台伺服器底下的裝置歸屬..."}

    def to_device_row(device_id):
        d = device_by_id.get(device_id)
        if not d:
            return None
        attrs = d.get("attributes", {})
        return {
            "id": d["id"], "serialNumber": attrs.get("serialNumber", ""),
            "deviceModel": attrs.get("deviceModel", ""), "color": attrs.get("color", ""),
            "status": attrs.get("status", ""), "wifiMacAddress": normalize_mac(attrs.get("wifiMacAddress", "")),
        }

    server_rows = []
    device_by_server = {}
    assigned_ids = set()

    for s in servers:
        attrs = s.get("attributes", {})
        server_name = attrs.get("serverName", "")
        yield {"message": f"查詢伺服器「{server_name}」底下的裝置..."}

        device_ids = fetch_server_device_ids(base_url, api_key, org_type, axm_name, s["id"])
        rows = [row for did in device_ids if (row := to_device_row(did)) is not None]
        device_by_server[s["id"]] = rows
        assigned_ids.update(device_ids)

        server_rows.append({
            "id": s["id"], "serverName": server_name,
            "serverType": attrs.get("serverType", ""), "status": attrs.get("status", ""),
            "device_count": len(rows),
        })

    unassigned_devices = [to_device_row(d["id"]) for d in devices if d["id"] not in assigned_ids]
    yield {
        "message": f"完成,共 {len(unassigned_devices)} 台裝置尚未指派",
        "done": True,
        "server_rows": server_rows,
        "device_by_server": device_by_server,
        "unassigned_devices": unassigned_devices,
    }


def write_asm_devices_cache(servers_csv_path, devices_csv_path, server_rows, device_by_server, unassigned_devices):
    """寫入兩份 UTF-8 with BOM 的 CSV,沿用使用者原本 all_asm_server.csv / all_asm_devices.csv 的命名慣例。"""
    import csv as _csv

    tmp_servers = servers_csv_path + ".tmp"
    with open(tmp_servers, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.writer(f)
        writer.writerow(["id", "serverName", "serverType", "status", "裝置數量"])
        for s in server_rows:
            writer.writerow([s["id"], s["serverName"], s["serverType"], s["status"], s["device_count"]])
    os.replace(tmp_servers, servers_csv_path)

    tmp_devices = devices_csv_path + ".tmp"
    with open(tmp_devices, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.writer(f)
        writer.writerow(["id", "serialNumber", "deviceModel", "color", "status", "wifiMacAddress", "assigned_server_id"])
        for server_id, rows in device_by_server.items():
            for d in rows:
                writer.writerow([d["id"], d["serialNumber"], d["deviceModel"], d["color"], d["status"], d.get("wifiMacAddress", ""), server_id])
        for d in unassigned_devices:
            writer.writerow([d["id"], d["serialNumber"], d["deviceModel"], d["color"], d["status"], d.get("wifiMacAddress", ""), ""])
    os.replace(tmp_devices, devices_csv_path)


def read_asm_devices_cache(servers_csv_path, devices_csv_path):
    """讀回快取的兩份CSV,重建成 (server_rows, device_by_server, unassigned_devices, mtime)。
    檔案不存在時回傳 ([], {}, [], None)
    """
    import csv as _csv

    if not os.path.exists(servers_csv_path) or not os.path.exists(devices_csv_path):
        return [], {}, [], None

    server_rows = []
    with open(servers_csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in _csv.DictReader(f):
            server_rows.append({
                "id": row["id"], "serverName": row["serverName"], "serverType": row["serverType"],
                "status": row["status"], "device_count": int(row["裝置數量"] or 0),
            })

    device_by_server = {s["id"]: [] for s in server_rows}
    unassigned_devices = []
    with open(devices_csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in _csv.DictReader(f):
            device_row = {
                "id": row["id"], "serialNumber": row["serialNumber"], "deviceModel": row["deviceModel"],
                "color": row["color"], "status": row["status"], "wifiMacAddress": row.get("wifiMacAddress", ""),
            }
            server_id = row.get("assigned_server_id", "")
            if server_id:
                device_by_server.setdefault(server_id, []).append(device_row)
            else:
                unassigned_devices.append(device_row)

    mtime = os.path.getmtime(devices_csv_path)
    return server_rows, device_by_server, unassigned_devices, mtime


def export_editable_csv(path, server_rows, device_by_server, unassigned_devices):
    """匯出給使用者編輯用的CSV:序號/型號/顏色/目前指派的MDM伺服器名稱。
    使用者可以直接修改「指派的MDM伺服器」欄位,改完後重新上傳,系統會自動比對差異。
    """
    import csv as _csv

    server_name_by_id = {s["id"]: s["serverName"] for s in server_rows}
    rows = []
    for server_id, devices in device_by_server.items():
        server_name = server_name_by_id.get(server_id, "")
        for d in devices:
            rows.append((d["serialNumber"], d["deviceModel"], d["color"], server_name))
    for d in unassigned_devices:
        rows.append((d["serialNumber"], d["deviceModel"], d["color"], ""))

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.writer(f)
        writer.writerow(["序號", "型號", "顏色", "指派的MDM伺服器"])
        for row in rows:
            writer.writerow(row)
    os.replace(tmp, path)


def parse_editable_csv(file_content_text):
    import csv as _csv
    import io

    reader = _csv.DictReader(io.StringIO(file_content_text))
    rows = []
    for row in reader:
        rows.append({
            "serialNumber": (row.get("序號") or "").strip(),
            "deviceModel": (row.get("型號") or "").strip(),
            "color": (row.get("顏色") or "").strip(),
            "target_server_name": (row.get("指派的MDM伺服器") or "").strip(),
        })
    return rows


def diff_editable_import(uploaded_rows, server_rows, device_by_server, unassigned_devices):
    """比對上傳的CSV跟目前快取狀態,找出「指派的MDM伺服器」欄位有變更的裝置。
    回傳 changes list,每筆包含 matched(是否能對應到實際存在的伺服器)。
    """
    server_id_by_name = {s["serverName"]: s["id"] for s in server_rows}
    server_name_by_id = {s["id"]: s["serverName"] for s in server_rows}

    current_by_serial = {}
    for server_id, devices in device_by_server.items():
        server_name = server_name_by_id.get(server_id, "")
        for d in devices:
            current_by_serial[d["serialNumber"]] = (d["id"], server_name)
    for d in unassigned_devices:
        current_by_serial[d["serialNumber"]] = (d["id"], "")

    changes = []
    for row in uploaded_rows:
        serial = row["serialNumber"]
        target_name = row["target_server_name"]
        if serial not in current_by_serial:
            continue  # CSV裡的序號在目前快取找不到,可能是打錯或裝置已被移除,略過
        device_id, current_name = current_by_serial[serial]
        if target_name == current_name:
            continue  # 沒有變更

        if not target_name:
            # 使用者把伺服器欄位清空,想取消指派 - 目前nanoAXM沒有已知的取消指派端點,標記為無法處理
            changes.append({
                "serialNumber": serial, "device_id": device_id,
                "current_server_name": current_name, "target_server_name": "",
                "target_server_id": None, "matched": False,
                "reason": "此工具目前不支援取消指派,請填入實際的伺服器名稱",
            })
            continue

        target_id = server_id_by_name.get(target_name)
        changes.append({
            "serialNumber": serial, "device_id": device_id,
            "current_server_name": current_name, "target_server_name": target_name,
            "target_server_id": target_id, "matched": bool(target_id),
            "reason": None if target_id else f"找不到名稱為「{target_name}」的伺服器,請確認拼字",
        })
    return changes


def build_asm_overview(base_url, api_key, org_type, axm_name):
    """完整組出 ASM 裝置頁需要的資料:對每個伺服器查詢其裝置ID清單,
    再跟完整的orgDevices清單比對取得序號/型號等屬性,沒有出現在任何伺服器清單裡的就是未指派。
    回傳 (server_rows, device_by_server, unassigned_devices)
    """
    servers = fetch_mdm_servers(base_url, api_key, org_type, axm_name)
    devices = fetch_org_devices(base_url, api_key, org_type, axm_name)
    device_by_id = {d["id"]: d for d in devices}

    def to_device_row(device_id):
        d = device_by_id.get(device_id)
        if not d:
            return None
        attrs = d.get("attributes", {})
        return {
            "id": d["id"],
            "serialNumber": attrs.get("serialNumber", ""),
            "deviceModel": attrs.get("deviceModel", ""),
            "color": attrs.get("color", ""),
            "status": attrs.get("status", ""),
            "wifiMacAddress": normalize_mac(attrs.get("wifiMacAddress", "")),
        }

    server_rows = []
    device_by_server = {}
    assigned_ids = set()

    for s in servers:
        device_ids = fetch_server_device_ids(base_url, api_key, org_type, axm_name, s["id"])
        rows = [row for did in device_ids if (row := to_device_row(did)) is not None]
        device_by_server[s["id"]] = rows
        assigned_ids.update(device_ids)

        attrs = s.get("attributes", {})
        server_rows.append({
            "id": s["id"],
            "serverName": attrs.get("serverName", ""),
            "serverType": attrs.get("serverType", ""),
            "status": attrs.get("status", ""),
            "device_count": len(rows),
        })

    unassigned_devices = [
        to_device_row(d["id"]) for d in devices if d["id"] not in assigned_ids
    ]

    return server_rows, device_by_server, unassigned_devices
