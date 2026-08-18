# -*- coding: utf-8 -*-
"""
ADE (DEP) 註冊 profile 管理:
- 跟 mobileconfig 完全是不同的東西:這裡管理的是 Apple DEP API 的 profile JSON
  (skip_setup_items / is_supervised 等),透過 nanodep 的 depserver proxy 送給 Apple。
- 本地儲存的檔案是「範本」,套用時才會實際呼叫 nanodep,取得新的 profile_uuid。
- Apple 的 DEP API 沒有「原地修改」,每次套用都是重新 define 一份,拿到新 UUID。
"""
import datetime
import json
import os
import re

import requests

import utils

FILENAME_RE = re.compile(r'^[A-Za-z0-9_\-]{1,100}\.json$')
DEFAULT_ENROLL_FILENAME = "default-enroll.json"

# 依照使用者實際運作中的 profile 驗證過的合法值(真實 ground truth,不是憑空列的)
VERIFIED_SKIP_SETUP_ITEMS = [
    "AppleID", "Android", "Biometric", "Diagnostics", "DisplayTone",
    "Location", "Passcode", "Payment", "Privacy", "Restore", "ScreenTime",
    "SIMSetup", "TOS", "TVProviderSignIn", "TVRoom", "UpdateCompleted",
    "WatchMigration", "Zoom", "Siri", "OnBoarding", "SoftwareUpdate",
    "iMessageAndFaceTime",
]

# 從 Apple 官方 apple/device-management repo 的 skipkeys.yaml 找到的候選項目。
# 注意:skipkeys.yaml 記錄的是 SetupAssistant.managed 描述檔的 SkipSetupItems,
# 跟 nanodep 用的 classic DEP Profile API 的 skip_setup_items 不保證是同一套機制,
# 這些項目未經實際部署驗證,套用前請先在測試機上單獨確認有效。
UNVERIFIED_SKIP_SETUP_ITEMS = [
    "Appearance", "Keyboard", "SpokenLanguage", "Welcome", "TermsOfAddress",
    "Safety", "SafetyAndHandling", "ActionButton", "CameraButton",
    "RestoreCompleted", "WebContentFiltering", "DeviceToDeviceMigration",
]

SKIP_SETUP_ITEMS = VERIFIED_SKIP_SETUP_ITEMS + UNVERIFIED_SKIP_SETUP_ITEMS
_UNVERIFIED_SET = set(UNVERIFIED_SKIP_SETUP_ITEMS)

SKIP_SETUP_ITEM_LABELS = {
    "AppleID": "Apple帳號登入", "Android": "從Android移轉資料", "Biometric": "Face ID/Touch ID設定",
    "Diagnostics": "診斷資料回報", "DisplayTone": "True Tone顯示", "Location": "定位服務",
    "Passcode": "設定密碼", "Payment": "Apple Pay設定", "Privacy": "隱私權說明頁",
    "Restore": "從備份還原", "ScreenTime": "螢幕使用時間", "SIMSetup": "SIM卡設定",
    "TOS": "服務條款", "TVProviderSignIn": "電視業者登入", "TVRoom": "Apple TV房間設定",
    "UpdateCompleted": "更新完成畫面", "WatchMigration": "Apple Watch移轉",
    "Zoom": "顯示縮放設定", "Siri": "Siri設定", "OnBoarding": "功能導覽",
    "SoftwareUpdate": "軟體更新檢查", "iMessageAndFaceTime": "iMessage/FaceTime設定",
    # 以下為未驗證項目
    "Appearance": "外觀(淺色/深色)選擇", "Keyboard": "鍵盤設定畫面",
    "SpokenLanguage": "口述/語音朗讀設定", "Welcome": "Get Started 歡迎畫面",
    "TermsOfAddress": "稱謂設定", "Safety": "安全性說明頁",
    "SafetyAndHandling": "安全與使用說明頁", "ActionButton": "Action Button設定",
    "CameraButton": "相機控制按鈕設定", "RestoreCompleted": "還原完成畫面",
    "WebContentFiltering": "網頁內容過濾設定", "DeviceToDeviceMigration": "裝置間資料移轉",
}


def is_verified_skip_item(key):
    return key not in _UNVERIFIED_SET

# Apple DEP Profile 的欄位 schema(單一事實來源,前端表單依此動態產生)
DEP_PROFILE_FIELDS = [
    {"name": "profile_name", "label": "Profile 名稱 (顯示於 ABM/ASM)", "type": "text", "default": ""},
    {"name": "url", "label": "註冊入口 URL (enroll-server.py 的網址)", "type": "text", "default": ""},
    {"name": "support_phone_number", "label": "支援電話", "type": "text", "default": ""},
    {"name": "support_email_address", "label": "支援信箱", "type": "text", "default": ""},
    {"name": "language", "label": "語言代碼", "type": "text", "default": "zh"},
    {"name": "region", "label": "地區代碼", "type": "text", "default": "TW"},
    {"name": "is_supervised", "label": "設為 Supervised", "type": "checkbox", "default": True},
    {"name": "is_mandatory", "label": "強制註冊(不可跳過)", "type": "checkbox", "default": True},
    {"name": "is_mdm_removable", "label": "允許使用者移除 MDM(教育部署通常關閉)", "type": "checkbox", "default": False},
    {"name": "allow_pairing", "label": "允許與電腦配對", "type": "checkbox", "default": False},
    {"name": "await_device_configured", "label": "等待 MDM 完成設定才繼續(await_device_configured)", "type": "checkbox", "default": False},
    {"name": "auto_advance_setup", "label": "自動跳過已略過畫面(auto_advance_setup)", "type": "checkbox", "default": True},
    {"name": "is_multi_user", "label": "共享 iPad 模式(is_multi_user)", "type": "checkbox", "default": False},
    {"name": "is_return_to_service", "label": "Return to Service 模式", "type": "checkbox", "default": False},
    {"name": "do_not_use_profile_from_backup", "label": "還原備份時不要套用這份profile", "type": "checkbox", "default": False},
]


# ---------------------------------------------------------------------------
# 檔案 CRUD (本地範本儲存,跟真正套用到 nanodep 是兩回事)
# ---------------------------------------------------------------------------
def ensure_dir(dir_path):
    os.makedirs(dir_path, exist_ok=True)


def validate_filename(filename):
    if not FILENAME_RE.match(filename or ""):
        raise ValueError("檔名只能使用英文、數字、- 、_ ,並且必須以 .json 結尾(長度限制100字元)")


def default_template():
    apple_profile = {f["name"]: f["default"] for f in DEP_PROFILE_FIELDS}
    apple_profile["skip_setup_items"] = []
    apple_profile["anchor_certs"] = []
    apple_profile["supervising_host_certs"] = []
    return {
        "apple_profile": apple_profile,
        "last_applied_uuid": None,
        "last_applied_at": None,
    }


def list_dep_profiles(dir_path, groups_path=None):
    ensure_dir(dir_path)
    groups = utils.load_groups(groups_path) if groups_path else {}
    results = []
    for fname in sorted(os.listdir(dir_path)):
        if not fname.endswith(".json"):
            continue
        full_path = os.path.join(dir_path, fname)
        info = {
            "filename": fname,
            "parse_error": None,
            "is_protected": fname == DEFAULT_ENROLL_FILENAME,
        }
        if fname == DEFAULT_ENROLL_FILENAME:
            info["assigned_group"] = None
            info["assigned_group_label"] = "預設(所有新裝置 / 未指派群組)"
        else:
            assigned = utils.find_group_by_paired_file(groups, "enroll_json", fname) if groups_path else None
            info["assigned_group"] = assigned
            info["assigned_group_label"] = assigned or "(尚未指派給任何群組)"
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            apple_profile = data.get("apple_profile", {})
            info.update({
                "profile_name": apple_profile.get("profile_name", ""),
                "last_applied_uuid": data.get("last_applied_uuid"),
                "last_applied_at": data.get("last_applied_at"),
                "skip_count": len(apple_profile.get("skip_setup_items", [])),
            })
        except Exception as e:
            info["parse_error"] = str(e)
        results.append(info)

    # default-enroll.json 固定排最前面,其餘依檔名排序
    results.sort(key=lambda r: (r["filename"] != DEFAULT_ENROLL_FILENAME, r["filename"]))
    return results


def get_unpaired_groups(groups_path, current_filename=None):
    """列出目前還沒有配對enroll_json的群組(下拉選單用),
    如果 current_filename 目前已經配對了某個群組,那個群組也要一併列入(讓使用者看到目前的指派狀態)
    """
    groups = utils.load_groups(groups_path)
    result = []
    for name, info in sorted(groups.items()):
        if not info.get("enroll_json") or info.get("enroll_json") == current_filename:
            result.append(name)
    return result


def assign_dep_profile_to_group(groups_path, filename, group_name):
    """把這份enroll json指派給某個群組(1:1,自動清除原本佔用同一個檔案的其他群組)。
    group_name傳None代表取消指派。default-enroll.json不能被指派給任何實體群組(它是保留給預設情境用的)。
    """
    if filename == DEFAULT_ENROLL_FILENAME:
        raise ValueError(f"{DEFAULT_ENROLL_FILENAME} 是保留給「預設/未指派群組」使用,不能指派給實體群組")
    return utils.set_group_paired_file(groups_path, group_name, "enroll_json", filename)


def read_dep_profile(dir_path, filename):
    validate_filename(filename)
    full_path = os.path.join(dir_path, filename)
    with open(full_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_dep_profile(dir_path, filename, data):
    validate_filename(filename)
    ensure_dir(dir_path)
    # 基本驗證:確認能被JSON序列化,且有apple_profile這個key
    if "apple_profile" not in data:
        raise ValueError("缺少 apple_profile 欄位")
    json.dumps(data)  # 確認可序列化

    full_path = os.path.join(dir_path, filename)
    if os.path.exists(full_path):
        with open(full_path, "r", encoding="utf-8") as src, open(full_path + ".bak", "w", encoding="utf-8") as dst:
            dst.write(src.read())
    tmp_path = full_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, full_path)


def delete_dep_profile(dir_path, filename, groups_path=None):
    validate_filename(filename)
    if filename == DEFAULT_ENROLL_FILENAME:
        raise ValueError(f"{DEFAULT_ENROLL_FILENAME} 是系統預設檔案,不能刪除,只能編輯內容")
    full_path = os.path.join(dir_path, filename)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"找不到檔案 {filename}")
    os.remove(full_path)
    if groups_path:
        utils.clear_group_paired_file(groups_path, "enroll_json", filename)


def duplicate_dep_profile(dir_path, source_filename, new_filename):
    """複製一份現有範本另存新檔,重置 last_applied_uuid/at(因為這是全新、尚未套用過的副本,
    要指派給不同的群組,不該延用來源檔案的套用紀錄)。"""
    validate_filename(source_filename)
    validate_filename(new_filename)
    source_path = os.path.join(dir_path, source_filename)
    new_path = os.path.join(dir_path, new_filename)

    if not os.path.exists(source_path):
        raise FileNotFoundError(f"找不到來源檔案 {source_filename}")
    if os.path.exists(new_path):
        raise ValueError(f"檔案 {new_filename} 已經存在,請換一個檔名")

    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["last_applied_uuid"] = None
    data["last_applied_at"] = None

    tmp_path = new_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, new_path)


def build_apple_profile_payload(apple_profile):
    """把表單資料整理成要送給 Apple DEP /profile 端點的乾淨 JSON
    (拿掉 profile_uuid 這種只該出現在回應裡、不該出現在請求裡的欄位)"""
    payload = dict(apple_profile)
    payload.pop("profile_uuid", None)
    payload.setdefault("anchor_certs", [])
    payload.setdefault("supervising_host_certs", [])
    payload.setdefault("skip_setup_items", [])
    return payload


# ---------------------------------------------------------------------------
# 實際套用到 nanodep (呼叫 depserver proxy API,等同於手動跑那幾支 shell script)
# ---------------------------------------------------------------------------
class ApplyError(Exception):
    pass


def fetch_all_dep_devices(nanodep_base_url, nanodep_api_key, dep_name, limit=1000):
    """呼叫 Apple DEP 的 /server/devices (Fetch Devices),自動處理分頁(cursor),
    回傳所有裝置的完整清單(包含 profile_uuid、profile_status、device_family、model等)。
    """
    auth = ("depserver", nanodep_api_key)
    headers = {"User-Agent": "nanodep-tools/0", "Content-Type": "application/json;charset=UTF8"}
    url = f"{nanodep_base_url.rstrip('/')}/proxy/{dep_name}/server/devices"

    all_devices = []
    cursor = None
    while True:
        body = {"limit": limit}
        if cursor:
            body["cursor"] = cursor
        try:
            resp = requests.post(url, json=body, auth=auth, headers=headers, timeout=60)
        except requests.RequestException as e:
            raise ApplyError(f"抓取裝置清單失敗(連線錯誤): {e}")
        if resp.status_code >= 400:
            raise ApplyError(f"抓取裝置清單失敗(HTTP {resp.status_code}): {resp.text}")
        try:
            data = resp.json()
        except ValueError:
            raise ApplyError(f"抓取裝置清單回應不是合法JSON: {resp.text}")

        all_devices.extend(data.get("devices", []))
        more = str(data.get("more_to_follow", "")).lower() == "true"
        if more and data.get("cursor"):
            cursor = data["cursor"]
        else:
            break

    return all_devices


def assign_single_device(nanodep_base_url, nanodep_api_key, dep_name, profile_uuid, serial):
    """把單一裝置序號手動指派到指定的 profile_uuid(等同 dep-assign-profile.sh 但只針對一台)"""
    auth = ("depserver", nanodep_api_key)
    headers = {"User-Agent": "nanodep-tools/0", "Content-Type": "application/json;charset=UTF8"}
    url = f"{nanodep_base_url.rstrip('/')}/proxy/{dep_name}/profile/devices"
    try:
        resp = requests.post(url, json={"profile_uuid": profile_uuid, "devices": [serial]}, auth=auth, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise ApplyError(f"指派失敗(連線錯誤): {e}")
    if resp.status_code >= 400:
        raise ApplyError(f"指派失敗(HTTP {resp.status_code}): {resp.text}")
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


def apply_dep_profile(nanodep_base_url, nanodep_api_key, dep_name, apple_profile,
                       target_group, group_serials_lookup, depsyncer_restart_cmd, run_cmd_func):
    """完整套用流程,依序等同於:
    1. dep-define-profile.sh (POST /proxy/{name}/profile) -> 拿到新 profile_uuid
    2a. 若 target_group 為 None(預設):cfg-set-assigner.sh (PUT /v1/assigner/{name})
    2b. 若 target_group 是特定群組:dep-assign-profile.sh (POST /proxy/{name}/profile/devices),
        對象是這個群組目前的所有裝置序號
    3. dep-get-profile.sh (GET /proxy/{name}/profile?profile_uuid=...) 驗證套用結果
    4. 重新啟動 depsyncer (執行使用者設定的指令)

    回傳 dict,包含每個步驟的結果,任何一步失敗就丟出 ApplyError,並附上已經成功的步驟供除錯。
    """
    auth = ("depserver", nanodep_api_key)
    headers = {"User-Agent": "nanodep-tools/0", "Content-Type": "application/json;charset=UTF8"}
    steps = {}

    # Step 1: define profile
    payload = build_apple_profile_payload(apple_profile)
    define_url = f"{nanodep_base_url.rstrip('/')}/proxy/{dep_name}/profile"
    try:
        resp = requests.post(define_url, json=payload, auth=auth, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise ApplyError(f"定義 profile 失敗(連線錯誤): {e}")
    if resp.status_code >= 400:
        raise ApplyError(f"定義 profile 失敗(HTTP {resp.status_code}): {resp.text}")
    try:
        define_result = resp.json()
    except ValueError:
        raise ApplyError(f"定義 profile 回應不是合法JSON: {resp.text}")

    new_uuid = define_result.get("profile_uuid")
    if not new_uuid:
        raise ApplyError(f"定義 profile 的回應裡沒有 profile_uuid: {define_result}")
    steps["define"] = define_result

    # Step 2: 設定assigner 或 指派給特定群組的裝置
    if target_group:
        serials = group_serials_lookup(target_group)
        if not serials:
            steps["assign"] = {"skipped": True, "reason": f"群組「{target_group}」目前沒有任何裝置序號,略過指派"}
        else:
            assign_url = f"{nanodep_base_url.rstrip('/')}/proxy/{dep_name}/profile/devices"
            assign_body = {"profile_uuid": new_uuid, "devices": serials}
            try:
                resp = requests.post(assign_url, json=assign_body, auth=auth, headers=headers, timeout=30)
            except requests.RequestException as e:
                raise ApplyError(f"指派給群組裝置失敗(連線錯誤): {e}")
            if resp.status_code >= 400:
                raise ApplyError(f"指派給群組裝置失敗(HTTP {resp.status_code}): {resp.text}")
            try:
                steps["assign"] = resp.json()
            except ValueError:
                steps["assign"] = {"raw": resp.text}
    else:
        assigner_url = f"{nanodep_base_url.rstrip('/')}/v1/assigner/{dep_name}"
        try:
            resp = requests.put(assigner_url, params={"profile_uuid": new_uuid}, auth=auth, headers=headers, timeout=30)
        except requests.RequestException as e:
            raise ApplyError(f"設定 assigner 失敗(連線錯誤): {e}")
        if resp.status_code >= 400:
            raise ApplyError(f"設定 assigner 失敗(HTTP {resp.status_code}): {resp.text}")
        try:
            steps["set_assigner"] = resp.json()
        except ValueError:
            steps["set_assigner"] = {"raw": resp.text}

    # Step 3: 驗證(get profile)
    get_url = f"{nanodep_base_url.rstrip('/')}/proxy/{dep_name}/profile"
    try:
        resp = requests.get(get_url, params={"profile_uuid": new_uuid}, auth=auth, headers=headers, timeout=30)
        steps["verify"] = resp.json() if resp.status_code < 400 else {"error": resp.text, "status_code": resp.status_code}
    except requests.RequestException as e:
        steps["verify"] = {"error": str(e)}

    # Step 4: 重新啟動 depsyncer
    if depsyncer_restart_cmd:
        rc, out, err = run_cmd_func(depsyncer_restart_cmd)
        steps["restart_depsyncer"] = {"returncode": rc, "stdout": out, "stderr": err}
    else:
        steps["restart_depsyncer"] = {"skipped": True, "reason": "尚未設定 depsyncer 重啟指令(NANODEP_DEPSYNCER_RESTART_CMD)"}

    return {"new_profile_uuid": new_uuid, "steps": steps}
