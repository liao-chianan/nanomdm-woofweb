# -*- coding: utf-8 -*-
"""
mobileconfig 描述檔管理:
- PAYLOAD_SCHEMA 定義每種 payload 支援哪些欄位(單一事實來源,前端表單依此動態產生)
- 用 plistlib 組建/解析,保證輸出一定是合法的 plist XML
- 針對已知的常見錯誤(例如 PayloadRemovalDisallowed 搭配 MDM payload)給出警告
"""
import os
import plistlib
import re
import tempfile
import uuid

import utils
import utils_signing

FILENAME_RE = re.compile(r'^[A-Za-z0-9_\-]{1,100}\.mobileconfig$')
PROTECTED_FILENAMES = {"enroll-template.mobileconfig", "baseline.mobileconfig"}

# ---------------------------------------------------------------------------
# Payload Schema:前端表單與後端組建/驗證的單一事實來源
# ---------------------------------------------------------------------------
PAYLOAD_SCHEMA = {
    "scep": {
        "label": "SCEP (裝置身份憑證)",
        "payload_type": "com.apple.security.scep",
        "singular": True,
        "help": "叫裝置跟憑證伺服器要一張「裝置專屬的身分憑證」,常見用途是802.1x憑證式Wi-Fi登入、VPN用戶端憑證等"
                "需要憑證身分驗證的服務。如果你不需要這類憑證式驗證,不用填這個Payload,不填完全沒有負面影響。"
                "系統預設的「精簡註冊描述檔」通常會搭配MDM Payload裡的「使用SCEP身份憑證」勾選項一起用,"
                "這種情況下才是必填。",
        "fields": [
            {"name": "URL", "label": "SCEP URL", "type": "text", "default": ""},
            {"name": "Challenge", "label": "Challenge", "type": "text", "default": ""},
            {"name": "Subject_O", "label": "Subject O (組織名稱)", "type": "text", "default": ""},
            {"name": "Subject_CN", "label": "Subject CN (可填 __SERIAL_PLACEHOLDER__ 讓 enroll-server.py 動態替換成裝置序號)", "type": "text", "default": ""},
            {"name": "Keysize", "label": "金鑰長度", "type": "select", "options": ["1024", "2048"], "default": "2048"},
        ],
    },
    "mdm": {
        "label": "MDM (裝置管理連線)",
        "payload_type": "com.apple.mdm",
        "singular": True,
        "help": "這個Payload的作用是讓裝置連上MDM伺服器、變成受管理裝置(Server URL是連線網址、APNs Topic是推播頻道識別碼)。"
                "⚠️ 你們學校的裝置是透過ADE自動註冊,MDM連線這件事在裝置註冊當下就已經處理好了,一般的群組專屬描述檔"
                "不需要用到這個Payload,請留空不要填。只有系統預設、不能刪除的那份「精簡註冊描述檔」才需要正確填寫這裡"
                "(那份就是裝置實際拿去完成MDM註冊用的描述檔本身)。",
        "fields": [
            {"name": "ServerURL", "label": "Server URL", "type": "text", "default": ""},
            {"name": "Topic", "label": "APNs Topic", "type": "text", "default": ""},
            {"name": "UseSCEPIdentity", "label": "使用上方 SCEP 的身份憑證(勾選才會產生SCEP+MDM連動)", "type": "checkbox", "default": True},
            {"name": "SignMessage", "label": "簽署訊息 (SignMessage)", "type": "checkbox", "default": True},
            {"name": "CheckOutWhenRemoved", "label": "移除時通知伺服器 (CheckOutWhenRemoved)", "type": "checkbox", "default": True},
        ],
    },
    "wifi": {
        "label": "Wi-Fi",
        "payload_type": "com.apple.wifi.managed",
        "singular": False,
        "fields": [
            {"name": "SSID_STR", "label": "SSID", "type": "text", "default": ""},
            {"name": "EncryptionType", "label": "加密類型", "type": "select", "options": ["None", "WEP", "WPA", "WPA2"], "default": "None"},
            {"name": "Password", "label": "密碼(選填,開放式網路留空)", "type": "text", "default": ""},
            {"name": "AutoJoin", "label": "自動加入", "type": "checkbox", "default": True},
            {"name": "HIDDEN_NETWORK", "label": "隱藏網路", "type": "checkbox", "default": False},
            {"name": "DisableAssociationMACRandomization", "label": "停用 MAC 位址隨機化", "type": "checkbox", "default": True},
        ],
    },
    "webclip": {
        "label": "Web Clip (主畫面捷徑)",
        "payload_type": "com.apple.webClip.managed",
        "singular": False,
        "fields": [
            {"name": "Label", "label": "顯示名稱", "type": "text", "default": ""},
            {"name": "URL", "label": "網址", "type": "text", "default": ""},
            {"name": "IsRemovable", "label": "允許使用者移除", "type": "checkbox", "default": True},
            {"name": "FullScreen", "label": "全螢幕模式", "type": "checkbox", "default": False},
            {"name": "Precomposed", "label": "圖示不加特效 (Precomposed)", "type": "checkbox", "default": False},
        ],
    },
    "shareddevice": {
        "label": "鎖定螢幕訊息 / 共用裝置資訊 (需裝置為 Supervised 狀態)",
        "payload_type": "com.apple.shareddeviceconfiguration",
        "singular": True,
        "fields": [
            {"name": "AssetTagInformation", "label": "資產標籤資訊(顯示在鎖定畫面上)", "type": "text", "default": ""},
            {
                "name": "IfLostReturnToMessage",
                "label": "鎖定螢幕下方訊息(Lock Screen Footnote)",
                "type": "text", "default": "",
                "help": "顯示在裝置登入畫面與鎖定畫面下方的文字,例如「如拾獲請聯繫OO國小資訊組 04-XXXXXXX」。"
                        "Apple 官方把這個功能稱作「Lock Screen footnote」,實際存進描述檔的鍵值叫"
                        "IfLostReturnToMessage,兩者是同一件事。",
            },
        ],
    },
    "restrictions": {
        "label": "取用限制 (Restrictions)",
        "payload_type": "com.apple.applicationaccess",
        "singular": True,
        "fields": [
            {"name": "allowCamera", "label": "允許相機", "type": "checkbox", "default": True},
            {"name": "allowScreenShot", "label": "允許螢幕截圖", "type": "checkbox", "default": True},
            {"name": "allowAppInstallation", "label": "允許安裝 App", "type": "checkbox", "default": True},
            {"name": "allowAppRemoval", "label": "允許移除 App", "type": "checkbox", "default": True},
            {"name": "allowUIAppInstallation", "label": "允許透過 App Store 安裝(圖形介面)", "type": "checkbox", "default": True},
            {"name": "allowEraseContentAndSettings", "label": "允許清除內容與設定", "type": "checkbox", "default": True},
            {"name": "allowSystemAppRemoval", "label": "允許移除系統內建 App", "type": "checkbox", "default": True},
            {"name": "allowPasscodeModification", "label": "允許修改密碼", "type": "checkbox", "default": True},
            {"name": "allowAutoUnlock", "label": "允許 Apple Watch 自動解鎖", "type": "checkbox", "default": False},
            {"name": "allowFindMyDevice", "label": "允許尋找我的裝置", "type": "checkbox", "default": False},
            {"name": "allowFindMyFriends", "label": "允許尋找我的朋友", "type": "checkbox", "default": False},
            {"name": "allowInAppPurchases", "label": "允許 App 內購買", "type": "checkbox", "default": False},
            {"name": "allowBookstoreErotica", "label": "允許書店成人內容", "type": "checkbox", "default": False},
            {"name": "allowExplicitContent", "label": "允許成人內容(音樂/Podcast等)", "type": "checkbox", "default": False},
            {"name": "allowCloudBackup", "label": "允許 iCloud 備份", "type": "checkbox", "default": True},
            {"name": "allowCloudPhotoLibrary", "label": "允許 iCloud 相片圖庫", "type": "checkbox", "default": False},
            {"name": "allowGameCenter", "label": "允許 Game Center", "type": "checkbox", "default": False},
            {"name": "allowMultiplayerGaming", "label": "允許多人連線遊戲", "type": "checkbox", "default": True},
            {"name": "allowAddingGameCenterFriends", "label": "允許新增 Game Center 好友", "type": "checkbox", "default": True},
            {"name": "allowAssistant", "label": "允許 Siri", "type": "checkbox", "default": True},
            {"name": "allowDiagnosticSubmission", "label": "允許傳送診斷資料給 Apple", "type": "checkbox", "default": False},
            {"name": "allowApplePersonalizedAdvertising", "label": "允許 Apple 個人化廣告", "type": "checkbox", "default": False},
            {"name": "allowDeviceNameModification", "label": "允許修改裝置名稱", "type": "checkbox", "default": True},
            {"name": "allowEnablingRestrictions", "label": "允許使用者自行開啟其他取用限制", "type": "checkbox", "default": True},
            {"name": "forceAutomaticDateAndTime", "label": "強制自動日期與時間", "type": "checkbox", "default": True},
            {"name": "forceLimitAdTracking", "label": "強制限制廣告追蹤", "type": "checkbox", "default": True},
            {"name": "forceWiFiPowerOn", "label": "強制開啟 Wi-Fi", "type": "checkbox", "default": True},
            {"name": "forceClassroomAutomaticallyJoinClasses", "label": "強制自動加入課堂 (Classroom)", "type": "checkbox", "default": True},
            {"name": "forceClassroomUnpromptedAppAndDeviceLock", "label": "允許 Classroom 直接鎖定裝置/App(免詢問)", "type": "checkbox", "default": True},
            {"name": "forceClassroomUnpromptedScreenObservation", "label": "允許 Classroom 直接查看螢幕(免詢問)", "type": "checkbox", "default": True},
            {
                "name": "blockedAppBundleIDs", "label": "原生 App 限制", "type": "app_checklist", "default": [],
                "help": "勾選後將會限制使用(App 會被隱藏、無法啟動)。只對監管模式(Supervised)的裝置生效,"
                        "透過 DEP/ASM 自動註冊的裝置預設就是監管模式。「設定」App 是 Apple 系統層級的限制,"
                        "無法被隱藏,不會列在下面的選項裡。「遊戲」對應的是 2025 年(iOS/iPadOS 26)才推出的"
                        "全新 Games App;獨立的「Game Center」App 其實從 2016 年(iOS 10)就已經被移除,"
                        "功能併入「設定」裡,所以沒有獨立的 Bundle ID 可以封鎖——如果這裡勾選「遊戲」後"
                        "還是沒有效果,可能是裝置的 iOS 版本較舊、還沒有這個新 App,或是 Apple 對這個較新的"
                        "系統 App 尚未完全開放透過限制清單封鎖(已知有其他管理者回報過這個狀況),建議勾選後"
                        "實際到裝置上測試確認。跟上方「允許 Game Center」開關是兩個獨立的設定,請避免"
                        "兩邊互相矛盾(例如一邊開著卻又勾選這裡禁用)。「電話」App 只有 iPhone 才有,"
                        "iPad 沒有電話功能,在 iPad 上勾選這個不會有任何效果。",
                "options": [
                    {"bundle_id": "com.apple.stocks", "label": "股市"},
                    {"bundle_id": "com.apple.Health", "label": "健康"},
                    {"bundle_id": "com.apple.tips", "label": "提示"},
                    {"bundle_id": "com.apple.podcasts", "label": "Podcast"},
                    {"bundle_id": "com.apple.Music", "label": "音樂"},
                    {"bundle_id": "com.apple.tv", "label": "TV"},
                    {"bundle_id": "com.apple.Home", "label": "家庭"},
                    {"bundle_id": "com.apple.games", "label": "遊戲"},
                    {"bundle_id": "com.apple.iBooks", "label": "書籍"},
                    {"bundle_id": "com.apple.MobileSMS", "label": "訊息"},
                    {"bundle_id": "com.apple.reminders", "label": "提醒事項"},
                    {"bundle_id": "com.apple.journal", "label": "日誌"},
                    {"bundle_id": "com.apple.MobileAddressBook", "label": "聯絡人"},
                    {"bundle_id": "com.apple.mobilephone", "label": "電話"},
                    {"bundle_id": "com.apple.facetime", "label": "FaceTime"},
                    {"bundle_id": "com.apple.MobileStore", "label": "iTunes Store"},
                ],
            },
        ],
    },
}

TOP_LEVEL_FIELDS = [
    {"name": "PayloadDisplayName", "label": "描述檔顯示名稱", "type": "text", "default": ""},
    {"name": "PayloadDescription", "label": "描述檔說明", "type": "text", "default": ""},
    {"name": "PayloadOrganization", "label": "組織名稱", "type": "text", "default": ""},
    {"name": "PayloadIdentifier", "label": "PayloadIdentifier (反向網域格式,例如 tw.edu.example.baseline)", "type": "text", "default": "",
     "help": "這份描述檔在裝置上的「身分識別碼」。裝置收到描述檔時,會用這個值判斷「這是同一份描述檔的新版本」還是"
             "「全新獨立的描述檔」——如果跟裝置上已經安裝的某份描述檔用一樣的值,推送下去會直接取代對方,不是疊加安裝,"
             "對方原本有、這份沒有的設定會憑空消失。請確保每一份描述檔都用不同的值(存檔時系統會自動檢查是否重複,"
             "重複會擋下不給存)。"},
    {"name": "PayloadRemovalDisallowed", "label": "不可移除 (只有 Supervised + ADE 流程安裝才能生效,詳見下方警告)", "type": "checkbox", "default": False},
]


# ---------------------------------------------------------------------------
# 檔案列表 / 刪除
# ---------------------------------------------------------------------------
def ensure_mobileconfig_dir(dir_path):
    os.makedirs(dir_path, exist_ok=True)


def list_mobileconfig_files(dir_path, groups_path=None):
    ensure_mobileconfig_dir(dir_path)
    groups = utils.load_groups(groups_path) if groups_path else {}
    results = []
    for fname in sorted(os.listdir(dir_path)):
        if not fname.endswith(".mobileconfig"):
            continue
        full_path = os.path.join(dir_path, fname)
        is_protected = fname in PROTECTED_FILENAMES
        info = {
            "filename": fname,
            "size": os.path.getsize(full_path),
            "mtime": os.path.getmtime(full_path),
            "display_name": "",
            "payload_types": [],
            "parse_error": None,
            "is_protected": is_protected,
            "is_signed": False,
        }
        if is_protected:
            info["assigned_group"] = None
            info["assigned_group_label"] = "系統預設(全域使用,非個別群組專屬)"
        else:
            assigned = utils.find_group_by_paired_file(groups, "mobileconfig", fname) if groups_path else None
            info["assigned_group"] = assigned
            info["assigned_group_label"] = assigned or "(尚未指派給任何群組)"
        try:
            with open(full_path, "rb") as f:
                raw_bytes = f.read()
            info["is_signed"] = utils_signing.is_signed_mobileconfig_bytes(raw_bytes)
            parsed = utils_signing.parse_mobileconfig_bytes(raw_bytes)
            info["display_name"] = parsed.get("PayloadDisplayName", "")
            info["payload_types"] = [p.get("PayloadType", "?") for p in parsed.get("PayloadContent", [])]
        except Exception as e:
            info["parse_error"] = str(e)
        results.append(info)

    # 兩個系統預設檔案固定排最前面(enroll-template在baseline之前),其餘依檔名排序
    protected_order = {"enroll-template.mobileconfig": 0, "baseline.mobileconfig": 1}
    results.sort(key=lambda r: (protected_order.get(r["filename"], 2), r["filename"]))
    return results


def get_unpaired_groups(groups_path, current_filename=None):
    """列出目前還沒有配對mobileconfig的群組(下拉選單用),
    如果 current_filename 目前已經配對了某個群組,那個群組也要一併列入。"""
    groups = utils.load_groups(groups_path)
    result = []
    for name, info in sorted(groups.items()):
        if not info.get("mobileconfig") or info.get("mobileconfig") == current_filename:
            result.append(name)
    return result


def assign_mobileconfig_to_group(groups_path, filename, group_name):
    """把這份mobileconfig指派給某個群組(1:1,自動清除原本佔用同一個檔案的其他群組)。
    系統預設的兩份檔案(enroll-template/baseline)不能被指派給特定群組,它們是全域使用的。
    """
    if filename in PROTECTED_FILENAMES:
        raise ValueError(f"{filename} 是系統預設檔案(全域使用),不能指派給特定群組")
    return utils.set_group_paired_file(groups_path, group_name, "mobileconfig", filename)


def validate_filename(filename):
    if not FILENAME_RE.match(filename or ""):
        raise ValueError(
            "檔名只能使用英文、數字、- 、_ ,並且必須以 .mobileconfig 結尾(長度限制100字元)"
        )


def delete_mobileconfig(dir_path, filename, groups_path=None):
    validate_filename(filename)
    if filename in PROTECTED_FILENAMES:
        raise ValueError(f"{filename} 是系統預設檔案,不能刪除,只能編輯內容")
    full_path = os.path.join(dir_path, filename)
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"找不到檔案 {filename}")
    os.remove(full_path)
    if groups_path:
        utils.clear_group_paired_file(groups_path, "mobileconfig", filename)


def duplicate_mobileconfig(dir_path, source_filename, new_filename,
                            sign_with_cert_path=None, sign_with_key_path=None, sign_with_ca_path=None):
    """複製一份現有描述檔另存新檔。
    1. 所有 PayloadUUID(含頂層跟每個payload,多實例的wifi/webclip每一份也都要)全部重新產生,
       不能沿用來源檔案的UUID,否則裝置可能會把兩份檔案搞混,或者其中一份被判定成同一個payload的更新。
    2. PayloadIdentifier 也要跟著調整:用新檔名(去掉.mobileconfig副檔名)當作識別字串,
       附加到來源檔案原本的PayloadIdentifier結尾。如果不這樣做,複製出來的新檔案會跟來源檔案
       用一模一樣的PayloadIdentifier,推送到裝置上時,後推送的那份會直接取代先推送的那份
       (不是疊加),而不是像期望的那樣是兩份互相獨立、各自套用在不同群組的描述檔。

    sign_with_cert_path/sign_with_key_path:選填,邏輯跟save_mobileconfig()一致——提供的話,
    寫入新檔案前會套用簽署;不提供則寫入未簽署版本。這是為了修正曾經發生過的問題:
    再製功能原本無論如何都只會寫出未簽署版本,不會跟隨目前系統的簽署設定,導致複製出來的
    新檔案,簽署狀態跟來源檔案(或系統目前的簽署設定)不一致。
    """
    validate_filename(source_filename)
    validate_filename(new_filename)
    source_path = os.path.join(dir_path, source_filename)
    new_path = os.path.join(dir_path, new_filename)

    if not os.path.exists(source_path):
        raise FileNotFoundError(f"找不到來源檔案 {source_filename}")
    if os.path.exists(new_path):
        raise ValueError(f"檔案 {new_filename} 已經存在,請換一個檔名")

    with open(source_path, "rb") as f:
        raw_bytes = f.read()
    parsed = utils_signing.parse_mobileconfig_bytes(raw_bytes)

    suffix = os.path.splitext(new_filename)[0]
    old_top_identifier = parsed.get("PayloadIdentifier", "")
    new_top_identifier = f"{old_top_identifier}.{suffix}" if old_top_identifier else suffix
    parsed["PayloadIdentifier"] = new_top_identifier

    parsed["PayloadUUID"] = str(uuid.uuid4()).upper()
    for payload in parsed.get("PayloadContent", []):
        payload["PayloadUUID"] = str(uuid.uuid4()).upper()
        old_payload_identifier = payload.get("PayloadIdentifier", "")
        if old_top_identifier and old_payload_identifier.startswith(old_top_identifier):
            # 子payload的識別碼通常是「頂層識別碼.xxx」這種前綴關係,把前綴換成新的頂層識別碼即可
            payload["PayloadIdentifier"] = old_payload_identifier.replace(old_top_identifier, new_top_identifier, 1)
        elif old_payload_identifier:
            payload["PayloadIdentifier"] = f"{old_payload_identifier}.{suffix}"

    plist_bytes = plistlib.dumps(parsed)

    final_bytes = plist_bytes
    if sign_with_cert_path and sign_with_key_path:
        signed_bytes, sign_err = utils_signing.sign_plist_bytes(
            plist_bytes, sign_with_cert_path, sign_with_key_path, ca_cert_path=sign_with_ca_path,
        )
        if signed_bytes is not None:
            final_bytes = signed_bytes
        # 簽署失敗時不拋出例外,直接寫入未簽署版本即可(再製動作本身不該因為簽署
        # 這個附加功能出問題就整個失敗)

    tmp_path = new_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(final_bytes)
    os.replace(tmp_path, new_path)


# ---------------------------------------------------------------------------
# 讀取現有檔案 -> 轉成編輯器用的表單結構
# ---------------------------------------------------------------------------
def _extract_subject_attr(subject_array, attr_name):
    """Subject 格式為 [[[attr, value]], [[attr, value]], ...] 這種巢狀RDN結構,
    掃描所有項目找出指定屬性(例如 O 或 CN)的值,而不假設固定在哪個index。"""
    for rdn_set in subject_array or []:
        for pair in rdn_set or []:
            if len(pair) == 2 and pair[0] == attr_name:
                return pair[1]
    return ""


def read_mobileconfig_as_form(dir_path, filename):
    """讀取既有的 .mobileconfig,盡量對應回 PAYLOAD_SCHEMA 的表單結構。
    無法辨識的 payload 類型會原樣保留在 unmanaged_payloads,存檔時會照樣寫回去,
    不會因為編輯器不認得就把它砍掉。
    """
    validate_filename(filename)
    full_path = os.path.join(dir_path, filename)
    with open(full_path, "rb") as f:
        raw_bytes = f.read()
    parsed = utils_signing.parse_mobileconfig_bytes(raw_bytes)

    top_level = {}
    for field in TOP_LEVEL_FIELDS:
        top_level[field["name"]] = parsed.get(field["name"], field["default"])

    payloads_form = {}
    unmanaged_payloads = []

    type_to_key = {v["payload_type"]: k for k, v in PAYLOAD_SCHEMA.items()}

    for payload in parsed.get("PayloadContent", []):
        ptype = payload.get("PayloadType")
        schema_key = type_to_key.get(ptype)
        if not schema_key:
            unmanaged_payloads.append(payload)
            continue

        schema = PAYLOAD_SCHEMA[schema_key]
        fields_out = {}
        # SCEP 的實際設定值(URL/Challenge/Subject等)巢狀在 PayloadContent 子字典裡,
        # 其他 payload 類型(WiFi/MDM/Restrictions等)是直接攤平在 payload 本身,結構不同要分開處理
        source = payload.get("PayloadContent", {}) if schema_key == "scep" else payload
        for field in schema["fields"]:
            fname = field["name"]
            if schema_key == "scep" and fname in ("Subject_O", "Subject_CN"):
                attr_name = "O" if fname == "Subject_O" else "CN"
                fields_out[fname] = _extract_subject_attr(source.get("Subject", []), attr_name)
            elif schema_key == "mdm" and fname == "UseSCEPIdentity":
                fields_out[fname] = bool(payload.get("IdentityCertificateUUID"))
            elif fname == "Keysize":
                fields_out[fname] = str(source.get(fname, field["default"]))
            else:
                fields_out[fname] = source.get(fname, field["default"])

        if schema.get("singular", True):
            payloads_form[schema_key] = {"enabled": True, "fields": fields_out, "uuid": payload.get("PayloadUUID")}
        else:
            # 非singular(wifi/webclip):同一種類型可能出現多次,收集成instances清單
            entry = payloads_form.setdefault(schema_key, {"enabled": True, "instances": []})
            entry["instances"].append({"fields": fields_out, "uuid": payload.get("PayloadUUID")})

    # 沒被勾選的payload類型也要回傳enabled:False,讓前端知道有這個選項存在
    for key, schema in PAYLOAD_SCHEMA.items():
        if key not in payloads_form:
            if schema.get("singular", True):
                payloads_form[key] = {"enabled": False, "fields": {}, "uuid": None}
            else:
                payloads_form[key] = {"enabled": False, "instances": []}

    return {
        "top_level": top_level,
        "payloads": payloads_form,
        "unmanaged_payloads": unmanaged_payloads,
    }


# ---------------------------------------------------------------------------
# 表單結構 -> 組建 plist bytes
# ---------------------------------------------------------------------------
def _build_scep_payload(fields, payload_uuid, identifier_prefix):
    subject_o = fields.get("Subject_O", "")
    subject_cn = fields.get("Subject_CN", "")
    subject = []
    if subject_o:
        subject.append([["O", subject_o]])
    if subject_cn:
        subject.append([["CN", subject_cn]])
    return {
        "PayloadType": "com.apple.security.scep",
        "PayloadIdentifier": f"{identifier_prefix}.scep",
        "PayloadUUID": payload_uuid,
        "PayloadVersion": 1,
        "PayloadDisplayName": "SCEP",
        "PayloadDescription": "取得裝置身份憑證",
        "PayloadContent": {
            "URL": fields.get("URL", ""),
            "Challenge": fields.get("Challenge", ""),
            "Keysize": int(fields.get("Keysize", 2048)),
            "Key Type": "RSA",
            "Key Usage": 5,
            "Subject": subject,
        },
    }


def _build_mdm_payload(fields, payload_uuid, identifier_prefix, scep_uuid):
    payload = {
        "PayloadType": "com.apple.mdm",
        "PayloadIdentifier": f"{identifier_prefix}.mdm",
        "PayloadUUID": payload_uuid,
        "PayloadVersion": 1,
        "PayloadDisplayName": "MDM",
        "PayloadDescription": "連線至 MDM 伺服器",
        "ServerURL": fields.get("ServerURL", ""),
        "Topic": fields.get("Topic", ""),
        "AccessRights": 8191,
        "CheckOutWhenRemoved": bool(fields.get("CheckOutWhenRemoved", True)),
        "SignMessage": bool(fields.get("SignMessage", True)),
    }
    if fields.get("UseSCEPIdentity") and scep_uuid:
        payload["IdentityCertificateUUID"] = scep_uuid
    return payload


def _build_wifi_payload(fields, payload_uuid, identifier_prefix, instance_idx=0):
    payload = {
        "PayloadType": "com.apple.wifi.managed",
        "PayloadIdentifier": f"{identifier_prefix}.wifi.{instance_idx}",
        "PayloadUUID": payload_uuid,
        "PayloadVersion": 1,
        "PayloadDisplayName": f"Wi-Fi ({fields.get('SSID_STR', '')})" if fields.get("SSID_STR") else "Wi-Fi",
        "PayloadDescription": "配置 Wi-Fi 設定",
        "SSID_STR": fields.get("SSID_STR", ""),
        "EncryptionType": fields.get("EncryptionType", "None"),
        "AutoJoin": bool(fields.get("AutoJoin", True)),
        "HIDDEN_NETWORK": bool(fields.get("HIDDEN_NETWORK", False)),
        "DisableAssociationMACRandomization": bool(fields.get("DisableAssociationMACRandomization", True)),
    }
    if fields.get("Password"):
        payload["Password"] = fields.get("Password")
    return payload


def _build_webclip_payload(fields, payload_uuid, identifier_prefix, instance_idx=0):
    return {
        "PayloadType": "com.apple.webClip.managed",
        "PayloadIdentifier": f"{identifier_prefix}.webclip.{instance_idx}",
        "PayloadUUID": payload_uuid,
        "PayloadVersion": 1,
        "PayloadDisplayName": fields.get("Label") or "Web Clip",
        "PayloadDescription": "配置 Web Clip 設定",
        "Label": fields.get("Label", ""),
        "URL": fields.get("URL", ""),
        "IsRemovable": bool(fields.get("IsRemovable", True)),
        "FullScreen": bool(fields.get("FullScreen", False)),
        "Precomposed": bool(fields.get("Precomposed", False)),
    }


def _build_shareddevice_payload(fields, payload_uuid, identifier_prefix):
    return {
        "PayloadType": "com.apple.shareddeviceconfiguration",
        "PayloadIdentifier": f"{identifier_prefix}.shareddevice",
        "PayloadUUID": payload_uuid,
        "PayloadVersion": 1,
        "PayloadDisplayName": "共用裝置資訊",
        "PayloadDescription": "設定共享裝置的擁有者資訊",
        "AssetTagInformation": fields.get("AssetTagInformation", ""),
        "IfLostReturnToMessage": fields.get("IfLostReturnToMessage", ""),
    }


def _build_restrictions_payload(fields, payload_uuid, identifier_prefix):
    payload = {
        "PayloadType": "com.apple.applicationaccess",
        "PayloadIdentifier": f"{identifier_prefix}.restrictions",
        "PayloadUUID": payload_uuid,
        "PayloadVersion": 1,
        "PayloadDisplayName": "取用限制",
        "PayloadDescription": "設定取用限制",
    }
    for field in PAYLOAD_SCHEMA["restrictions"]["fields"]:
        fname = field["name"]
        if field["type"] == "app_checklist":
            # 陣列型別,不能用bool()強制轉換(非空陣列會被誤判成True,空陣列被誤判成False,
            # 完全不是原本要存的陣列內容),直接存成list
            value = fields.get(fname, field["default"])
            payload[fname] = list(value) if value else []
        else:
            payload[fname] = bool(fields.get(fname, field["default"]))
    return payload


_BUILDERS = {
    "scep": _build_scep_payload,
    "wifi": _build_wifi_payload,
    "webclip": _build_webclip_payload,
    "shareddevice": _build_shareddevice_payload,
    "restrictions": _build_restrictions_payload,
}


def build_mobileconfig(top_level, payloads, unmanaged_payloads=None, existing_uuids=None):
    """把編輯器的表單資料組成完整的 mobileconfig plist bytes。
    top_level: dict,對應 TOP_LEVEL_FIELDS
    payloads: { schema_key: {"enabled": bool, "fields": {...}} }
    existing_uuids: { schema_key: uuid_str } 編輯既有檔案時延用原本的 PayloadUUID,
                     避免每次存檔都被裝置當成新描述檔重新安裝
    回傳 (plist_bytes, warnings) - warnings 是字串list,不會阻擋存檔,但要顯示給使用者看
    """
    unmanaged_payloads = unmanaged_payloads or []
    existing_uuids = existing_uuids or {}
    warnings = []

    identifier_prefix = (top_level.get("PayloadIdentifier") or "tw.edu.mobileconfig").strip()

    def get_uuid(key):
        return existing_uuids.get(key) or str(uuid.uuid4()).upper()

    payload_contents = []
    scep_uuid = None

    if payloads.get("scep", {}).get("enabled"):
        scep_uuid = get_uuid("scep")
        payload_contents.append(_build_scep_payload(payloads["scep"]["fields"], scep_uuid, identifier_prefix))

    if payloads.get("mdm", {}).get("enabled"):
        mdm_fields = payloads["mdm"]["fields"]
        if mdm_fields.get("UseSCEPIdentity") and not scep_uuid:
            warnings.append("MDM payload 勾選了「使用 SCEP 身份憑證」,但這份描述檔沒有包含 SCEP payload,IdentityCertificateUUID 不會被設定。")
        payload_contents.append(_build_mdm_payload(mdm_fields, get_uuid("mdm"), identifier_prefix, scep_uuid))

    for key in ("shareddevice", "restrictions"):
        if payloads.get(key, {}).get("enabled"):
            builder = _BUILDERS[key]
            payload_contents.append(builder(payloads[key]["fields"], get_uuid(key), identifier_prefix))
            if key == "shareddevice":
                warnings.append("共用裝置資訊 (SharedDeviceConfiguration) 需要裝置為 Supervised 狀態才能安裝成功,非 Supervised 裝置安裝這份描述檔可能會被判定為無效。")

    # wifi / webclip 是非singular,支援多組實例,每個實例各自延用(或新產生)自己的UUID
    for key in ("wifi", "webclip"):
        entry = payloads.get(key, {})
        if not entry.get("enabled"):
            continue
        builder = _BUILDERS[key]
        existing_list = existing_uuids.get(key) or []
        for idx, instance in enumerate(entry.get("instances", [])):
            instance_uuid = existing_list[idx] if idx < len(existing_list) else str(uuid.uuid4()).upper()
            payload_contents.append(builder(instance.get("fields", {}), instance_uuid, identifier_prefix, idx))

    payload_contents.extend(unmanaged_payloads)

    has_mdm = payloads.get("mdm", {}).get("enabled")
    if has_mdm and top_level.get("PayloadRemovalDisallowed"):
        warnings.append(
            "這份描述檔同時包含 MDM payload 且勾選了「不可移除」。"
            "只有透過 ADE(DEP)流程安裝且裝置已是 Supervised 狀態,才允許這樣設定,"
            "否則裝置安裝時會直接判定「描述檔無效」。手動/非ADE安裝的註冊描述檔請務必取消勾選。"
        )

    if not payload_contents:
        warnings.append("目前沒有勾選任何 payload,這會是一份空的描述檔。")

    top = {
        "PayloadContent": payload_contents,
        "PayloadDescription": top_level.get("PayloadDescription", ""),
        "PayloadDisplayName": top_level.get("PayloadDisplayName", ""),
        "PayloadIdentifier": identifier_prefix,
        "PayloadOrganization": top_level.get("PayloadOrganization", ""),
        "PayloadType": "Configuration",
        "PayloadUUID": existing_uuids.get("_top") or str(uuid.uuid4()).upper(),
        "PayloadVersion": 1,
    }
    if top_level.get("PayloadRemovalDisallowed"):
        top["PayloadRemovalDisallowed"] = True

    plist_bytes = plistlib.dumps(top)
    return plist_bytes, warnings


def check_duplicate_payload_identifier(dir_path, payload_identifier, exclude_filename=None):
    """掃描目錄下所有.mobileconfig檔案,檢查有沒有「其他」檔案的頂層PayloadIdentifier
    跟目前要存的這個一樣(exclude_filename用來排除正在編輯的檔案自己,不然編輯既有檔案
    存檔時,自己跟自己比對一定會誤判成重複)。
    回傳撞到的檔名,沒有撞到回傳None。
    """
    if not payload_identifier or not os.path.isdir(dir_path):
        return None
    for fname in os.listdir(dir_path):
        if not fname.endswith(".mobileconfig") or fname == exclude_filename:
            continue
        full_path = os.path.join(dir_path, fname)
        try:
            with open(full_path, "rb") as f:
                raw_bytes = f.read()
            parsed = utils_signing.parse_mobileconfig_bytes(raw_bytes)
        except Exception:
            continue  # 讀取失敗的檔案跳過,不影響這次檢查
        if parsed.get("PayloadIdentifier") == payload_identifier:
            return fname
    return None


def save_mobileconfig(dir_path, filename, top_level, payloads, unmanaged_payloads=None, existing_uuids=None,
                       sign_with_cert_path=None, sign_with_key_path=None, sign_with_ca_path=None):
    """sign_with_cert_path/sign_with_key_path:選填,提供的話會在寫入檔案前,
    用openssl smime把內容簽署成PKCS#7格式,不提供(維持None)的話完全比照
    加上簽署功能之前的行為,不會有任何改變。

    簽署失敗時不會讓整個存檔動作失敗——退回寫入未簽署版本,並在warnings裡
    加上一則說明,讓使用者知道這次沒有簽署成功、需要自己去確認,而不是
    因為簽署這個額外功能出問題,就連基本的存檔都做不了。
    """
    validate_filename(filename)
    ensure_mobileconfig_dir(dir_path)
    plist_bytes, warnings = build_mobileconfig(top_level, payloads, unmanaged_payloads, existing_uuids)

    # 用 plistlib 重新載入一次,double-check 產出的內容一定是合法可解析的 plist
    # (這個驗證一定要在簽署之前做,簽署後的PKCS#7格式plistlib沒辦法解析)
    plistlib.loads(plist_bytes)

    # enroll-template.mobileconfig這份檔案本質上是含有字面佔位符(__SERIAL_PLACEHOLDER__)
    # 的原始文字樣板,由enroll-server.py在每次裝置請求時動態做文字替換+簽署,存在磁碟上
    # 的這份檔案本身必須永遠維持未簽署的純文字格式——不然enroll-server.py完全沒辦法對
    # 簽署過的二進位內容做文字替換,會直接導致服務啟動失敗。不管呼叫端傳入什麼簽署參數,
    # 這份檔案一律強制不簽署,避免這個曾經實際發生過的問題再次出現。
    if filename == "enroll-template.mobileconfig":
        sign_with_cert_path = None
        sign_with_key_path = None

    final_bytes = plist_bytes
    if sign_with_cert_path and sign_with_key_path:
        import utils_signing
        signed_bytes, sign_err = utils_signing.sign_plist_bytes(
            plist_bytes, sign_with_cert_path, sign_with_key_path, ca_cert_path=sign_with_ca_path,
        )
        if signed_bytes is not None:
            final_bytes = signed_bytes
        else:
            warnings = list(warnings) + [f"描述檔簽署失敗,已改為儲存未簽署版本(不影響裝置安裝與生效): {sign_err}"]

    full_path = os.path.join(dir_path, filename)
    tmp_path = full_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(final_bytes)
    os.replace(tmp_path, full_path)
    return warnings


def get_existing_uuids(dir_path, filename):
    """讀取既有檔案裡每個payload目前的PayloadUUID,存檔時延用,避免UUID每次都變動。
    非singular類型(wifi/webclip)會收集成list,依原本在檔案裡出現的順序。
    """
    full_path = os.path.join(dir_path, filename)
    if not os.path.exists(full_path):
        return {}
    with open(full_path, "rb") as f:
        raw_bytes = f.read()
    parsed = utils_signing.parse_mobileconfig_bytes(raw_bytes)

    type_to_key = {v["payload_type"]: k for k, v in PAYLOAD_SCHEMA.items()}
    result = {"_top": parsed.get("PayloadUUID")}
    for payload in parsed.get("PayloadContent", []):
        key = type_to_key.get(payload.get("PayloadType"))
        if not key:
            continue
        schema = PAYLOAD_SCHEMA[key]
        if schema.get("singular", True):
            result[key] = payload.get("PayloadUUID")
        else:
            result.setdefault(key, []).append(payload.get("PayloadUUID"))
    return result


def update_enroll_template_topic(mobileconfig_dir, new_topic):
    """更新精簡註冊描述檔(enroll-template.mobileconfig,位於mobileconfig_dir底下,
    這是enroll-server.py實際讀取使用的檔案)裡MDM Payload的Topic欄位。
    這裡刻意不整份用plistlib重新解析、序列化寫回去——因為enroll-server.py是對
    這份檔案的「原始文字內容」做字串替換(尋找__SERIAL_PLACEHOLDER__這類佔位符,
    用來組成每台裝置SCEP身分憑證的唯一CN),整份重新序列化雖然理論上內容不會壞,
    但有不必要的風險(例如格式被重新排版)。改成只在原始文字裡,精準把「舊的Topic值」
    字串替換成「新的Topic值」字串,其餘所有內容(含佔位符、原始格式)保證完全不受影響。
    回傳 (ok, message)。
    """
    path = os.path.join(mobileconfig_dir, "enroll-template.mobileconfig")
    if not os.path.exists(path):
        return False, f"找不到精簡註冊描述檔: {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except Exception as e:
        return False, f"讀取精簡註冊描述檔失敗: {e}"

    # 用plistlib解析只是為了「找出目前的Topic值是什麼」,不會拿解析後的結果寫回去
    try:
        parsed = plistlib.loads(raw_text.encode("utf-8"))
    except Exception as e:
        return False, f"解析精簡註冊描述檔失敗: {e}"

    mdm_payload = None
    for payload in parsed.get("PayloadContent", []):
        if payload.get("PayloadType") == "com.apple.mdm":
            mdm_payload = payload
            break

    if mdm_payload is None:
        return False, "精簡註冊描述檔裡找不到MDM Payload,沒有更新任何內容,請自己手動確認"

    old_topic = mdm_payload.get("Topic", "")
    if old_topic == new_topic:
        return True, "Topic本來就已經是新的值,不需要更新"
    if not old_topic:
        return False, "MDM Payload裡目前沒有Topic值,無法用文字替換的方式定位,請自己手動確認並更新"

    occurrences = raw_text.count(old_topic)
    if occurrences != 1:
        return False, f"預期舊Topic值在檔案裡只出現1次,實際找到{occurrences}次,為了安全起見不自動處理,請自己手動確認並更新"

    new_text = raw_text.replace(old_topic, new_topic)

    try:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp_path, path)
    except Exception as e:
        return False, f"寫入精簡註冊描述檔失敗: {e}"

    return True, f"已將Topic從「{old_topic}」更新為「{new_topic}」"


ENROLL_TEMPLATE_CA_PAYLOAD_IDENTIFIER = "tw.edu.nanomdm.enroll.ca-root"
ENROLL_TEMPLATE_SERIAL_PLACEHOLDER = "__SERIAL_PLACEHOLDER__"  # 跟enroll-server.py裡的PLACEHOLDER常數保持一致


def add_ca_cert_to_enroll_template(mobileconfig_dir, scep_ca_path, timeout=15):
    """把SCEP CA的根憑證,加進精簡註冊描述檔(enroll-template.mobileconfig)裡,
    成為一個獨立的「憑證」payload(PayloadType: com.apple.security.root)。

    背景:裝置透過SCEP拿到自己的身分憑證,只會建立「裝置跟MDM伺服器之間」的信任關係,
    不會讓這張CA自動變成裝置「一般用途信任根憑證清單」裡的一員——而簽署過的描述檔
    要不要顯示「已驗證」,查的正是這份一般用途的信任清單。加上這個憑證payload後,
    裝置完成初次註冊的同時就會信任這張CA,之後任何用同一張CA簽發的簽署憑證簽過的
    描述檔(baseline、群組描述檔等),才會被裝置正確判定為「已驗證」。

    做法:
    1. 用openssl把PEM格式的CA憑證轉成DER格式(Apple的憑證payload規定要DER編碼,
       不是PEM文字格式;PEM轉DER的輸出是二進位資料,全程用暫存檔案處理輸入輸出,
       不透過文字模式的subprocess呼叫,避免二進位內容被文字編碼轉換搞壞)
    2. 用plistlib讀入現有的精簡註冊描述檔,檢查有沒有已經加過這個憑證payload
       (用固定的PayloadIdentifier判斷),有的話就地更新憑證內容(CA換過的情況),
       沒有的話新增一筆
    3. 重新序列化寫回檔案

    這裡改用plistlib整份重新序列化寫回去,不像update_enroll_template_topic()那樣
    只做純文字替換——因為「加入一個全新的陣列元素」沒辦法用簡單的文字替換可靠地
    做到。已經確認過plistlib重新序列化後,__SERIAL_PLACEHOLDER__這個佔位符字串
    本身會完整保留,不受影響。

    回傳 (ok, message)。
    """
    template_path = os.path.join(mobileconfig_dir, "enroll-template.mobileconfig")
    if not os.path.exists(template_path):
        return False, f"找不到精簡註冊描述檔: {template_path}"
    if not os.path.exists(scep_ca_path):
        return False, f"找不到 SCEP CA 憑證: {scep_ca_path}"

    with tempfile.TemporaryDirectory() as tmpdir:
        der_path = os.path.join(tmpdir, "ca.der")
        rc, out, err = utils.run_cmd(
            ["openssl", "x509", "-in", scep_ca_path, "-outform", "der", "-out", der_path],
            timeout=timeout,
        )
        if rc != 0:
            return False, f"轉換 CA 憑證格式(PEM轉DER)失敗: {err or out}"
        try:
            with open(der_path, "rb") as f:
                der_bytes = f.read()
        except OSError as e:
            return False, f"讀取轉換後的憑證失敗: {e}"

    if not der_bytes:
        return False, "轉換後的憑證內容是空的,openssl可能沒有正確產生輸出"

    try:
        with open(template_path, "rb") as f:
            raw_bytes = f.read()
        parsed = utils_signing.parse_mobileconfig_bytes(raw_bytes)
    except Exception as e:
        return False, f"讀取/解析精簡註冊描述檔失敗: {e}"

    payload_content = parsed.setdefault("PayloadContent", [])
    existing_cert_payload = next(
        (p for p in payload_content if p.get("PayloadIdentifier") == ENROLL_TEMPLATE_CA_PAYLOAD_IDENTIFIER),
        None,
    )

    if existing_cert_payload is not None:
        existing_cert_payload["PayloadContent"] = der_bytes
        action_msg = "已更新既有的CA根憑證payload內容"
    else:
        cert_payload = {
            "PayloadCertificateFileName": "nanomdm-ca.cer",
            "PayloadContent": der_bytes,
            "PayloadDescription": "讓裝置信任這張CA,之後用這張CA簽署過的描述檔才會顯示為已驗證",
            "PayloadDisplayName": "NanoMDM CA 根憑證",
            "PayloadIdentifier": ENROLL_TEMPLATE_CA_PAYLOAD_IDENTIFIER,
            "PayloadType": "com.apple.security.root",
            "PayloadUUID": str(uuid.uuid4()).upper(),
            "PayloadVersion": 1,
        }
        payload_content.append(cert_payload)
        action_msg = "已新增CA根憑證payload"

    new_bytes = plistlib.dumps(parsed)

    # 寫回之前,先確認佔位符沒有在重新序列化的過程中意外消失或被改動,
    # 這個檢查失敗的話寧可整個動作失敗,也不要寫入一份壞掉、裝置沒辦法正常註冊的模板
    if ENROLL_TEMPLATE_SERIAL_PLACEHOLDER.encode("utf-8") not in new_bytes:
        return False, (
            f"重新序列化後找不到預期的序號佔位符({ENROLL_TEMPLATE_SERIAL_PLACEHOLDER}),"
            "為了安全起見不寫入檔案,請自己手動確認"
        )

    tmp_path = template_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(new_bytes)
    os.replace(tmp_path, template_path)

    return True, f"{action_msg},裝置下次註冊時就會信任這張CA"


def find_other_files_with_mdm_payload(mobileconfig_dir, exclude_filename="enroll-template.mobileconfig"):
    """掃描mobileconfig_dir底下所有描述檔,找出「除了精簡註冊描述檔以外」還有哪些檔案
    也包含MDM Payload。理論上一般群組描述檔不應該用到這個Payload,但如果誤設定了,
    topic換掉之後那些檔案裡殘留的舊topic就是過期資料,提醒使用者自己檢查、決定要不要更新。
    """
    results = []
    if not os.path.isdir(mobileconfig_dir):
        return results
    for fname in sorted(os.listdir(mobileconfig_dir)):
        if fname == exclude_filename or not fname.endswith(".mobileconfig"):
            continue
        full_path = os.path.join(mobileconfig_dir, fname)
        try:
            with open(full_path, "rb") as f:
                raw_bytes = f.read()
            parsed = utils_signing.parse_mobileconfig_bytes(raw_bytes)
        except Exception:
            continue
        for payload in parsed.get("PayloadContent", []):
            if payload.get("PayloadType") == "com.apple.mdm":
                results.append(fname)
                break
    return results
