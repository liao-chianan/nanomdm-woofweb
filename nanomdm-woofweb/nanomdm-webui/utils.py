# -*- coding: utf-8 -*-
"""
共用工具函式:
- .env 檔案讀寫
- devices.csv 讀寫
- groups.json 讀寫
- 執行外部指令 (docker exec mysql / dep-*.sh / check_vpp_license.sh)
- 組出 MDM Command plist 並透過 nanomdm API 送出
"""
import base64
import csv
import datetime
import fcntl
import io
import json
import os
import stat
import re
import subprocess
import uuid
import plistlib

import requests

VALID_NAME_RE = re.compile(r'^[^\x00-\x1f\x7f,"]{1,64}$')


# ---------------------------------------------------------------------------
# .env 檔案處理
# ---------------------------------------------------------------------------
def read_env_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_env_file(path, content):
    # 寫入前備份一份,避免手殘寫壞
    if os.path.exists(path):
        backup_path = path + ".bak"
        with open(path, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def parse_env_dict(path):
    """把 .env 解析成 dict,方便程式內部取用 (例如 NANOMDM_API_KEY)"""
    result = {}
    content = read_env_file(path)
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        result[k] = v
    return result


# ---------------------------------------------------------------------------
# devices.csv 處理 (欄位: serial_number, device_name, group)
# ---------------------------------------------------------------------------
CSV_FIELDS = ["serial_number", "device_name", "group", "wifi_mac"]


def read_devices_csv(path):
    """回傳 { serial_number: {device_name, group, wifi_mac} }"""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sn = (row.get("serial_number") or "").strip()
            if not sn:
                continue
            result[sn] = {
                "device_name": (row.get("device_name") or "").strip(),
                "group": (row.get("group") or "").strip(),
                "wifi_mac": (row.get("wifi_mac") or "").strip(),
            }
    return result


def read_devices_csv_raw(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_devices_csv(path, devices_dict):
    """devices_dict: { serial_number: {device_name, group, wifi_mac} }, 完整覆寫檔案"""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for sn, info in sorted(devices_dict.items()):
            writer.writerow({
                "serial_number": sn,
                "device_name": info.get("device_name", ""),
                "group": info.get("group", ""),
                "wifi_mac": info.get("wifi_mac", ""),
            })
    os.replace(tmp_path, path)


def upsert_device_row(path, serial_number, device_name, group, wifi_mac=None):
    """wifi_mac 是選填的:傳 None 代表「不異動」,沿用這台裝置目前已經記錄的值,
    這樣像「所有裝置與命令」這種只存裝置名稱/群組的頁面,不會不小心把wifi_mac清空。
    """
    device_name = device_name or ""
    group = group or ""
    # 空字串代表「未命名」/「未分類」,是合法狀態;非空時才需要檢查格式
    if device_name and not VALID_NAME_RE.match(device_name):
        raise ValueError("裝置名稱不可包含逗號、雙引號或控制字元,且長度需在 1~64 字元內")
    if group and not VALID_NAME_RE.match(group):
        raise ValueError("群組不可包含逗號、雙引號或控制字元,且長度需在 1~64 字元內")
    devices = read_devices_csv(path)
    existing_wifi_mac = devices.get(serial_number, {}).get("wifi_mac", "")
    devices[serial_number] = {
        "device_name": device_name,
        "group": group,
        "wifi_mac": wifi_mac if wifi_mac is not None else existing_wifi_mac,
    }
    write_devices_csv(path, devices)
    return devices[serial_number]


def delete_device_row(path, serial_number):
    """從devices.csv整筆移除某台裝置的記錄(裝置退場用,跟upsert不同,這裡是真的刪掉整列)。"""
    devices = read_devices_csv(path)
    if serial_number in devices:
        del devices[serial_number]
        write_devices_csv(path, devices)
    return True


def delete_devices_status_row(path, serial_number):
    """從devices-status.csv整筆移除某台裝置的快取狀態(電量/容量/系統版本/定位等)。"""
    cache, _ = read_devices_status_cache(path)
    if serial_number in cache:
        del cache[serial_number]
        write_devices_status_cache(path, list(cache.values()))
    return True


def find_group_by_paired_file(groups_dict, field_name, filename):
    """反查:目前是哪個群組正在使用這個檔案(field_name是'enroll_json'或'mobileconfig')"""
    for name, info in groups_dict.items():
        if info.get(field_name) == filename:
            return name
    return None


def set_group_paired_file(groups_path, group_name, field_name, filename):
    """把某個群組的enroll_json或mobileconfig指到filename,並強制1:1:
    如果原本有其他群組指向同一個檔案,自動清除那個群組的配對。
    group_name可以是None,代表只是要清除某個檔案目前被誰佔用(不指派給任何群組)。
    """
    groups = load_groups(groups_path)

    # 先清掉「目前佔用這個檔案」的舊群組(如果跟目標群組不同的話)
    occupying_group = find_group_by_paired_file(groups, field_name, filename)
    if occupying_group and occupying_group != group_name:
        groups[occupying_group][field_name] = None

    if group_name:
        if group_name not in groups:
            raise ValueError(f"找不到群組 {group_name}")
        groups[group_name][field_name] = filename

    save_groups(groups_path, groups)
    return occupying_group if occupying_group != group_name else None


def clear_group_paired_file(groups_path, field_name, filename):
    """檔案被刪除時,清掉所有群組對它的引用"""
    groups = load_groups(groups_path)
    changed = False
    for name, info in groups.items():
        if info.get(field_name) == filename:
            info[field_name] = None
            changed = True
    if changed:
        save_groups(groups_path, groups)


def rename_group_paired_file(groups_path, field_name, old_filename, new_filename):
    """檔案被複製/改名時,如果有群組正在引用舊檔名,同步更新成新檔名(目前複製功能不會用到,保留給未來改名用)"""
    groups = load_groups(groups_path)
    changed = False
    for name, info in groups.items():
        if info.get(field_name) == old_filename:
            info[field_name] = new_filename
            changed = True
    if changed:
        save_groups(groups_path, groups)


def write_device_enrollment_export_csv(path, rows):
    """匯出給使用者編輯用的CSV(UTF-8 with BOM):序號/裝置名稱/群組/WiFi MAC/DEP profile_uuid/MDM UUID。
    後兩欄是拿來做匯入時的一致性比對用,不建議使用者自行修改。
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["序號", "裝置名稱", "群組", "WiFi_MAC", "DEP_profile_uuid", "MDM_UUID"])
        for r in rows:
            writer.writerow([
                r.get("serial_number", ""), r.get("device_name", ""), r.get("group", ""),
                r.get("wifi_mac", ""), r.get("profile_uuid", ""), r.get("enrollment_id", ""),
            ])
    os.replace(tmp_path, path)


def parse_device_enrollment_import_csv(content_text):
    reader = csv.DictReader(io.StringIO(content_text))
    rows = []
    for row in reader:
        rows.append({
            "serial_number": (row.get("序號") or "").strip(),
            "device_name": (row.get("裝置名稱") or "").strip(),
            "group": (row.get("群組") or "").strip(),
            "wifi_mac": (row.get("WiFi_MAC") or "").strip(),
            "profile_uuid": (row.get("DEP_profile_uuid") or "").strip(),
            "enrollment_id": (row.get("MDM_UUID") or "").strip(),
        })
    return rows


def diff_device_enrollment_import(uploaded_rows, current_rows, groups_dict):
    """比對上傳的CSV跟目前即時狀態的差異。
    會先驗證 DEP profile_uuid 與 MDM UUID 是否跟目前狀態一致(避免拿舊資料誤蓋新狀態),
    不一致的直接標記成 mismatch 不會被套用;群組欄位也會驗證是否為目前存在的群組。
    回傳 (changes, mismatches) 兩個 list。
    """
    current_by_serial = {r["serial_number"]: r for r in current_rows}
    changes = []
    mismatches = []

    for row in uploaded_rows:
        serial = row["serial_number"]
        if not serial:
            continue
        current = current_by_serial.get(serial)
        if not current:
            mismatches.append({**row, "reason": "目前系統裡找不到這個序號(可能已被移除)"})
            continue

        if row["profile_uuid"] != (current.get("profile_uuid") or ""):
            mismatches.append({**row, "reason": "DEP profile_uuid 跟目前狀態不一致,資料可能已過時,請重新匯出後再編輯"})
            continue
        if row["enrollment_id"] != (current.get("enrollment_id") or ""):
            mismatches.append({**row, "reason": "MDM UUID 跟目前狀態不一致,資料可能已過時,請重新匯出後再編輯"})
            continue
        if row["group"] and row["group"] not in groups_dict:
            mismatches.append({**row, "reason": f"群組「{row['group']}」不存在,請確認拼字或先建立這個群組"})
            continue

        name_changed = row["device_name"] != (current.get("device_name") or "")
        group_changed = row["group"] != (current.get("group") or "")
        if not name_changed and not group_changed:
            continue

        changes.append({
            "serial_number": serial,
            "device_name": row["device_name"],
            "group": row["group"],
            "wifi_mac": current.get("wifi_mac") or "",
            "enrollment_id": current.get("enrollment_id") or "",
            "name_changed": name_changed,
            "group_changed": group_changed,
            "old_device_name": current.get("device_name") or "",
            "old_group": current.get("group") or "",
        })

    return changes, mismatches


def parse_env_file_lines(path):
    """讀取 .env,保留原始行順序與註解,回傳list,每個元素是
    {'type':'kv','key':...,'value':...,'raw':原始行} 或 {'type':'raw','raw':原始行}(註解/空行)
    """
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            result.append({"type": "raw", "raw": line})
            continue
        key, _, value = line.partition("=")
        result.append({"type": "kv", "key": key.strip(), "value": value, "raw": line})
    return result


def env_fields_from_lines(lines):
    """轉成表單用的欄位清單: [{key, value}, ...],value 去掉前後空白與包住的引號"""
    fields = []
    for item in lines:
        if item["type"] == "kv":
            v = item["value"].strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            fields.append({"key": item["key"], "value": v})
    return fields


def backup_env_file(path, backup_dir=None):
    """建立備份:覆蓋單一的 .env.bak(快速還原用),並在 backup_dir 底下額外留一份時間戳記版本(保留歷史)。
    回傳這次時間戳記備份的檔案路徑(沒有 backup_dir 或原始檔不存在時回傳 None)。
    """
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    with open(path + ".bak", "w", encoding="utf-8") as f:
        f.write(content)

    if not backup_dir:
        return None
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = os.path.join(backup_dir, f".env.{ts}.bak")
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(content)
    return backup_path


def update_env_key(path, backup_dir, key, value):
    """只更新單一個 key 的值,保留檔案裡其他行(含註解、順序)原封不動。存檔前先備份。"""
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key or ""):
        raise ValueError("變數名稱格式不正確,只能包含英文字母、數字、底線,且不能以數字開頭")
    if "\n" in value or "\r" in value:
        raise ValueError("變數值不能包含換行字元")

    lines = parse_env_file_lines(path)
    backup_env_file(path, backup_dir)

    found = False
    new_lines = []
    for item in lines:
        if item["type"] == "kv" and item["key"] == key:
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(item["raw"])
    if not found:
        new_lines.append(f"{key}={value}")

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")
    os.replace(tmp_path, path)


def delete_env_key(path, backup_dir, key):
    """刪除.env裡的某個變數(整行移除),存檔前先備份"""
    lines = parse_env_file_lines(path)
    backup_env_file(path, backup_dir)

    new_lines = [item["raw"] for item in lines if not (item["type"] == "kv" and item["key"] == key)]

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + ("\n" if new_lines else ""))
    os.replace(tmp_path, path)


def list_env_backups(backup_dir):
    if not os.path.exists(backup_dir):
        return []
    results = []
    for fname in os.listdir(backup_dir):
        full_path = os.path.join(backup_dir, fname)
        if os.path.isfile(full_path):
            results.append({
                "filename": fname,
                "size": os.path.getsize(full_path),
                "mtime": os.path.getmtime(full_path),
            })
    results.sort(key=lambda r: r["mtime"], reverse=True)
    return results


def load_groups(path):
    """讀取 groups.json 成 dict 結構: { group_name: {description, apps, enroll_json, mobileconfig} }"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return {}
    # 補齊缺少的欄位,避免舊格式的 groups.json 缺欄位造成錯誤
    for name, info in data.items():
        if not isinstance(info, dict):
            data[name] = {"description": "", "apps": [], "enroll_json": None, "mobileconfig": None}
        else:
            info.setdefault("description", "")
            info.setdefault("apps", [])
            info.setdefault("enroll_json", None)
            info.setdefault("mobileconfig", None)
    return data


def save_groups(path, groups_dict):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as src, open(path + ".bak", "w", encoding="utf-8") as dst:
            dst.write(src.read())
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(groups_dict, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def query_and_merge_devices(mysql_cfg, db_password, devices_csv_path):
    """查詢 mysql enrollments 並與 devices.csv 合併,回傳 (rows, rc, err)
    供「所有裝置與命令」頁與「所有群組」頁共用。"""
    rows, rc, out, err = query_devices_from_mysql(mysql_cfg, db_password)
    if rc != 0:
        return [], rc, (err or out)

    csv_map = read_devices_csv(devices_csv_path)
    merged = []
    for row in rows:
        sn = row["serial_number"]
        extra = csv_map.get(sn, {"device_name": "", "group": "", "wifi_mac": ""})
        merged.append({
            "serial_number": sn,
            "enrollment_id": row["enrollment_id"],
            "device_name": extra.get("device_name", ""),
            "group": extra.get("group", ""),
            "wifi_mac": extra.get("wifi_mac", ""),
            "last_seen_at": row["last_seen_at"],
            "enabled": row["enabled"],
        })
    return merged, 0, None


# ---------------------------------------------------------------------------
# groups.json 處理
# ---------------------------------------------------------------------------
def read_groups_json_raw(path):
    if not os.path.exists(path):
        return "{}"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_groups_json_raw(path, content):
    # 驗證是合法 JSON 才寫入
    json.loads(content)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as src, open(path + ".bak", "w", encoding="utf-8") as dst:
            dst.write(src.read())
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# 外部指令執行 (subprocess, 不透過 shell 字串組合以避免 injection)
# ---------------------------------------------------------------------------
def build_subprocess_env(env_file_path, extra_env=None):
    """合併目前系統環境變數與 .env 檔案內容,供呼叫外部 script 時使用,
    這樣 check_vpp_license.sh、dep-*.sh 等 script 內部若有讀取
    .env 裡定義的變數(例如 VPP_TOKEN_PATH),才抓得到值。
    extra_env 可以額外注入呼叫端指定的變數(例如把 NANODEP_BASE_URL 對應轉成
    腳本實際需要的 BASE_URL 名稱),優先權最高,會覆蓋掉.env裡的同名變數。
    """
    merged = dict(os.environ)
    merged.update(parse_env_dict(env_file_path))
    if extra_env:
        merged.update(extra_env)
    return merged


def run_shell_command(cmd_str, timeout=30):
    """執行使用者在設定裡填的shell指令字串(例如重啟depsyncer的指令)。
    用shell=True讓使用者可以填類似 'docker restart xxx' 或帶管線/&&的複合指令。
    """
    if not cmd_str:
        return -1, "", "指令為空"
    try:
        proc = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"指令逾時: {e}"
    except Exception as e:
        return -1, "", f"執行失敗: {e}"


def run_cmd_with_stdin(args, stdin_text, timeout=30, env=None, cwd=None):
    """執行外部指令,並把 stdin_text 透過 stdin 餵給它(例如把憑證PEM內容餵給 openssl x509)"""
    try:
        proc = subprocess.run(
            args, input=stdin_text, capture_output=True, text=True,
            timeout=timeout, env=env, cwd=cwd,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"指令逾時: {e}"
    except FileNotFoundError as e:
        return -1, "", f"找不到指令: {e}"
    except Exception as e:
        return -1, "", f"執行失敗: {e}"


def run_docker_logs(container_name, tail=5000, timeout=30):
    """讀取 docker container 的 log。nanomdm 這類 Go 服務通常把結構化 log 寫到 stderr,
    所以 stdout/stderr 都要接住合併解析,不確定哪一串才有內容。
    """
    args = ["docker", "logs", "--tail", str(tail), container_name]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        combined = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, combined, ""
    except subprocess.TimeoutExpired as e:
        return -1, "", f"指令逾時: {e}"
    except FileNotFoundError as e:
        return -1, "", f"找不到指令: {e}"
    except Exception as e:
        return -1, "", f"執行失敗: {e}"


_LOGFMT_PATTERN = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def parse_logfmt_line(line):
    """解析 logfmt 格式的一行 log(key=value key2=\"value with space\" ...),回傳dict。"""
    result = {}
    for key, value in _LOGFMT_PATTERN.findall(line):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        result[key] = value
    return result


def extract_device_ips_from_nanomdm_logs(log_text):
    """把 nanomdm 的 log 依 trace_id 關聯起來:
    HTTP請求層的一行有 trace_id + x_forwarded_for(裝置實際連線的來源IP),
    service層的另一行有相同的 trace_id + id(裝置的 enrollment id/UDID)。
    兩行用 trace_id 兜起來,就能反推「這個裝置的這次連線是從哪個IP來的」。
    因為裝置會反覆連線,同一個 enrollment id 可能出現很多次,這裡只保留「最後(最新)一次」的IP,
    log本身是照時間順序輸出的,所以後面出現的自然會覆蓋掉前面的,不需要額外排序。
    回傳 { enrollment_id: {"ip": ..., "trace_id": ...} }
    """
    ip_by_trace_id = {}
    result = {}

    for line in log_text.splitlines():
        if "trace_id=" not in line:
            continue
        fields = parse_logfmt_line(line)
        trace_id = fields.get("trace_id")
        if not trace_id:
            continue

        if "x_forwarded_for" in fields and fields["x_forwarded_for"]:
            ip_by_trace_id[trace_id] = fields["x_forwarded_for"]

        device_id = fields.get("id")
        if device_id and trace_id in ip_by_trace_id:
            result[device_id] = {"ip": ip_by_trace_id[trace_id], "trace_id": trace_id}

    return result


def run_cmd(args, timeout=30, env=None, cwd=None):
    """執行外部指令,回傳 (returncode, stdout, stderr)。
    明確把stdin指向DEVNULL:systemd服務底下沒有互動式終端機可以輸入,
    避免被呼叫的指令(shell script、openssl等)萬一嘗試讀取標準輸入等待確認時,
    無限期卡住等一個永遠不會出現的輸入,最後只能靠逾時機制硬中斷。
    """
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd,
            stdin=subprocess.DEVNULL,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return -1, "", f"指令逾時: {e}"
    except FileNotFoundError as e:
        return -1, "", f"找不到指令: {e}"
    except Exception as e:
        return -1, "", f"執行失敗: {e}"


def delete_nanomdm_enrollment(mysql_cfg, db_password, enrollment_id):
    """從nanomdm資料庫刪除這台裝置的enrollment紀錄。
    nanomdm官方沒有提供刪除裝置的API,只能直接動資料庫,這裡只刪 enrollments 表這一筆,
    不動 devices 表(devices表可能有其他enrollment紀錄關聯,保留比較安全)。
    如果 nanomdm 的schema有設定好對應的CASCADE刪除,關聯的指令佇列等資料會一併清掉;
    如果沒有設定,可能會留下孤兒資料(不會造成錯誤,只是資料庫多了些用不到的紀錄)。
    回傳 (ok, message)。
    """
    safe_id = enrollment_id.replace("'", "''")
    sql = f"DELETE FROM enrollments WHERE id = '{safe_id}';"
    args = [
        "docker", "exec", mysql_cfg["docker_container"], "mysql",
        f"-u{mysql_cfg['db_user']}", f"-p{db_password}",
        "-N", "-B", "--raw", mysql_cfg["db_name"], "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=20)
    if rc != 0:
        return False, (err or out or "刪除失敗,原因不明")
    return True, None


def remove_from_udid_serial_cache(cache_path, lock_path, enrollment_id):
    """從webhook-server.py用來暫存「UDID->序號」對應關係的檔案裡,移除這台裝置的紀錄。
    跟webhook-server.py用同一套fcntl檔案鎖機制,避免這邊清理的同時webhook-server.py
    剛好也在寫入,造成資料互相覆蓋(這正是先前webhook-server.py併發問題修正時用的同一招)。
    找不到檔案或這筆紀錄本來就不存在都視為成功(反正結果是一致的:這筆紀錄不存在)。
    """
    if not os.path.exists(cache_path):
        return True, None
    try:
        with open(lock_path, "w") as lockfile:
            fcntl.flock(lockfile, fcntl.LOCK_EX)
            try:
                with open(cache_path, encoding="utf-8") as f:
                    cache = json.load(f)
                if enrollment_id in cache:
                    del cache[enrollment_id]
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache, f)
            finally:
                fcntl.flock(lockfile, fcntl.LOCK_UN)
        return True, None
    except Exception as e:
        return False, str(e)


def get_unlock_token(mysql_cfg, db_password, enrollment_id):
    """取得裝置在完成註冊(TokenUpdate)時回報給nanomdm的UnlockToken(原始二進位資料)。
    ClearPasscode指令一定要帶這個欄位,沒有帶會被Apple判定成CommandFormatError
    (這是nanomdm官方schema.sql裡devices.unlock_token欄位存的東西,不是隨便一個參數)。
    回傳 (token_bytes_or_None, error_message_or_None)。
    """
    safe_id = enrollment_id.replace("'", "''")
    sql = (
        "SELECT TO_BASE64(d.unlock_token) FROM enrollments e "
        "JOIN devices d ON e.device_id = d.id "
        f"WHERE e.id = '{safe_id}';"
    )
    args = [
        "docker", "exec", mysql_cfg["docker_container"], "mysql",
        f"-u{mysql_cfg['db_user']}", f"-p{db_password}",
        "-N", "-B", "--raw", mysql_cfg["db_name"], "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=15)
    if rc != 0:
        return None, (err or out)
    raw = out.strip()
    if not raw or raw == "NULL":
        return None, None
    try:
        return base64.b64decode(raw), None
    except Exception as e:
        return None, f"UnlockToken解碼失敗: {e}"


def query_devices_from_mysql(mysql_cfg, db_password):
    """透過 docker exec 查詢 nanomdm 的 enrollments + devices 表"""
    sql = (
        "SELECT e.id AS enrollment_id, d.serial_number, e.enabled, "
        "DATE_FORMAT(CONVERT_TZ(e.last_seen_at, '+00:00', '+08:00'), '%Y-%m-%d %H:%i:%s') AS last_seen_at "
        "FROM enrollments e JOIN devices d ON e.device_id = d.id;"
    )
    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        "-N", "-B", "--raw",  # batch mode, no column header, tab分隔; --raw避免內容被client端二次跳脫
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=20)
    rows = []
    if rc == 0:
        for line in out.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            rows.append({
                "enrollment_id": parts[0],
                "serial_number": parts[1],
                "enabled": parts[2],
                "last_seen_at": parts[3],
            })
    return rows, rc, out, err


def ensure_executable(script_path):
    """確保這支腳本檔案有執行權限,執行前自動補上,不管缺execute bit的原因是什麼
    (zip/git在某些流程下不一定會保留unix執行權限、透過webui自己的更新機制重新展開檔案時
    漏了重設權限、或任何其他原因)。這是自我修復機制,不依賴安裝當下有沒有正確設定過權限。

    找不到檔案、或沒有權限修改(例如檔案擁有者不是目前執行webui的使用者)時,靜默略過,
    讓後續實際執行時,依照真實的錯誤原因(找不到檔案 vs. 權限不足)自然地失敗並回報,
    不在這裡假裝掩蓋問題。
    """
    try:
        if not os.path.exists(script_path):
            return
        current_mode = os.stat(script_path).st_mode
        # 在既有的讀寫權限基礎上,加上「擁有者、群組、其他人」的執行位元(+x),
        # 不動到原本的讀寫權限設定,只補上執行權限
        os.chmod(script_path, current_mode | 0o111)
    except Exception:
        pass


# 掃描.sh檔案權限時要排除的目錄名稱——這些是第三方套件/虛擬環境自己管理的內容,
# 不是我們自己專案的腳本,不該被我們的掃描工具動到權限(而且這些目錄底下常常有大量檔案,
# 掃描會很慢,也沒有意義)
SCAN_EXCLUDE_DIRS = {"venv", "node_modules", "__pycache__", ".git"}


def scan_sh_file_permissions(root_dirs):
    """掃描指定的根目錄(可以是多個路徑的list),遞迴找出所有.sh檔案,回報每一個的
    目前執行權限狀態。給[系統狀態]頁面的手動掃描功能使用。

    回傳一個list,每個元素是 {"path": 完整路徑, "is_executable": bool, "mode": 權限字串(例如'755')}。
    依路徑排序,方便畫面上穩定呈現(不會每次掃描順序都不一樣)。
    """
    results = []
    for root_dir in root_dirs:
        if not os.path.exists(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # 用in-place修改dirnames,讓os.walk()不要繼續往這些排除目錄底下遞迴,
            # 不是掃完再事後過濾,直接跳過整個子樹,速度快很多
            dirnames[:] = [d for d in dirnames if d not in SCAN_EXCLUDE_DIRS]
            for filename in filenames:
                if not filename.endswith(".sh"):
                    continue
                full_path = os.path.join(dirpath, filename)
                try:
                    mode = os.stat(full_path).st_mode
                    is_executable = bool(mode & 0o111)
                    mode_str = oct(stat.S_IMODE(mode))[2:]
                    results.append({"path": full_path, "is_executable": is_executable, "mode": mode_str})
                except OSError:
                    continue
    results.sort(key=lambda r: r["path"])
    return results


def grant_executable_to_sh_files(paths):
    """對指定的一批.sh檔案路徑,逐一補上執行權限。給[系統狀態]頁面掃描後,
    使用者確認要修正時使用,重用ensure_executable()同一份邏輯,確保跟其他地方
    (執行第三方腳本前的自我修復機制)的權限處理行為完全一致。

    回傳 (success_count, failed_paths)。
    """
    success_count = 0
    failed_paths = []
    for path in paths:
        try:
            ensure_executable(path)
            # ensure_executable內部把例外都吞掉了,這裡額外檢查一次結果是否真的成功,
            # 確保回傳的成功/失敗統計是真實反映結果,不是「有呼叫就算成功」
            mode = os.stat(path).st_mode
            if mode & 0o111:
                success_count += 1
            else:
                failed_paths.append(path)
        except Exception:
            failed_paths.append(path)
    return success_count, failed_paths


def run_dep_account_detail(script_path, env_file_path=None, extra_env=None):
    ensure_executable(script_path)
    env = build_subprocess_env(env_file_path, extra_env) if env_file_path else extra_env
    cwd = os.path.dirname(script_path) or None
    rc, out, err = run_cmd([script_path], timeout=20, env=env, cwd=cwd)
    return rc, out, err


def run_dep_device_details(script_path, serial_number, env_file_path=None, extra_env=None):
    ensure_executable(script_path)
    env = build_subprocess_env(env_file_path, extra_env) if env_file_path else extra_env
    cwd = os.path.dirname(script_path) or None
    rc, out, err = run_cmd([script_path, serial_number], timeout=30, env=env, cwd=cwd)
    return rc, out, err


def stream_check_vpp_license(script_path, env_file_path=None):
    """以 generator 方式逐行讀取 check_vpp_license.sh 的輸出 (供 SSE 使用)"""
    ensure_executable(script_path)
    env = build_subprocess_env(env_file_path) if env_file_path else None
    cwd = os.path.dirname(script_path) or None
    proc = subprocess.Popen(
        [script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        cwd=cwd,
    )
    try:
        for line in iter(proc.stdout.readline, ""):
            if line == "" and proc.poll() is not None:
                break
            if line:
                yield line.rstrip("\n")
        proc.stdout.close()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.terminate()


def fetch_app_version_info(adam_id, country="tw", timeout=10):
    """查詢iTunes Lookup API,取得這個App目前在App Store上的版本號跟版本發布日期。

    已知限制:Apple自己的API有時候對「版本發布日期」(currentVersionReleaseDate)
    回傳不準確的資料(開發者論壇上有多起回報,實際查證過),這是Apple那邊API本身的
    資料品質問題,不是我們這裡解析錯誤——顯示出來的日期如果偶爾跟App Store頁面
    上看到的不一致,屬於已知情況。

    country參數:某些App如果在美國(預設)以外的地區上架,不帶country參數可能會
    查無資料,這裡預設帶tw(台灣),對學校情境比較適用。

    回傳 (version, release_date)的tuple,查詢失敗或找不到資料時,兩者都回傳空字串。
    """
    try:
        resp = requests.get(
            "https://itunes.apple.com/lookup",
            params={"id": adam_id, "country": country},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            return "", ""
        info = results[0]
        version = info.get("version", "")
        release_date_raw = info.get("currentVersionReleaseDate", "")
        # 日期原始格式是ISO 8601(例如"2026-07-18T07:00:00Z"),只取日期部分,不需要精確到時分秒
        release_date = release_date_raw.split("T")[0] if release_date_raw else ""
        return version, release_date
    except Exception:
        return "", ""


def parse_vpp_table_output(raw_text):
    """把 check_vpp_license.sh 輸出的文字表格解析成結構化資料。
    只抓有 '|' 分隔且第一欄是純數字(adamId)的資料列,
    自動略過標題列、分隔線(====)、以及過程訊息(例如 '1. 正在查詢...')。
    """
    rows = []
    for line in raw_text.splitlines():
        line = line.rstrip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        adam_id, bundle_id, name, total, available = parts[:5]
        if not adam_id.isdigit():
            continue  # 跳過標題列 "Adam ID | ..."
        rows.append({
            "adam_id": adam_id,
            "bundle_id": bundle_id,
            "name": name,
            "total": total,
            "available": available,
        })
    return rows


def write_vpp_cache_csv(path, rows):
    """寫入 UTF-8 with BOM 的 CSV,讓 Excel 開啟時中文不會亂碼"""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Adam ID", "Bundle ID", "軟體名稱", "當下版本", "版本日期", "總數量", "剩餘量", "自動更新"])
        for r in rows:
            writer.writerow([
                r["adam_id"], r["bundle_id"], r["name"],
                r.get("version", ""), r.get("release_date", ""),
                r["total"], r["available"],
                "true" if r.get("auto_update") else "false",
            ])
    os.replace(tmp_path, path)


def read_vpp_cache_csv(path):
    """回傳 (rows, mtime),檔案不存在時回傳 ([], None)"""
    if not os.path.exists(path):
        return [], None
    mtime = os.path.getmtime(path)
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows, mtime


def find_groups_bound_to_app(groups_path, adam_id):
    """掃描groups.json,找出「apps」清單裡有包含這個adam_id的所有群組名稱。
    adam_id用字串比對(groups.json裡存的通常也是字串),避免int/str型別不一致漏掉比對。
    回傳群組名稱的list,找不到任何綁定的話回傳空list。
    """
    if not os.path.exists(groups_path):
        return []
    try:
        with open(groups_path, encoding="utf-8") as f:
            groups = json.load(f)
    except Exception:
        return []

    adam_id_str = str(adam_id)
    bound_groups = []
    for group_name, group_data in groups.items():
        if not isinstance(group_data, dict):
            continue
        apps = group_data.get("apps", [])
        if any(str(a) == adam_id_str for a in apps):
            bound_groups.append(group_name)
    return bound_groups


def find_devices_in_groups(devices_csv_path, group_names):
    """掃描devices.csv,找出「group」欄位屬於指定群組清單裡任一個的所有裝置序號。
    回傳序號的list。
    """
    if not group_names:
        return []
    devices = read_devices_csv(devices_csv_path)
    group_set = set(group_names)
    return [sn for sn, info in devices.items() if info.get("group") in group_set]


def query_all_devices_latest_status(mysql_cfg, db_password, enrollment_id=None):
    """一次查出「每一台裝置」最近一筆 DeviceInformation / AvailableOSUpdates /
    OSUpdateStatus / DeviceLocation 回應,用 window function(ROW_NUMBER)在單一SQL裡完成,
    不用對每台裝置各自呼叫一次(那樣裝置一多會很慢)。

    enrollment_id: 選填,只查這一台裝置(給單一裝置手動同步用,不用每次都撈出全部裝置)。
    回傳 (rows, rc, err),rows 是 [{id, request_type, result, result_updated_at}, ...]
    """
    enrollment_filter = ""
    if enrollment_id:
        safe_id = enrollment_id.replace("'", "''")
        enrollment_filter = f"AND id = '{safe_id}' "
    sql = (
        "SELECT JSON_ARRAYAGG(JSON_OBJECT("
        "'id', id, 'request_type', request_type, 'result_b64', TO_BASE64(result), "
        "'result_updated_at', DATE_FORMAT(CONVERT_TZ(result_updated_at, '+00:00', '+08:00'), '%Y-%m-%d %H:%i:%s')"
        ")) FROM (SELECT id, request_type, result, result_updated_at, "
        "ROW_NUMBER() OVER (PARTITION BY id, request_type ORDER BY result_updated_at DESC) AS rn "
        "FROM view_queue "
        "WHERE request_type IN ('DeviceInformation', 'AvailableOSUpdates', 'OSUpdateStatus', 'DeviceLocation', 'ScheduleOSUpdate') "
        f"AND result IS NOT NULL {enrollment_filter}"
        ") t WHERE rn = 1;"
    )
    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        "-N", "-B", "--raw",
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=30)
    if rc != 0:
        return None, rc, (err or out)

    raw = out.strip()
    if not raw or raw == "NULL":
        return [], 0, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, -1, f"JSON 解析失敗: {e}"

    rows = []
    for item in (data or []):
        result_b64 = item.pop("result_b64", "") or ""
        try:
            item["result"] = base64.b64decode(result_b64).decode("utf-8", errors="replace") if result_b64 else None
        except Exception:
            item["result"] = None
        rows.append(item)
    return rows, 0, None


DEVICES_STATUS_CSV_FIELDS = [
    "serial_number", "battery_level", "device_capacity", "available_device_capacity",
    "os_version", "build_version", "available_os_version", "available_os_product_key",
    "os_update_is_downloaded", "os_update_status", "ip_address",
    "lost_mode_enabled", "location_lat", "location_lng", "location_at", "location_accuracy",
]


def write_devices_status_cache(path, rows):
    """rows: 每台裝置一筆dict,欄位對應 DEVICES_STATUS_CSV_FIELDS。
    ip_address 是從 nanomdm 服務自己的連線 log 解析取得(不是透過 Apple MDM 協定查詢,
    Apple 的 MDM 協定本身沒有提供這個查詢欄位),裝置最近沒有連線紀錄的話會是空字串。
    lost_mode_enabled 是本地追蹤的狀態(不是查MDM取得,是我們自己送EnableLostMode/
    DisableLostMode指令成功後記錄的),每次重建快取時要記得從舊檔案裡保留下來,
    不然背景排程每次重建都會把這個狀態清空。
    """
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DEVICES_STATUS_CSV_FIELDS)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in DEVICES_STATUS_CSV_FIELDS})
    os.replace(tmp_path, path)


def read_devices_status_cache(path):
    """回傳 ({serial_number: {...}}, mtime)。檔案不存在時回傳 ({}, None)"""
    if not os.path.exists(path):
        return {}, None
    result = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sn = row.get("serial_number", "")
            if not sn:
                continue
            result[sn] = row
    return result, os.path.getmtime(path)


def set_lost_mode_state(path, serial_number, enabled):
    """更新單一裝置的遺失模式本地追蹤狀態(不影響其他欄位),
    在成功送出EnableLostMode/DisableLostMode指令後呼叫。
    停用遺失模式時,順便清掉舊的定位資料(避免顯示過期、可能已經不適用的位置)。
    """
    cache, _ = read_devices_status_cache(path)
    row = cache.get(serial_number, {"serial_number": serial_number})
    row["lost_mode_enabled"] = "true" if enabled else "false"
    if not enabled:
        row["location_lat"] = ""
        row["location_lng"] = ""
        row["location_at"] = ""
        row["location_accuracy"] = ""
    cache[serial_number] = row
    write_devices_status_cache(path, list(cache.values()))


def build_devices_status_rows(status_rows, serial_by_enrollment_id, existing_cache=None):
    """把 query_all_devices_latest_status 查回來的原始資料(依 enrollment_id 分散的多筆記錄),
    整理成每台裝置一列的 devices-status.csv 用資料。
    existing_cache(可選,即目前檔案裡已經有的資料)用來保留 lost_mode_enabled 這種
    本地追蹤、不是從MDM查詢結果來的狀態,避免每次重建快取就被清空。
    """
    by_serial = {}
    if existing_cache:
        for serial, old_row in existing_cache.items():
            by_serial[serial] = {
                "serial_number": serial,
                "lost_mode_enabled": old_row.get("lost_mode_enabled", ""),
                "location_lat": old_row.get("location_lat", ""),
                "location_lng": old_row.get("location_lng", ""),
                "location_at": old_row.get("location_at", ""),
                "location_accuracy": old_row.get("location_accuracy", ""),
            }

    # 依result_updated_at時間排序(由舊到新)再處理,確保有「同一個裝置欄位可能被多種
    # request_type設定」的情況時(例如os_update_status同時會被OSUpdateStatus跟
    # ScheduleOSUpdate設定),最後生效的一定是時間上真正最新的那筆,不會因為SQL查詢
    # 回傳的順序不保證跟時間一致,而被舊資料蓋掉新資料。空字串(缺少時間戳記的極端情況)
    # 排最前面,不會影響排序穩定性。
    sorted_status_rows = sorted(status_rows, key=lambda item: item.get("result_updated_at") or "")

    for item in sorted_status_rows:
        enrollment_id = item.get("id")
        serial = serial_by_enrollment_id.get(enrollment_id)
        if not serial:
            continue
        parsed = parse_plist_text(item.get("result"))
        if not parsed:
            continue
        row = by_serial.setdefault(serial, {"serial_number": serial})

        if item.get("request_type") == "DeviceInformation":
            qr = parsed.get("QueryResponses") or {}
            if qr.get("BatteryLevel") is not None:
                row["battery_level"] = qr["BatteryLevel"]
            if qr.get("DeviceCapacity") is not None:
                row["device_capacity"] = qr["DeviceCapacity"]
            if qr.get("AvailableDeviceCapacity") is not None:
                row["available_device_capacity"] = qr["AvailableDeviceCapacity"]
            if qr.get("OSVersion"):
                row["os_version"] = qr["OSVersion"]
            if qr.get("BuildVersion"):
                row["build_version"] = qr["BuildVersion"]

        elif item.get("request_type") == "AvailableOSUpdates":
            updates = parsed.get("AvailableOSUpdates") or []
            if updates:
                latest = updates[0]
                version_label = latest.get("HumanReadableName") or latest.get("Version") or ""
                row["available_os_version"] = version_label
                row["available_os_product_key"] = latest.get("ProductKey") or ""
            else:
                # 空陣列不代表「完全沒有更新」,也可能代表「更新已經下載完成、
                # 不再是待下載狀態」——只清空「可用更新版本」相關欄位,不要連帶清空
                # os_update_is_downloaded/os_update_status。這兩個欄位由OSUpdateStatus
                # 獨立維護,如果這裡也跟著清空,萬一同一輪排程裡AvailableOSUpdates(空陣列)
                # 剛好排在OSUpdateStatus之後處理,會把剛解析出來的IsDownloaded=true洗掉,
                # 導致裝置明明已經下載完成,畫面卻還是顯示「下載更新」而不是「安裝更新」。
                # 前端的下載/安裝按鈕完全是靠available_os_version是否有值來決定要不要顯示,
                # 不清空這兩個欄位不會造成「沒有更新卻顯示按鈕」的問題。
                row["available_os_version"] = ""
                row["available_os_product_key"] = ""

        elif item.get("request_type") == "OSUpdateStatus":
            # 回應格式參考micromdm的OSUpdateStatusResponseItem:
            # {ProductKey, IsDownloaded, DownloadPercentComplete, Status}
            # 頂層key沿用RequestType慣例(跟AvailableOSUpdates一致),防禦性處理找不到的情況
            statuses = parsed.get("OSUpdateStatus") or []
            target_key = row.get("available_os_product_key")
            # 陣列裡通常只會有一筆資料(裝置目前只會處理一個更新),這種情況直接採用,
            # 不強制要求跟target_key完全比對成功——這是實際發生過的問題:如果target_key
            # 因為時序(AvailableOSUpdates還沒處理過)或格式些微差異對不上,整筆回應會被
            # 靜靜跳過,os_update_is_downloaded就會停留在舊值,不會被更新。
            # 只有陣列裡真的有多筆資料、需要挑出正確那筆時,才使用target_key比對篩選。
            candidates = statuses if (len(statuses) <= 1 or not target_key) else [
                s for s in statuses if s.get("ProductKey") == target_key
            ] or statuses  # 篩選後如果一筆都沒有(target_key對不上任何一筆),退回用全部,不要整批放棄
            for s in candidates:
                is_downloaded = s.get("IsDownloaded")
                # 已知部分裝置回傳整數0/1而非布林值(社群回報過的已知行為),兩種都處理
                row["os_update_is_downloaded"] = "true" if is_downloaded in (True, 1, "1", "true") else "false"
                row["os_update_status"] = s.get("Status", "")
                break

        elif item.get("request_type") == "ScheduleOSUpdate":
            # ScheduleOSUpdate指令本身的確認回應,格式跟OSUpdateStatus不同:
            # 頂層key是UpdateResults(不是OSUpdateStatus),每筆包含
            # {InstallAction, ProductKey, Status}。這是唯一會回報「安裝中(Installing)」
            # 這個狀態的地方——OSUpdateStatus查詢指令本身不會回報Installing,
            # 只有實際觸發ScheduleOSUpdate指令後,裝置在這個指令自己的確認回應裡
            # 才會回報目前是不是正在安裝。(查證來源:Apple官方文件
            # ScheduleOSUpdateResponse.UpdateResultsItem,以及多篇社群MDM廠商
            # 技術文件裡的實際範例回應)
            update_results = parsed.get("UpdateResults") or []
            target_key = row.get("available_os_product_key")
            candidates = update_results if (len(update_results) <= 1 or not target_key) else [
                u for u in update_results if u.get("ProductKey") == target_key
            ] or update_results
            for u in candidates:
                status_value = u.get("Status", "")
                if status_value:  # 空字串代表這筆沒有明確狀態(例如純粹的錯誤回應),不要覆蓋掉既有值
                    row["os_update_status"] = status_value
                break

        elif item.get("request_type") == "DeviceLocation":
            lat = parsed.get("Latitude")
            lng = parsed.get("Longitude")
            if lat is not None and lng is not None:
                row["location_lat"] = lat
                row["location_lng"] = lng
                row["location_at"] = item.get("result_updated_at", "")
                row["location_accuracy"] = parsed.get("HorizontalAccuracy", "")

        row.setdefault("ip_address", "")  # Apple MDM協定不提供,固定空字串

    return list(by_serial.values())



def query_command_history(mysql_cfg, db_password, enrollment_id, limit=30):
    """查詢某個 enrollment 最近的指令派送與回應紀錄。
    用 MySQL 的 JSON_OBJECT/JSON_ARRAYAGG 讓資料庫直接吐出單行 JSON,
    避免 result 欄位(原始 plist,內含換行字元)打斷一般 tab/換行分隔的解析方式。
    result 欄位另外用 TO_BASE64() 包裝:因為它可能含有無法安全塞進JSON字串的位元組
    (例如非UTF-8內容),不管內容是什麼,base64輸出保證是純ASCII、不可能破壞JSON語法。
    回傳 (rows, rc, err)。
    """
    safe_id = enrollment_id.replace("'", "''")  # 基本轉義,避免SQL injection
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 30

    sql = (
        "SELECT JSON_ARRAYAGG(JSON_OBJECT("
        "'command_uuid', command_uuid, 'request_type', request_type, "
        "'status', status, 'result_b64', TO_BASE64(COALESCE(result, '')), "
        "'command_b64', TO_BASE64(COALESCE(command, '')), "
        "'created_at', DATE_FORMAT(CONVERT_TZ(created_at, '+00:00', '+08:00'), '%Y-%m-%d %H:%i:%s'), "
        "'result_updated_at', DATE_FORMAT(CONVERT_TZ(result_updated_at, '+00:00', '+08:00'), '%Y-%m-%d %H:%i:%s')"
        ")) FROM (SELECT command_uuid, request_type, status, result, command, created_at, result_updated_at "
        f"FROM view_queue WHERE id = '{safe_id}' "
        f"ORDER BY created_at DESC LIMIT {limit_int}) t;"
    )
    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        "-N", "-B", "--raw",
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=20)
    if rc != 0:
        return None, rc, (err or out)

    raw = out.strip()
    if not raw or raw == "NULL":
        return [], 0, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, -1, f"JSON 解析失敗: {e}"

    rows = []
    for item in (data or []):
        result_b64 = item.pop("result_b64", "") or ""
        try:
            item["result"] = base64.b64decode(result_b64).decode("utf-8", errors="replace") if result_b64 else None
        except Exception:
            item["result"] = None

        command_b64 = item.pop("command_b64", "") or ""
        try:
            item["command"] = base64.b64decode(command_b64).decode("utf-8", errors="replace") if command_b64 else None
        except Exception:
            item["command"] = None

        rows.append(item)
    return rows, 0, None
    return rows, 0, None


def parse_plist_text(text):
    """把原始 plist XML 字串解析成 Python dict,失敗回傳 None"""
    if not text:
        return None
    try:
        return plistlib.loads(text.encode("utf-8"))
    except Exception:
        return None


def json_safe(value):
    """遞迴把 plistlib 解析出來可能含有的 datetime/bytes 轉成 JSON 可序列化的型態"""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes binary data>"
    return value


# ---------------------------------------------------------------------------
# MDM Command 組建與送出
# ---------------------------------------------------------------------------
def build_command_plist(request_type, params=None):
    """用 plistlib 組出合法的 Command plist bytes"""
    params = params or {}
    command = {"RequestType": request_type}
    command.update(params)
    payload = {
        "Command": command,
        "CommandUUID": str(uuid.uuid4()),
    }
    return plistlib.dumps(payload)


def revoke_vpp_license(vpp_token_path, serial, adam_id, timeout=30):
    """透過 classic VPP API 撤銷指定裝置序號的 App 授權(裝置退場時用,釋放授權額度)。
    用 disassociateSerialNumbers(跟指派用的 associateSerialNumbers 對稱),
    這個參數名稱是從 Apple 開發者論壇的真實請求範例查證確認的,不是用猜的。
    回傳 Apple 的原始回應 dict,呼叫端可以檢查裡面的 status 欄位(0 代表成功)。
    """
    with open(vpp_token_path, "r") as f:
        stoken = f.read().strip()

    resp = requests.post(
        "https://vpp.itunes.apple.com/mdm/manageVPPLicensesByAdamIdSrv",
        headers={"Content-Type": "application/json"},
        json={
            "sToken": stoken,
            "adamIdStr": str(adam_id),
            "pricingParam": "STDQ",
            "disassociateSerialNumbers": [serial],
            "notifyDisassociation": False,
        },
        timeout=timeout,
    )
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text, "status_code": resp.status_code}


def assign_vpp_license(vpp_token_path, serial, adam_id, timeout=30):
    """透過 classic VPP API 把 App 授權指派給指定的裝置序號。
    這一步是送出 InstallApplication 指令「之前」一定要做的準備動作
    (webhook-server.py 的自動化流程本來就有做這步,但「派送命令」手動安裝時
    原本沒有這個步驟,導致 Apple 回報「無法取得App許可證」的錯誤)。
    回傳 Apple 的原始回應 dict,呼叫端可以檢查裡面的 status 欄位(0 代表成功)。
    """
    with open(vpp_token_path, "r") as f:
        stoken = f.read().strip()

    resp = requests.post(
        "https://vpp.itunes.apple.com/mdm/manageVPPLicensesByAdamIdSrv",
        headers={"Content-Type": "application/json"},
        json={
            "sToken": stoken,
            "adamIdStr": str(adam_id),
            "pricingParam": "STDQ",
            "associateSerialNumbers": [serial],
        },
        timeout=timeout,
    )
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text, "status_code": resp.status_code}


def send_mdm_command(nanomdm_base_url, api_user, api_key, enrollment_id, request_type, params=None, timeout=15):
    """組出 plist 並用 requests PUT/curl等效方式送到 nanomdm 的 /v1/enqueue/{id}"""
    plist_bytes = build_command_plist(request_type, params)
    url = f"{nanomdm_base_url.rstrip('/')}/v1/enqueue/{enrollment_id}"
    resp = requests.put(
        url,
        data=plist_bytes,
        auth=(api_user, api_key),
        timeout=timeout,
        verify=True,
    )
    try:
        result_json = resp.json()
    except Exception:
        result_json = {"raw": resp.text}
    return resp.status_code, result_json


def trigger_push(nanomdm_base_url, api_user, api_key, enrollment_id, timeout=15):
    url = f"{nanomdm_base_url.rstrip('/')}/v1/push/{enrollment_id}"
    resp = requests.get(url, auth=(api_user, api_key), timeout=timeout)
    try:
        result_json = resp.json()
    except Exception:
        result_json = {"raw": resp.text}
    return resp.status_code, result_json


def get_pending_status_query_types(mysql_cfg, db_password):
    """查詢目前所有裝置,還有哪些(enrollment_id, request_type)組合「真的還沒收到任何回應」,
    以及這筆pending紀錄是什麼時候建立的。只鎖定裝置狀態排程會用到的這幾種查詢類型,
    不是全部指令類型,避免不必要地撈出大量不相關的資料。

    回傳一個dict,key是(enrollment_id, request_type)的tuple,value是這筆pending紀錄裡
    「最新一筆」的建立時間字串("%Y-%m-%d %H:%M:%S",已轉換成台灣時區)。用途:讓裝置狀態
    排程在每次派送新的查詢指令前,先確認「這台裝置的這種指令類型,最近一次送出的那筆,
    是不是還在等回應」,如果是「最近才送、還在等回應」就跳過這次派送,避免對離線裝置
    無限疊加派送同類型指令;但如果連最新的那筆pending都已經卡了很久(裝置持續離線超過
    某個時間門檻),呼叫端可以判斷「這筆多半已經沒有意義了」,重新嘗試送一次新的,不會
    因為一筆卡住的舊紀錄就永久放棄追蹤這台裝置。

    重要:這裡刻意用MAX(取最新一筆)而不是MIN(取最早一筆)。曾經寫成MIN,結果對於
    「已經累積了大量從未被解決的極舊pending紀錄」的裝置(例如從好幾週前就開始堆積),
    MIN永遠會抓到那筆極舊的紀錄,導致「已經超過門檻、該重試」的判斷永遠成立,不管
    最近是不是才剛送過,完全沒有真的達到「避免短時間內重複」的效果——這是實際發生過、
    造成裝置每10分鐘還是重複被派送指令的真正原因。

    更重要:判斷「還在不在等待中」不能只看active=1這個欄位——已經證實過nanomdm的
    active欄位不會因為裝置成功回應(Acknowledged)就自動變成0,只有明確被取消才會變0。
    如果只看active=1,一台早就已經回應完成的裝置,還是會被誤判成「還在等待中」,
    導致下一輪排程時,明明該無條件強制重送最新查詢,卻被6小時的逾時重派門檻誤擋下來,
    造成「排程自動更新」跟「逾時重派門檻」兩個機制互相衝突。正確做法是額外用
    LEFT JOIN command_results,只把「還沒有對應回應紀錄」的查詢視為真正等待中——
    已經收到任何回應(不管是Acknowledged還是其他任何最終狀態)的,一律視為「不在等待中」,
    完全不會出現在這個函式的回傳結果裡,讓呼叫端(_should_send_query)判斷「沒有pending紀錄」
    而直接放行,下一輪排程正常強制送出新的查詢,不受逾時重派門檻限制。
    """
    query_types = "'DeviceInformation','AvailableOSUpdates','OSUpdateStatus','DeviceLocation'"
    sql = (
        "SELECT q.id AS enrollment_id, c.request_type, "
        "DATE_FORMAT(CONVERT_TZ(MAX(q.created_at), '+00:00', '+08:00'), '%Y-%m-%d %H:%i:%s') AS latest_created_at "
        "FROM enrollment_queue q "
        "JOIN commands c ON q.command_uuid = c.command_uuid "
        "LEFT JOIN command_results r ON r.command_uuid = q.command_uuid AND r.id = q.id "
        f"WHERE r.id IS NULL AND c.request_type IN ({query_types}) "
        "GROUP BY q.id, c.request_type;"
    )
    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        "-N", "-B", "--raw",
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=20)
    pending = {}
    if rc != 0:
        return pending  # 查詢失敗時回傳空dict,讓呼叫端維持原本行為(照常派送),
                         # 不要因為這個防重複機制本身查詢失敗,就讓原本的狀態查詢功能整個停擺
    for line in out.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pending[(parts[0], parts[1])] = parts[2]
    return pending


def _cleanup_safe_where_clause(retention_days):
    """組出安全的清理條件:夠舊(超過保留天數) 且 沒有任何裝置的佇列項目「真的還沒收到回應」。

    重要修正記錄:原本這裡用 active=1 當作「還有裝置在等」的判斷依據,但查證nanomdm
    官方schema.sql後發現這個假設是錯的——active這個欄位似乎不會因為裝置成功回應
    (Acknowledged)就自動被nanomdm改成0(官方文件說明,nanomdm自己內建的清理機制
    -storage-options delete=1,做的是「直接刪除整筆紀錄」,不是「把active改成0」,
    代表active很可能從建立那一刻起就一直維持在預設值1,直到那筆紀錄被刪除為止,
    跟裝置有沒有回應完全無關)。這個問題經過實際資料驗證確認:有Acknowledged超過
    13天的紀錄,active依然是1。

    如果繼續用active=1判斷,會把絕大多數早就處理完畢的指令,都誤判成「還有裝置在等」
    而永遠排除在清理範圍外,這正是保留天數設定生效卻清不掉舊資料的根本原因。

    正確的判斷依據改成:這個佇列項目(enrollment_queue的每一列,代表某台裝置在等
    某個指令)是不是「還沒收到回應」而且「還沒被取消」——兩個條件都要符合,才是真正
    該保護、不能清理的對象:
      - 沒有收到回應(LEFT JOIN command_results後cr.id IS NULL):對應nanomdm自己
        在view_queue裡描述「outstanding」(未處理)的定義
      - active仍然是1:如果是active=0(不管是「取消命令」功能手動設的,還是其他原因),
        代表這個佇列項目已經不再被視為需要處理,即使沒有command_results也不該
        阻擋清理,不然「已取消」的舊紀錄會被誤判成「還在等待」而永遠清不掉,
        跟已取消也該正常被清理的設計互相矛盾
    """
    retention_days = int(retention_days)  # 防止SQL injection,確定是整數才拼進SQL
    return (
        f"created_at < (NOW() - INTERVAL {retention_days} DAY) "
        f"AND command_uuid NOT IN ("
        f"SELECT eq.command_uuid FROM enrollment_queue eq "
        f"LEFT JOIN command_results cr ON cr.command_uuid = eq.command_uuid AND cr.id = eq.id "
        f"WHERE cr.id IS NULL AND eq.active = 1"
        f")"
    )


def preview_command_cleanup(mysql_cfg, db_password, retention_days):
    """預覽清理效果,只查COUNT,不會真的刪除任何資料。
    回傳dict: {ok, commands_count, command_results_count, enrollment_queue_count,
               by_request_type: [(request_type, count), ...], error}
    command_results_count/enrollment_queue_count是「會被cascade一併刪除」的估計筆數,
    不是這兩個表本身有獨立的刪除條件。
    """
    where_clause = _cleanup_safe_where_clause(retention_days)

    def run_query(sql):
        args = [
            "docker", "exec",
            mysql_cfg["docker_container"],
            "mysql",
            f"-u{mysql_cfg['db_user']}",
            f"-p{db_password}",
            "-N", "-B", "--raw",
            mysql_cfg["db_name"],
            "-e", sql,
        ]
        return run_cmd(args, timeout=30)

    rc1, out1, err1 = run_query(f"SELECT COUNT(*) FROM commands WHERE {where_clause};")
    if rc1 != 0:
        return {"ok": False, "error": err1 or out1}
    commands_count = int(out1.strip() or 0)

    rc2, out2, err2 = run_query(
        f"SELECT COUNT(*) FROM command_results WHERE command_uuid IN "
        f"(SELECT command_uuid FROM commands WHERE {where_clause});"
    )
    command_results_count = int(out2.strip() or 0) if rc2 == 0 else None

    rc3, out3, err3 = run_query(
        f"SELECT COUNT(*) FROM enrollment_queue WHERE command_uuid IN "
        f"(SELECT command_uuid FROM commands WHERE {where_clause});"
    )
    enrollment_queue_count = int(out3.strip() or 0) if rc3 == 0 else None

    rc4, out4, err4 = run_query(
        f"SELECT request_type, COUNT(*) FROM commands WHERE {where_clause} "
        f"GROUP BY request_type ORDER BY COUNT(*) DESC;"
    )
    by_request_type = []
    if rc4 == 0:
        for line in out4.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) == 2:
                by_request_type.append({"request_type": parts[0], "count": int(parts[1])})

    return {
        "ok": True,
        "commands_count": commands_count,
        "command_results_count": command_results_count,
        "enrollment_queue_count": enrollment_queue_count,
        "by_request_type": by_request_type,
    }


def execute_command_cleanup(mysql_cfg, db_password, retention_days):
    """實際執行清理:刪除commands裡符合安全條件的紀錄,command_results/enrollment_queue
    會透過資料庫本身的ON DELETE CASCADE自動一併清除,不需要另外下刪除指令。
    回傳dict: {ok, deleted_count, error}
    """
    where_clause = _cleanup_safe_where_clause(retention_days)
    sql = f"DELETE FROM commands WHERE {where_clause};"

    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=60)
    if rc != 0:
        return {"ok": False, "error": err or out}

    # DELETE指令本身的輸出不會直接告訴你刪了幾筆,用mysql client的--verbose也不保證格式穩定,
    # 改用執行完後另外查一次affected rows的方式不可靠(連線已經結束),這裡改成執行前先跑過一次
    # preview查詢記錄「預期要刪的數量」,呼叫端(app.py)會自己把這個數字帶入回應,這裡回傳ok即可
    return {"ok": True}


def lookup_vpp_app_info(vpp_cache_path, adam_id=None, bundle_id=None):
    """從VPP快取CSV裡,用adam_id或bundle_id查出對應的App資訊(Bundle ID + 軟體名稱)。
    兩個查詢鍵擇一提供即可,adam_id優先(InstallApplication指令用這個);
    bundle_id是給RemoveApplication這種指令本身就直接帶Bundle ID、不需要查adam_id的情況用。
    查不到、或VPP快取檔案不存在,回傳None,不影響呼叫端繼續正常運作(只是沒有額外資訊可以補)。
    """
    rows, _ = read_vpp_cache_csv(vpp_cache_path)
    if not rows:
        return None

    for row in rows:
        if adam_id and row.get("Adam ID") == str(adam_id):
            return {"bundle_id": row.get("Bundle ID", ""), "name": row.get("軟體名稱", "")}
        if bundle_id and row.get("Bundle ID") == bundle_id:
            return {"bundle_id": row.get("Bundle ID", ""), "name": row.get("軟體名稱", "")}
    return None


def _build_command_history_where_clause(enrollment_ids=None, request_type=None, status=None):
    """組出query_all_command_history跟count_all_command_history共用的WHERE條件,
    避免兩處各自維護一份、容易改一邊忘了改另一邊導致總筆數跟實際資料筆數對不起來。

    回傳 (where_sql, short_circuit_empty)。short_circuit_empty為True代表篩選條件
    已知不會有任何結果(例如enrollment_ids算出來是空list),呼叫端應該直接回傳空結果,
    不需要真的送出SQL查詢。
    """
    where_clauses = []
    if enrollment_ids is not None:
        if not enrollment_ids:
            return "", True
        safe_ids = "','".join(eid.replace("'", "''") for eid in enrollment_ids)
        where_clauses.append(f"id IN ('{safe_ids}')")
    if request_type:
        safe_type = request_type.replace("'", "''")
        where_clauses.append(f"request_type = '{safe_type}'")
    if status:
        safe_status = status.replace("'", "''")
        if status == "__pending__":
            # 特殊值:代表「真的還在排隊等待回應中」,必須同時符合status為NULL
            # 且active=1兩個條件——如果只看status IS NULL,會把「已經被取消」的舊紀錄
            # (active=0但status同樣是NULL)也混進來,造成畫面上分不清哪些是真的在等、
            # 哪些其實已經處理過了
            where_clauses.append("status IS NULL AND active = 1")
        elif status == "__cancelled__":
            # 特殊值:代表「已經被取消」,同樣是status為NULL,但active=0
            where_clauses.append("status IS NULL AND active = 0")
        else:
            where_clauses.append(f"status = '{safe_status}'")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_sql, False


def count_all_command_history(mysql_cfg, db_password, enrollment_ids=None, request_type=None, status=None):
    """查詢符合篩選條件的「總筆數」(不受LIMIT/OFFSET影響),給分頁計算總頁數用。
    用跟query_all_command_history完全相同的WHERE條件(共用_build_command_history_where_clause),
    確保總筆數跟實際分頁資料是同一個篩選條件算出來的,不會對不起來。
    回傳 (count, rc, err)。
    """
    where_sql, short_circuit_empty = _build_command_history_where_clause(enrollment_ids, request_type, status)
    if short_circuit_empty:
        return 0, 0, None

    sql = f"SELECT COUNT(*) FROM view_queue {where_sql};"
    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        "-N", "-B", "--raw",
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=30)
    if rc != 0:
        return 0, rc, (err or out)
    try:
        return int(out.strip()), 0, None
    except (ValueError, TypeError):
        return 0, -1, f"無法解析筆數: {out}"


def query_all_command_history(mysql_cfg, db_password, enrollment_ids=None, request_type=None, status=None, limit=1000, offset=0):
    """查詢「所有裝置」的指令派送與回應紀錄(不像query_command_history限定單一enrollment)。
    給[系統紀錄]裡的「指派命令紀錄」彙整表用。

    enrollment_ids: 選填,限定只查這些enrollment_id(用來支援依裝置名稱/序號/群組篩選,
                     這些條件要先在Python端對照本地devices.csv/groups.json算出符合的
                     enrollment_id清單,再傳進來,因為nanomdm的資料庫完全不知道群組/裝置名稱
                     這些我們自己webui才有的概念)。
    request_type/status: 選填,直接對應到SQL WHERE條件(這兩個nanomdm資料庫本身就有)。
    limit/offset: 分頁用,依created_at時間新到舊排序,offset=0代表第一頁。

    回傳 (rows, rc, err)。
    """
    try:
        limit_int = int(limit)
    except (TypeError, ValueError):
        limit_int = 1000
    try:
        offset_int = int(offset)
    except (TypeError, ValueError):
        offset_int = 0

    where_sql, short_circuit_empty = _build_command_history_where_clause(enrollment_ids, request_type, status)
    if short_circuit_empty:
        # 篩選條件算出來是「沒有任何裝置符合」,直接回傳空結果,不用送一個IN ()的SQL
        # (在部分SQL方言或組合下這樣容易出錯,直接短路處理更保險)
        return [], 0, None

    sql = (
        "SELECT JSON_ARRAYAGG(JSON_OBJECT("
        "'id', id, 'command_uuid', command_uuid, 'request_type', request_type, "
        "'status', status, 'active', active, "
        "'result_b64', TO_BASE64(COALESCE(result, '')), "
        "'command_b64', TO_BASE64(COALESCE(command, '')), "
        "'created_at', DATE_FORMAT(CONVERT_TZ(created_at, '+00:00', '+08:00'), '%Y-%m-%d %H:%i:%s'), "
        "'result_updated_at', DATE_FORMAT(CONVERT_TZ(result_updated_at, '+00:00', '+08:00'), '%Y-%m-%d %H:%i:%s')"
        ")) FROM (SELECT id, command_uuid, request_type, status, active, result, command, created_at, result_updated_at "
        f"FROM view_queue {where_sql} "
        f"ORDER BY created_at DESC LIMIT {limit_int} OFFSET {offset_int}) t;"
    )
    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        "-N", "-B", "--raw",
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=30)
    if rc != 0:
        return None, rc, (err or out)

    raw = out.strip()
    if not raw or raw == "NULL":
        return [], 0, None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, -1, f"JSON 解析失敗: {e}"

    rows = []
    for item in (data or []):
        result_b64 = item.pop("result_b64", "") or ""
        try:
            item["result"] = base64.b64decode(result_b64).decode("utf-8", errors="replace") if result_b64 else None
        except Exception:
            item["result"] = None

        command_b64 = item.pop("command_b64", "") or ""
        try:
            item["command"] = base64.b64decode(command_b64).decode("utf-8", errors="replace") if command_b64 else None
        except Exception:
            item["command"] = None

        rows.append(item)
    return rows, 0, None


def cancel_pending_command(mysql_cfg, db_password, enrollment_id, command_uuid):
    """取消一筆「尚未完成」的指令:把enrollment_queue.active設成0,不刪除任何資料。
    這跟nanomdm自己在裝置正常回應完指令時做的狀態轉換完全一樣(active: 1->0),
    差別只在於這次是管理者手動觸發,不是等裝置回應觸發。
    只會影響active目前是1的紀錄,避免對已經處理完的舊紀錄造成任何意外的資料異動。
    回傳 (ok, error)。
    """
    safe_eid = enrollment_id.replace("'", "''")
    safe_uuid = command_uuid.replace("'", "''")
    sql = (
        f"UPDATE enrollment_queue SET active = 0 "
        f"WHERE id = '{safe_eid}' AND command_uuid = '{safe_uuid}' AND active = 1;"
    )
    args = [
        "docker", "exec",
        mysql_cfg["docker_container"],
        "mysql",
        f"-u{mysql_cfg['db_user']}",
        f"-p{db_password}",
        mysql_cfg["db_name"],
        "-e", sql,
    ]
    rc, out, err = run_cmd(args, timeout=20)
    if rc != 0:
        return False, (err or out)
    return True, None
