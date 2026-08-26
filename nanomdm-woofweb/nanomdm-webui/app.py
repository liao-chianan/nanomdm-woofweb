# -*- coding: utf-8 -*-
import base64
import datetime
import functools
import json
import os
import re
import requests
import shutil
import subprocess
import threading
import time

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, Response, stream_with_context, send_file
)
from werkzeug.security import check_password_hash

import config
from config import load_config, ConfigError
import utils
import utils_profiles
import utils_depprofile
import utils_asm
import utils_sysstatus
import utils_version
import utils_certs
import utils_signing
import utils_auth
import utils_logging

try:
    CFG = load_config()
except ConfigError as e:
    print(f"[設定錯誤] {e}")
    raise SystemExit(1)

app = Flask(__name__)
app.secret_key = CFG["secret_key"]

# 部署在 nginx 的 /miniweb 子路徑後面時,靠這個中介層讓 Flask 正確辨識
# nginx 傳來的 X-Forwarded-Prefix,url_for() 產生的連結/重導向才會自動帶上 /miniweb 前綴。
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_prefix=1)

# session cookie 限定在 /miniweb 路徑下,避免外洩到同網域(例如 nanomdm.your-school.edu.tw)其他服務
app.config["SESSION_COOKIE_PATH"] = "/miniweb"

# Jinja2 的 {{ ... | tojson }} filter 預設會把 dict key 依字母順序重新排序
# (這是 Jinja2 自己內建的 policies['json.dumps_kwargs']['sort_keys']=True 設定,
# 跟 Flask 的 app.json.sort_keys 是兩條完全獨立的路徑,設定 app.json.sort_keys 不會生效)。
# COMMAND_DEFS 這種刻意排過順序、要給前端下拉選單照順序顯示的資料會因此被打亂,這裡關掉。
app.jinja_env.policies["json.dumps_kwargs"]["sort_keys"] = False

# 每次啟動服務都會產生新版本號,讓瀏覽器不會沿用快取的舊版 JS/CSS
STATIC_VERSION = str(int(time.time()))


@app.context_processor
def inject_static_version():
    return {"static_version": STATIC_VERSION}


@app.context_processor
def inject_branding():
    return {"branding": config.reload_branding()}


@app.before_request
def check_ip_allowlist():
    """IP白名單限制:套用到所有請求(含登入頁),避免未授權來源連到登入畫面。
    白名單是空清單時不做任何限制(預設行為,避免部署時因為忘記設定而把自己鎖在外面)。
    """
    if request.path.startswith("/static/"):
        return None
    state = config.reload_auth_state()
    rules = state.get("ip_allowlist", [])
    if not rules:
        return None
    client_ip = request.remote_addr
    if not utils_auth.is_ip_allowed(client_ip, rules):
        return Response("存取被拒絕:你的 IP 不在允許清單內。", status=403, mimetype="text/plain")
    return None


# 指令定義:提供給前端派送命令的選單使用
# type: none(無參數) / fields(需要額外欄位)
# group_excluded: True 代表這個指令不會出現在「群組命令」的選單(只適用單一裝置)
# hidden: True 代表不會出現在「派送命令」下拉選單,但後端仍然接受呼叫
#   (這幾個是「裝置詳細資訊」彈窗裡的按鈕直接呼叫用的底層指令,拿掉顯示但保留功能)
COMMAND_DEFS = {
    "SetDeviceName": {
        "label": "修改裝置名稱(系統層級)",
        "danger": False,
        "fields": [{"name": "DeviceName", "label": "新裝置名稱", "type": "text", "default": ""}],
        "group_excluded": True,
    },
    "RestartDevice": {"label": "裝置重開機", "danger": False, "fields": []},
    "ShutDownDevice": {"label": "裝置關機", "danger": False, "fields": []},
    "ClearPasscode": {"label": "清除密碼", "danger": False, "fields": []},
    "DeviceLock": {
        "label": "進入鎖定畫面",
        "danger": False,
        "fields": [
            {"name": "Message", "label": "鎖定畫面顯示訊息(選填,填了才看得出裝置真的被鎖定)", "type": "text", "default": ""},
            {"name": "PhoneNumber", "label": "聯絡電話(選填,會顯示在鎖定畫面上)", "type": "text", "default": ""},
        ],
    },
    "InstallApplication": {
        "label": "安裝APP",
        "danger": False,
        "fields": [{"name": "iTunesStoreID", "label": "App adamId", "type": "text", "default": ""}],
    },
    "RemoveApplication": {
        "label": "移除APP",
        "danger": False,
        "fields": [{"name": "Identifier", "label": "App Bundle ID", "type": "text", "default": ""}],
    },
    "EnableLostMode": {
        "label": "啟用遺失尋找模式(LOST MODE)",
        "danger": False,
        "fields": [
            {"name": "Message", "label": "螢幕顯示訊息", "type": "text", "default": "此設備為雙園國小財產，如有拾獲請聯繫"},
            {"name": "PhoneNumber", "label": "聯絡電話", "type": "text", "default": "0223061893"},
            {"name": "Footnote", "label": "附註", "type": "text", "default": ""},
        ],
    },
    "DeviceLocation": {"label": "取得裝置定位(需先啟用遺失尋找模式)", "danger": False, "fields": []},
    "DisableLostMode": {"label": "解除遺失尋找模式", "danger": False, "fields": []},
    "EraseDevice": {"label": "清除裝置(危險：會回復原廠設定)", "danger": True, "fields": []},

    "CheckOSUpdate": {
        "label": "查詢更新(自動掃描並檢測目前適用的最新版本)",
        "danger": False, "fields": [],
    },
    "DownloadOSUpdate": {
        "label": "下載更新(自動下載目前適用的最新版本)",
        "danger": False, "fields": [],
    },
    "InstallOSUpdate": {
        "label": "安裝更新(自動安裝目前適用的最新版本) ⚠️",
        "danger": True, "fields": [],
    },

    # ---- 以下不會出現在「派送命令」下拉選單,是「裝置詳細資訊」彈窗按鈕直接呼叫用的底層指令 ----
    "DeviceInformation": {"label": "查詢裝置資訊", "danger": False, "fields": [], "hidden": True},
    "AvailableOSUpdates": {"label": "查詢可用的 iOS / 軟體更新", "danger": False, "fields": [], "hidden": True},
    "OSUpdateStatus": {"label": "查詢軟體更新進度狀態", "danger": False, "fields": [], "hidden": True},
    "ScheduleOSUpdateScan": {
        "label": "觸發軟體更新掃描",
        "danger": False,
        "fields": [{"name": "Force", "label": "強制掃描 (true/false)", "type": "text", "default": "true"}],
        "hidden": True,
    },
    "ScheduleOSUpdate": {
        "label": "執行系統更新安裝",
        "danger": True,
        "fields": [
            {"name": "ProductKey", "label": "ProductKey", "type": "text", "default": ""},
            {"name": "ProductVersion", "label": "版本號(選填)", "type": "text", "default": ""},
            {"name": "InstallAction", "label": "安裝方式", "type": "text", "default": "Default"},
        ],
        "hidden": True,
    },
    "ManagedApplicationList": {
        "label": "查詢App受管理狀態(診斷用)",
        "danger": False,
        "fields": [{"name": "Identifiers", "label": "Bundle ID(留空=查詢全部App)", "type": "text", "default": ""}],
        "hidden": True,
    },
}


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def get_env_dict():
    return utils.parse_env_dict(CFG["paths"]["env_file"])


def get_nanomdm_conn():
    env = get_env_dict()
    base_url = env.get(CFG["nanomdm"]["base_url_env_key"], "")
    api_key = env.get(CFG["nanomdm"]["api_key_env_key"], "")
    api_user = CFG["nanomdm"]["api_user"]
    return base_url, api_user, api_key


def get_nanodep_conn():
    env = get_env_dict()
    base_url = env.get(CFG["nanodep"]["base_url_env_key"], "")
    api_key = env.get(CFG["nanodep"]["api_key_env_key"], "")
    dep_name = env.get(CFG["nanodep"]["name_env_key"], "")
    restart_cmd = env.get(CFG["nanodep"]["depsyncer_restart_cmd_env_key"], "")
    return base_url, api_key, dep_name, restart_cmd


def get_nanoaxm_conn():
    env = get_env_dict()
    base_url = env.get(CFG["nanoaxm"]["base_url_env_key"], "")
    api_key = env.get(CFG["nanoaxm"]["api_key_env_key"], "")
    axm_name = env.get(CFG["nanoaxm"]["name_env_key"], "")
    org_type = CFG["nanoaxm"]["org_type"]
    return base_url, api_key, org_type, axm_name


# ---------------------------------------------------------------------------
# 登入 / 登出
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        success = utils_auth.verify_login(username, password)
        utils_logging.log_login(
            CFG["system_logs"]["user_login_log"], CFG["system_logs"]["user_login_retention_days"],
            username, success, request.remote_addr,
        )
        if success:
            session["logged_in"] = True
            session["username"] = username
            return redirect(url_for("devices_page"))
        error = "帳號或密碼錯誤"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# 帳號管理
# ---------------------------------------------------------------------------
@app.route("/account-management")
@login_required
def account_management_page():
    return render_template("account_management.html", active="account_management")


@app.route("/api/accounts")
@login_required
def api_accounts_list():
    return jsonify({"ok": True, "accounts": utils_auth.list_accounts(), "current_username": session.get("username")})


@app.route("/api/accounts/add", methods=["POST"])
@login_required
def api_accounts_add():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    try:
        utils_auth.add_account(username, password)
        return jsonify({"ok": True, "message": f"已新增帳號 {username}"})
    except utils_auth.AuthError as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/accounts/delete", methods=["POST"])
@login_required
def api_accounts_delete():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    try:
        utils_auth.delete_account(username, current_username=session.get("username"))
        return jsonify({"ok": True, "message": f"已刪除帳號 {username}"})
    except utils_auth.AuthError as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/accounts/change-password", methods=["POST"])
@login_required
def api_accounts_change_password():
    data = request.json or {}
    username = (data.get("username") or "").strip()
    new_password = data.get("new_password") or ""
    try:
        utils_auth.change_password(username, new_password, current_username=session.get("username"))
        return jsonify({"ok": True, "message": f"已更新 {username} 的密碼"})
    except utils_auth.AuthError as e:
        return jsonify({"ok": False, "message": str(e)}), 400


@app.route("/api/ip-allowlist")
@login_required
def api_ip_allowlist_list():
    return jsonify({"ok": True, "rules": utils_auth.list_ip_rules(), "your_ip": request.remote_addr})


@app.route("/api/ip-allowlist/save", methods=["POST"])
@login_required
def api_ip_allowlist_save():
    data = request.json or {}
    rules = data.get("rules") or []
    try:
        saved = utils_auth.save_ip_rules(rules, requester_ip=request.remote_addr)
        return jsonify({"ok": True, "message": "已儲存 IP 白名單", "rules": saved})
    except utils_auth.AuthError as e:
        return jsonify({"ok": False, "message": str(e)}), 400


# ---------------------------------------------------------------------------
# 系統紀錄
# ---------------------------------------------------------------------------
@app.route("/system-logs")
@login_required
def system_logs_page():
    return render_template("system_logs.html", active="system_logs")


@app.route("/api/system-logs")
@login_required
def api_system_logs():
    log_type = request.args.get("type", "login")
    if log_type == "login":
        entries = utils_logging.read_log_entries(CFG["system_logs"]["user_login_log"])
        retention_days = CFG["system_logs"]["user_login_retention_days"]
    elif log_type == "activity":
        entries = utils_logging.read_log_entries(CFG["system_logs"]["user_activity_log"])
        retention_days = CFG["system_logs"]["user_activity_retention_days"]
    else:
        return jsonify({"ok": False, "message": f"不支援的紀錄類型: {log_type}"}), 400

    return jsonify({"ok": True, "entries": entries, "retention_days": retention_days, "count": len(entries)})


def _enrich_command_row_with_app_info(row, vpp_cache_path):
    """給InstallApplication/RemoveApplication補上App資訊(Bundle ID+軟體名稱),
    跟單一裝置的回應記錄(/api/devices/command-history)共用同一套邏輯,抽成共用函式
    避免兩處各自維護一份、容易改一邊漏改另一邊。
    """
    request_type = row.get("request_type")
    if request_type not in ("InstallApplication", "RemoveApplication"):
        return None

    parsed_command = utils.parse_plist_text(row.get("command"))
    command_body = (parsed_command or {}).get("Command", {})
    if request_type == "InstallApplication":
        adam_id = command_body.get("iTunesStoreID")
        if not adam_id:
            return None
        vpp_info = utils.lookup_vpp_app_info(vpp_cache_path, adam_id=adam_id)
        return {
            "adam_id": adam_id,
            "bundle_id": vpp_info["bundle_id"] if vpp_info else None,
            "name": vpp_info["name"] if vpp_info else None,
        }
    else:  # RemoveApplication
        identifier = command_body.get("Identifier")
        if not identifier:
            return None
        vpp_info = utils.lookup_vpp_app_info(vpp_cache_path, bundle_id=identifier)
        return {"bundle_id": identifier, "name": vpp_info["name"] if vpp_info else None}


@app.route("/api/system-logs/commands")
@login_required
def api_system_logs_commands():
    search_text = request.args.get("search", "").strip().lower()
    group_filter = request.args.get("group", "").strip()
    request_type_filter = request.args.get("request_type", "").strip()
    status_filter = request.args.get("status", "").strip()

    devices_csv = utils.read_devices_csv(CFG["paths"]["devices_csv"])
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    mdm_devices, rc0, _out0, err0 = utils.query_devices_from_mysql(CFG["mysql"], db_password)
    if rc0 != 0:
        return jsonify({"ok": False, "message": f"查詢裝置清單失敗: {err0}"}), 500

    eid_to_serial = {d["enrollment_id"]: d["serial_number"] for d in mdm_devices}

    # 依裝置名稱/序號文字、群組,先在本地算出符合條件的enrollment_id清單,
    # 因為這兩項是我們自己webui的概念(devices.csv),nanomdm的資料庫完全不知道
    enrollment_ids_filter = None
    if search_text or group_filter:
        enrollment_ids_filter = []
        for eid, serial in eid_to_serial.items():
            info = devices_csv.get(serial, {})
            name = info.get("device_name", "")
            group = info.get("group", "")
            if group_filter and group != group_filter:
                continue
            if search_text and search_text not in serial.lower() and search_text not in name.lower():
                continue
            enrollment_ids_filter.append(eid)

    rows, rc, err = utils.query_all_command_history(
        CFG["mysql"], db_password,
        enrollment_ids=enrollment_ids_filter,
        request_type=request_type_filter or None,
        status=status_filter or None,
        limit=1000,
    )
    if rc != 0:
        return jsonify({"ok": False, "message": err}), 500

    vpp_cache_path = CFG["paths"]["vpp_cache_csv"]
    result_rows = []
    for row in rows:
        eid = row.get("id")
        serial = eid_to_serial.get(eid, "")
        info = devices_csv.get(serial, {})
        result_rows.append({
            "enrollment_id": eid,
            "serial_number": serial,
            "device_name": info.get("device_name", ""),
            "group": info.get("group", ""),
            "command_uuid": row.get("command_uuid"),
            "request_type": row.get("request_type"),
            "status": row.get("status"),  # None代表尚未回應(仍在排隊等待中)
            "active": row.get("active"),
            "created_at": row.get("created_at"),
            "result_updated_at": row.get("result_updated_at"),
            "raw_result": row.get("result"),
            "app_info": _enrich_command_row_with_app_info(row, vpp_cache_path),
        })

    # 篩選選項清單用固定來源(COMMAND_DEFS的所有指令類型、群組清單來自devices.csv實際存在的群組、
    # 狀態用已知的MDM協定標準值),不是從「目前查詢結果」反推,這樣使用者篩選後,
    # 下拉選單裡的其他選項不會跟著消失、還是能繼續切換組合篩選條件
    groups_available = sorted(set(v.get("group", "") for v in devices_csv.values() if v.get("group")))

    return jsonify({
        "ok": True, "rows": result_rows,
        "filter_options": {
            "groups": groups_available,
            "request_types": sorted(COMMAND_DEFS.keys()),
            "statuses": ["__pending__", "__cancelled__", "Acknowledged", "NotNow", "Error", "CommandFormatError", "Idle"],
        },
    })


@app.route("/api/system-logs/commands/cancel", methods=["POST"])
@login_required
def api_system_logs_commands_cancel():
    data = request.json or {}
    enrollment_id = data.get("enrollment_id", "")
    command_uuid = data.get("command_uuid", "")
    if not enrollment_id or not command_uuid:
        return jsonify({"ok": False, "message": "缺少enrollment_id或command_uuid"}), 400

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    ok, err = utils.cancel_pending_command(CFG["mysql"], db_password, enrollment_id, command_uuid)

    log_activity_entry(
        "系統紀錄-取消指令", ok,
        detail=f"enrollment_id={enrollment_id}, command_uuid={command_uuid}" + (f", error={err}" if not ok else ""),
    )
    if not ok:
        return jsonify({"ok": False, "message": f"取消失敗: {err}"}), 500
    return jsonify({"ok": True, "message": "已取消這筆指令(裝置不會再收到這筆待處理的指令)"})


@app.route("/api/system-logs/commands/resend", methods=["POST"])
@login_required
def api_system_logs_commands_resend():
    data = request.json or {}
    enrollment_id = data.get("enrollment_id", "")
    command_uuid = data.get("command_uuid", "")
    if not enrollment_id or not command_uuid:
        return jsonify({"ok": False, "message": "缺少enrollment_id或command_uuid"}), 400

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")

    # 重新派送:不是用使用者填的參數重新組一次指令(那樣有轉譯失真的風險),
    # 而是直接拿原本失敗那筆的完整原始內容,原封不動地再送一次,確保跟原本失敗的
    # 那次一模一樣。只是send_mdm_command內部會自動配上一組全新的CommandUUID
    # (沿用舊的UUID會跟資料庫裡的唯一鍵衝突)。
    rows, rc, err = utils.query_all_command_history(
        CFG["mysql"], db_password, enrollment_ids=[enrollment_id], limit=1000,
    )
    if rc != 0:
        return jsonify({"ok": False, "message": f"查詢原始指令失敗: {err}"}), 500

    target_row = next((r for r in rows if r.get("command_uuid") == command_uuid), None)
    if not target_row:
        return jsonify({"ok": False, "message": "找不到這筆指令的原始內容,可能已經被清理"}), 404

    parsed_command = utils.parse_plist_text(target_row.get("command"))
    command_body = dict((parsed_command or {}).get("Command", {}))
    request_type = command_body.pop("RequestType", None)
    if not request_type:
        return jsonify({"ok": False, "message": "無法解析原始指令內容,無法重新派送"}), 500

    base_url, api_user, api_key = get_nanomdm_conn()
    try:
        status_code, result = utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, request_type, command_body)
        ok = status_code < 400
    except Exception as e:
        ok, status_code, result = False, None, str(e)

    log_activity_entry(
        "系統紀錄-重新派送指令", ok,
        detail=f"enrollment_id={enrollment_id}, 原command_uuid={command_uuid}, request_type={request_type}, http_status={status_code}",
    )
    if not ok:
        return jsonify({"ok": False, "message": f"重新派送失敗: {result}"}), 500
    return jsonify({"ok": True, "message": f"已重新派送 {request_type} 指令"})


@app.route("/")
@login_required
def index():
    return redirect(url_for("devices_page"))


# ---------------------------------------------------------------------------
def log_activity_entry(command, success, detail=None, serial=None, device_name=None, group=None):
    """記錄一筆操作紀錄,自動帶入目前登入的帳號與來源IP。
    serial/device_name/group 是額外的結構化參數,只要有給,一律統一格式化放在detail最前面
    (格式: 序號=X, 裝置名稱=Y, 群組=Z),確保牽涉到裝置的操作都能在系統紀錄頁一眼識別是哪台裝置、
    哪個群組,不用每個呼叫點自己手動排字串格式、容易漏欄位或格式不一致。
    """
    id_parts = []
    if serial:
        id_parts.append(f"序號={serial}")
    if device_name:
        id_parts.append(f"裝置名稱={device_name}")
    if group:
        id_parts.append(f"群組={group}")

    full_detail = detail
    if id_parts:
        id_prefix = ", ".join(id_parts)
        full_detail = f"{id_prefix} | {detail}" if detail else id_prefix

    utils_logging.log_activity(
        CFG["system_logs"]["user_activity_log"], CFG["system_logs"]["user_activity_retention_days"],
        session.get("username"), command, success, request.remote_addr, detail=full_detail,
    )


def log_system_activity_entry(command, success, detail=None):
    """給背景排程(沒有Flask request context,例如ASM/VPP自動同步的排程執行緒)用的記錄函式。
    不能沿用 log_activity_entry(),那個內部會呼叫 session.get()/request.remote_addr,
    背景執行緒沒有Flask request context,直接呼叫會丟RuntimeError。
    這裡用固定的「系統(自動排程)」當使用者名稱,IP留空,跟手動觸發的紀錄在畫面上可以清楚區分開來。
    """
    utils_logging.log_activity(
        CFG["system_logs"]["user_activity_log"], CFG["system_logs"]["user_activity_retention_days"],
        "系統(自動排程)", command, success, None, detail=detail,
    )


def verify_current_user_password(password):
    """驗證輸入的密碼是否對得上「目前登入的這個帳號」,用於高風險操作(憑證更換等)的二次確認。
    刻意用session裡的username去驗證,不能只驗證「這是某組有效帳密」,必須是操作者自己的密碼,
    避免有人拿別的有效帳密矇混過關。
    """
    username = session.get("username")
    if not username:
        return False
    return utils_auth.verify_login(username, password)


def clear_nanoaxm_token_cache(axm_name):
    """清除nanoaxm快取住的OAuth token(ca_token/ca_validity_sec/ca_expiry_unix三個欄位)。
    實際查證發現:nanoaxm的/authcreds端點更新client_id/key_id/私鑰時,不會連帶清掉
    舊的ca_token快取——這個快取是用「當時的client_id」簽出來的JWT,一旦client_id換了,
    快取住的JWT內容跟現在存的client_id/私鑰就對不起來,nanoaxm拿這組不匹配的快取
    去跟Apple換token會被判定成invalid_client。
    ca_validity_sec預設180天,不會自然過期修復自己,只能手動清除強制它下次重新簽發。
    直接寫資料庫是因為nanoaxm官方沒有提供對應的API或指令可以做這件事。
    """
    env = get_env_dict()
    db_password = env.get(CFG["nanoaxm_mysql"]["db_password_env_key"], "")
    safe_name = axm_name.replace("'", "''")
    sql = (
        f"UPDATE axm_names SET ca_token = NULL, ca_validity_sec = NULL, ca_expiry_unix = NULL "
        f"WHERE name = '{safe_name}';"
    )
    args = [
        "docker", "exec", CFG["nanoaxm_mysql"]["docker_container"], "mysql",
        f"-u{CFG['nanoaxm_mysql']['db_user']}", f"-p{db_password}",
        "-N", "-B", "--raw", CFG["nanoaxm_mysql"]["db_name"], "-e", sql,
    ]
    rc, out, err = utils.run_cmd(args, timeout=15)
    if rc != 0:
        return False, (err or out or "清除快取失敗,原因不明")
    return True, None


def restart_service_and_log(service_type, service_name, reason):
    """憑證更新後如果需要重啟服務,統一透過這個函式執行並記錄,
    service_type是"docker"或"systemd",對應呼叫不同的重啟函式。
    回傳 (ok, message)。
    """
    if service_type == "docker":
        ok, err = utils_sysstatus.restart_docker_container(service_name)
    else:
        ok, err = utils_sysstatus.restart_systemd_service(service_name)
    log_activity_entry(f"憑證管理-重啟服務({service_name})", ok, detail=f"原因: {reason}" + (f", 錯誤: {err}" if err else ""))
    return ok, err


def get_nanodep_script_env():
    """nanodep 官方的 shell 工具(dep-account-detail.sh、dep-device-details.sh 等)
    內部寫死要讀 BASE_URL / DEP_NAME / APIKEY 這幾個變數名稱,
    跟我們自己在 .env 裡用的 NANODEP_BASE_URL / NANODEP_NAME / NANODEP_API_KEY 不一樣,
    這裡做名稱對應,讓腳本在 systemd(乾淨環境、沒有繼承任何互動式 shell 變數)下也能正常運作。
    """
    base_url, api_key, dep_name, _ = get_nanodep_conn()
    return {"BASE_URL": base_url or "", "DEP_NAME": dep_name or "", "APIKEY": api_key or ""}


def get_nanoaxm_script_env():
    """cfg-authcreds.sh 內部寫死要讀 AXM_NAME / BASE_URL / API_KEY 這幾個變數名稱。
    注意:是「API_KEY」(有底線),跟nanodep的腳本用的「APIKEY」(無底線)不一樣,
    這是直接看過使用者提供的cfg-authcreds.sh原始碼確認的,不是憑印象類比猜測。
    """
    base_url, api_key, _, axm_name = get_nanoaxm_conn()
    return {"AXM_NAME": axm_name or "", "BASE_URL": base_url or "", "API_KEY": api_key or ""}


# ---------------------------------------------------------------------------
# 頂部資訊欄 - dep-account-detail
# ---------------------------------------------------------------------------
@app.route("/api/dep-account-detail")
@login_required
def api_dep_account_detail():
    script = CFG["paths"]["dep_account_detail_script"]
    rc, out, err = utils.run_dep_account_detail(
        script, env_file_path=CFG["paths"]["env_file"], extra_env=get_nanodep_script_env()
    )

    fields = None
    if rc == 0:
        try:
            raw = json.loads(out)
            fields = {
                "server_name": raw.get("server_name"),
                "server_uuid": raw.get("server_uuid"),
                "facilitator_id": raw.get("facilitator_id"),
                "admin_id": raw.get("admin_id"),
                "org_name": raw.get("org_name"),
                "org_email": raw.get("org_email"),
                "org_phone": raw.get("org_phone"),
                "org_address": raw.get("org_address"),
                "org_id": raw.get("org_id"),
            }
        except json.JSONDecodeError:
            fields = None

    return jsonify({"returncode": rc, "stdout": out, "stderr": err, "fields": fields})


# ---------------------------------------------------------------------------
# 品牌設定 (站台名稱 / LOGO)
# ---------------------------------------------------------------------------
ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


@app.route("/api/logo-image")
@login_required
def api_logo_image():
    branding = config.reload_branding()
    logo_dir = CFG["paths"]["logo_dir"]
    filename = branding.get("logo_filename", "default.png")
    full_path = os.path.join(logo_dir, filename)
    if not os.path.exists(full_path):
        full_path = os.path.join(logo_dir, "default.png")
    if not os.path.exists(full_path):
        return jsonify({"ok": False, "message": "找不到LOGO圖片,連預設圖片都不存在"}), 404
    return send_file(full_path)


@app.route("/api/branding/save", methods=["POST"])
@login_required
def api_branding_save():
    data = request.json or {}
    site_label = (data.get("site_label") or "").strip()
    if not site_label:
        return jsonify({"ok": False, "message": "站台名稱不能是空的"}), 400
    if len(site_label) > 50:
        return jsonify({"ok": False, "message": "站台名稱長度請控制在50字以內"}), 400
    config.save_branding(site_label=site_label)
    return jsonify({"ok": True, "message": "已儲存站台名稱"})


@app.route("/api/branding/upload-logo", methods=["POST"])
@login_required
def api_branding_upload_logo():
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"ok": False, "message": "沒有選擇檔案"}), 400

    f = request.files["file"]
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_LOGO_EXTENSIONS:
        return jsonify({"ok": False, "message": f"不支援的圖片格式,請上傳 {'/'.join(ALLOWED_LOGO_EXTENSIONS)} 其中一種"}), 400

    logo_dir = CFG["paths"]["logo_dir"]
    os.makedirs(logo_dir, exist_ok=True)
    filename = f"custom_logo{ext}"
    full_path = os.path.join(logo_dir, filename)
    try:
        f.save(full_path)
    except Exception as e:
        return jsonify({"ok": False, "message": f"儲存圖片失敗: {e}"}), 500

    config.save_branding(logo_filename=filename)
    return jsonify({"ok": True, "message": "已更新LOGO,頁首實際顯示尺寸是50x50,建議上傳正方形圖片效果較好"})


@app.route("/api/branding/reset-logo", methods=["POST"])
@login_required
def api_branding_reset_logo():
    config.save_branding(logo_filename="default.png")
    return jsonify({"ok": True, "message": "已還原成預設圖片"})


# ---------------------------------------------------------------------------
# 環境參數頁
# ---------------------------------------------------------------------------
@app.route("/env")
@login_required
def env_page():
    return render_template("env.html", active="env")


@app.route("/api/system-params")
@login_required
def api_system_params_get():
    return jsonify({
        "ok": True,
        "params": {
            "asm_devices_interval_minutes": CFG["asm_devices_cache"]["refresh_interval_seconds"] // 60,
            "vpp_interval_minutes": CFG["vpp_cache"]["refresh_interval_seconds"] // 60,
            "devices_status_interval_minutes": CFG["devices_status_cache"]["refresh_interval_seconds"] // 60,
            "pending_retry_threshold_hours": CFG["devices_status_cache"]["pending_retry_threshold_minutes"] / 60,
        },
    })


@app.route("/api/system-params", methods=["POST"])
@login_required
def api_system_params_save():
    data = request.json or {}

    def _to_positive_int(value, field_label):
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field_label}必須是數字")
        if n < 1:
            raise ValueError(f"{field_label}必須大於0")
        return n

    try:
        asm_devices_minutes = _to_positive_int(data.get("asm_devices_interval_minutes"), "ASM 所有裝置的自動背景更新時間")
        vpp_minutes = _to_positive_int(data.get("vpp_interval_minutes"), "ASM 軟體資訊的自動背景更新時間")
        devices_status_minutes = _to_positive_int(data.get("devices_status_interval_minutes"), "裝置與命令的自動背景更新時間")
        retry_hours = _to_positive_int(data.get("pending_retry_threshold_hours"), "逾時命令可重派時間")
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    CFG["asm_devices_cache"]["refresh_interval_seconds"] = asm_devices_minutes * 60
    CFG["vpp_cache"]["refresh_interval_seconds"] = vpp_minutes * 60
    CFG["devices_status_cache"]["refresh_interval_seconds"] = devices_status_minutes * 60
    CFG["devices_status_cache"]["pending_retry_threshold_minutes"] = retry_hours * 60
    config.save_config(CFG)

    log_activity_entry(
        "系統參數-背景排程時間設定", True,
        detail=(
            f"ASM所有裝置={asm_devices_minutes}分鐘, ASM軟體資訊={vpp_minutes}分鐘, "
            f"裝置與命令={devices_status_minutes}分鐘, 逾時命令可重派時間={retry_hours}小時"
        ),
    )
    return jsonify({"ok": True})


@app.route("/api/env")
@login_required
def api_env():
    path = CFG["paths"]["env_file"]
    return jsonify({"content": utils.read_env_file(path)})


@app.route("/api/env/fields")
@login_required
def api_env_fields():
    path = CFG["paths"]["env_file"]
    lines = utils.parse_env_file_lines(path)
    fields = utils.env_fields_from_lines(lines)
    return jsonify({"ok": True, "fields": fields})


@app.route("/api/env/fields/save", methods=["POST"])
@login_required
def api_env_fields_save():
    data = request.json or {}
    key = (data.get("key") or "").strip()
    value = data.get("value", "")
    try:
        utils.update_env_key(CFG["paths"]["env_file"], CFG["paths"]["env_backup_dir"], key, value)
        log_activity_entry("系統環境參數-修改", True, detail=f"變數={key}(基於安全考量,不記錄實際值)")
        return jsonify({"ok": True, "message": f"已儲存 {key} (已自動備份)"})
    except ValueError as e:
        log_activity_entry("系統環境參數-修改", False, detail=f"變數={key}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        log_activity_entry("系統環境參數-修改", False, detail=f"變數={key}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/env/fields/add", methods=["POST"])
@login_required
def api_env_fields_add():
    data = request.json or {}
    key = (data.get("key") or "").strip()
    value = data.get("value", "")
    existing = utils.env_fields_from_lines(utils.parse_env_file_lines(CFG["paths"]["env_file"]))
    if any(f["key"] == key for f in existing):
        return jsonify({"ok": False, "message": f"變數 {key} 已經存在,請直接編輯既有欄位"}), 400
    try:
        utils.update_env_key(CFG["paths"]["env_file"], CFG["paths"]["env_backup_dir"], key, value)
        log_activity_entry("系統環境參數-新增", True, detail=f"變數={key}(基於安全考量,不記錄實際值)")
        return jsonify({"ok": True, "message": f"已新增 {key} (已自動備份)"})
    except ValueError as e:
        log_activity_entry("系統環境參數-新增", False, detail=f"變數={key}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        log_activity_entry("系統環境參數-新增", False, detail=f"變數={key}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/env/fields/delete", methods=["POST"])
@login_required
def api_env_fields_delete():
    data = request.json or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "message": "缺少變數名稱"}), 400
    try:
        utils.delete_env_key(CFG["paths"]["env_file"], CFG["paths"]["env_backup_dir"], key)
        log_activity_entry("系統環境參數-刪除", True, detail=f"變數={key}")
        return jsonify({"ok": True, "message": f"已刪除 {key} (已自動備份)"})
    except Exception as e:
        log_activity_entry("系統環境參數-刪除", False, detail=f"變數={key}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/env/backup", methods=["POST"])
@login_required
def api_env_backup():
    backup_path = utils.backup_env_file(CFG["paths"]["env_file"], CFG["paths"]["env_backup_dir"])
    if not backup_path:
        return jsonify({"ok": False, "message": "找不到 .env 檔案,無法備份"}), 400
    return jsonify({"ok": True, "message": f"已建立備份 {os.path.basename(backup_path)}"})


@app.route("/api/env/backups")
@login_required
def api_env_backups():
    backups = utils.list_env_backups(CFG["paths"]["env_backup_dir"])
    return jsonify({"ok": True, "backups": backups})


@app.route("/api/env/backups/download/<filename>")
@login_required
def api_env_backups_download(filename):
    if not re.match(r'^[A-Za-z0-9_.\-]+$', filename or ""):
        return jsonify({"ok": False, "message": "檔名格式不正確"}), 400
    full_path = os.path.join(CFG["paths"]["env_backup_dir"], filename)
    if not os.path.exists(full_path):
        return jsonify({"ok": False, "message": "找不到這個備份檔案"}), 404
    return send_file(full_path, as_attachment=True, download_name=filename)


@app.route("/api/devices-csv-raw")
@login_required
def api_devices_csv_raw():
    return jsonify({"content": utils.read_devices_csv_raw(CFG["paths"]["devices_csv"])})


@app.route("/api/groups-json")
@login_required
def api_groups_json():
    path = CFG["paths"]["groups_json"]
    return jsonify({"content": utils.read_groups_json_raw(path)})


# ---------------------------------------------------------------------------
# 憑證狀態檢視
# ---------------------------------------------------------------------------
@app.route("/cert-status")
@login_required
def cert_status_page():
    return render_template("cert_status.html", active="cert_status")


@app.route("/api/cert-status", methods=["GET"])
@login_required
def api_cert_status():
    env = get_env_dict()
    try:
        results = utils_certs.run_all_checks(CFG, env)
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        return jsonify({"ok": False, "message": f"檢查過程發生未預期錯誤: {e}"}), 500


def get_profile_signing_kwargs():
    """依照目前系統的描述檔簽署設定,組出可以直接**展開傳給save_mobileconfig()/
    duplicate_mobileconfig()的簽署參數dict。沒啟用簽署、或簽署憑證不存在時,
    回傳空dict(展開後等於完全不傳簽署參數,維持不簽署的行為)。
    """
    signing_cfg = CFG.get("profile_signing", {})
    if not signing_cfg.get("enabled"):
        return {}
    if not utils_signing.signing_cert_exists(
        signing_cfg.get("signing_cert_path", ""), signing_cfg.get("signing_key_path", "")
    ):
        return {}
    return {
        "sign_with_cert_path": signing_cfg["signing_cert_path"],
        "sign_with_key_path": signing_cfg["signing_key_path"],
        "sign_with_ca_path": CFG["cert_status"]["scep_ca_path"],
    }


@app.route("/api/profile-signing/status")
@login_required
def api_profile_signing_status():
    signing_cfg = CFG.get("profile_signing", {})
    cert_path = signing_cfg.get("signing_cert_path", "")
    key_path = signing_cfg.get("signing_key_path", "")
    exists = utils_signing.signing_cert_exists(cert_path, key_path)
    info = utils_signing.get_signing_cert_info(cert_path) if exists else None
    return jsonify({
        "ok": True,
        "enabled": bool(signing_cfg.get("enabled")),
        "cert_exists": exists,
        "cert_info": info,
    })


@app.route("/api/profile-signing/generate", methods=["POST"])
@login_required
def api_profile_signing_generate():
    signing_cfg = CFG.get("profile_signing", {})
    scep_ca_path = CFG["cert_status"]["scep_ca_path"]
    scep_ca_key_path = CFG["cert_status"].get("scep_ca_key_path", "")
    env = get_env_dict()
    ca_key_password = env.get("SCEP_CA_PASSWORD", "")

    ok, message = utils_signing.generate_profile_signing_cert(
        scep_ca_path, scep_ca_key_path,
        signing_cfg["signing_cert_path"], signing_cfg["signing_key_path"],
        ca_key_password=ca_key_password,
    )
    log_activity_entry("描述檔簽署-產生憑證", ok, detail=message)
    if not ok:
        return jsonify({"ok": False, "message": message}), 500
    return jsonify({"ok": True, "message": message})


@app.route("/api/profile-signing/add-ca-to-enroll-template", methods=["POST"])
@login_required
def api_profile_signing_add_ca_to_enroll_template():
    scep_ca_path = CFG["cert_status"]["scep_ca_path"]
    ok, message = utils_profiles.add_ca_cert_to_enroll_template(
        CFG["paths"]["mobileconfig_dir"], scep_ca_path,
    )
    log_activity_entry("描述檔簽署-CA加入註冊模板", ok, detail=message)
    if not ok:
        return jsonify({"ok": False, "message": message}), 500
    return jsonify({"ok": True, "message": message})


@app.route("/api/profile-signing/toggle", methods=["POST"])
@login_required
def api_profile_signing_toggle():
    data = request.json or {}
    enabled = bool(data.get("enabled"))

    if enabled:
        signing_cfg = CFG.get("profile_signing", {})
        if not utils_signing.signing_cert_exists(
            signing_cfg.get("signing_cert_path", ""), signing_cfg.get("signing_key_path", "")
        ):
            return jsonify({"ok": False, "message": "尚未產生簽署憑證,請先按「產生簽署憑證」再啟用"}), 400

    CFG["profile_signing"]["enabled"] = enabled
    config.save_config(CFG)
    log_activity_entry("描述檔簽署-開關設定", True, detail=f"enabled={enabled}")
    return jsonify({"ok": True})


@app.route("/api/cert-status/nginx/days-left")
@login_required
def api_cert_nginx_days_left():
    """查詢nginx憑證目前距離到期還有幾天,給前端判斷要不要在執行續期前
    先跳出「還沒到期,是否要強制換發」的警告。
    """
    cert_path = CFG["cert_status"]["nginx_cert_path"]
    expiry_date, days_left, error = utils_certs.get_cert_enddate_from_file(cert_path)
    if error:
        return jsonify({"ok": False, "message": error}), 500
    return jsonify({"ok": True, "days_left": days_left, "expiry_date": expiry_date})


@app.route("/api/cert-status/nginx/renew-stream")
@login_required
def api_cert_nginx_renew_stream():
    """執行 certbot renew,即時串流輸出過程,成功後 reload(不是restart!) nginx 讓新憑證生效。
    注意:一定要用reload,不能用restart——這條SSE連線本身是透過nginx轉送的,
    如果對nginx下restart,會把nginx整個服務程序砍掉重開,連帶把這條顯示進度用的連線也砍斷,
    導致使用者畫面卡住、看不到後續任何訊息(即使伺服器端其實已經執行完成)。
    reload只會讓nginx重新讀取設定/憑證,不會中斷現有連線。

    支援 force 參數(前端在使用者確認過風險後才會帶這個參數上來):
    加上--force-renewal會強制換發,不管目前憑證是否還沒到期。
    Let's Encrypt對换發次數有速率限制,不應該當作日常操作隨意使用,
    所以「要不要force」這個決定必須是使用者在看過明確風險說明後自己按下確認,
    後端這裡不主動判斷、也不擅自決定要不要加上這個參數。
    """
    force = request.args.get("force") == "true"

    def generate():
        # --no-random-sleep-on-renew: Debian/Ubuntu/Fedora套件版certbot內建有隨機延遲機制
        # (可長達5分鐘以上,官方bug report明確證實),用意是避免排程自動觸發renew時大量伺服器
        # 同時連線Let's Encrypt造成擁塞。但這裡是管理者手動點擊、明確要求立刻執行,
        # 不需要這種為了排程自動化情境設計的隨機延遲,加上這個參數跳過等待,
        # 避免明明沒有任何錯誤、卻因為卡在這個隨機延遲裡而被我們自己設的逾時機制誤判成失敗。
        cmd = ["certbot", "renew", "--non-interactive", "--no-random-sleep-on-renew"]
        if force:
            cmd.append("--force-renewal")
        yield f"data: {json.dumps({'message': '正在執行 certbot renew' + ('(強制換發)' if force else '') + '...', 'done': False}, ensure_ascii=False)}\n\n"
        rc, out, err = utils_sysstatus.run_cmd(cmd, timeout=120)
        output_text = (out or "") + (err or "")
        ok = rc == 0

        log_activity_entry("憑證管理-nginx手動續期", ok, detail=("(強制換發) " if force else "") + (output_text[-500:] if output_text else ""))

        if not ok:
            yield f"data: {json.dumps({'message': f'certbot renew 失敗: {output_text}', 'done': True, 'ok': False}, ensure_ascii=False)}\n\n"
            return

        # 先把certbot實際輸出的內容完整送給使用者看,讓使用者能自己判斷這次是不是真的有換發新憑證,
        # 還是憑證離到期還很久、certbot判斷還不用更新所以什麼都沒做(這是正常、預期的行為,不是錯誤)
        renew_output_message = "certbot renew 執行完成,輸出內容:\n" + output_text
        yield f"data: {json.dumps({'message': renew_output_message, 'done': False}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'message': '正在重新載入(reload) nginx 讓新憑證生效...', 'done': False}, ensure_ascii=False)}\n\n"

        reload_ok, reload_err = utils_sysstatus.reload_systemd_service("nginx.service")
        log_activity_entry("憑證管理-重啟服務(nginx.service)", reload_ok, detail="原因: certbot renew 後重新載入憑證(reload,非restart)" + (f", 錯誤: {reload_err}" if reload_err else ""))

        if reload_ok:
            yield f"data: {json.dumps({'message': '完成!nginx 已重新載入設定。如果上面的certbot輸出顯示「not yet due for renewal」,代表憑證離到期還很久、這次沒有真的換發新憑證,這是正常現象。', 'done': True, 'ok': True, 'certbot_output': output_text}, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'message': f'certbot renew 完成,但 nginx reload 失敗: {reload_err},請手動確認 nginx 狀態', 'done': True, 'ok': False}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/cert-status/vpp/upload", methods=["POST"])
@login_required
def api_cert_vpp_upload():
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"ok": False, "message": "沒有選擇檔案"}), 400

    f = request.files["file"]
    raw = f.read()

    # 上傳前先驗證這是不是一份能正常解析的VPP Token,不要盲目覆蓋掉現有可用的檔案
    try:
        decoded = base64.b64decode(raw)
        data = json.loads(decoded)
        new_org_name = data.get("orgName", "")
        new_exp_date = data.get("expDate", "")
    except Exception as e:
        return jsonify({"ok": False, "message": f"上傳的檔案無法解析,不是有效的VPP Content Token: {e}"}), 400

    token_path = CFG["cert_status"]["vpp_token_path"]
    tmp_path = token_path + ".tmp"
    with open(tmp_path, "wb") as out_f:
        out_f.write(raw)
    os.replace(tmp_path, token_path)

    log_activity_entry(
        "憑證管理-VPP Content Token更新", True,
        detail=f"新Token組織名稱={new_org_name}, 到期日={new_exp_date}",
    )
    return jsonify({
        "ok": True,
        "message": f"已更新VPP Content Token(組織名稱: {new_org_name}, 到期日: {new_exp_date}),"
                   f"之後所有VPP相關的查詢/授權指派/撤銷都會自動套用這份新Token,不需要重啟服務。",
    })

@app.route("/api/cert-status/dep/download-cert")
@login_required
def api_cert_dep_download_cert():
    """執行 cfg-get-cert.sh 產生公鑰憑證(要上傳到 ASM/ABM,讓Apple加密Token回傳用),
    直接把結果當檔案下載給使用者,不在畫面上顯示內容(避免複製貼上時漏字元或多空白)。
    """
    script = CFG["paths"]["cfg_get_cert_script"]
    cn_label = request.args.get("cn", "depserver")
    validity_days = request.args.get("validity_days", "365")

    env = get_nanodep_script_env()
    cwd = os.path.dirname(script) or None
    rc, out, err = utils.run_cmd([script, cn_label, validity_days], timeout=20, env=env, cwd=cwd)

    if rc != 0:
        log_activity_entry("憑證管理-DEP公鑰產生", False, detail=err or out)
        return jsonify({"ok": False, "message": f"產生公鑰失敗: {err or out}"}), 500

    log_activity_entry("憑證管理-DEP公鑰產生", True, detail=f"cn={cn_label}, validity_days={validity_days}")

    tmp_path = os.path.join("/tmp", f"dep-public-key-{int(time.time())}.pem")
    with open(tmp_path, "w") as f:
        f.write(out)
    return send_file(tmp_path, as_attachment=True, download_name="dep-public-key.pem", mimetype="application/x-pem-file")


@app.route("/api/cert-status/dep/upload-token", methods=["POST"])
@login_required
def api_cert_dep_upload_token():
    """接收從ASM/ABM下載回來的.p7m加密Token檔案,執行cfg-decrypt-tokens.sh解密並存入nanodep。
    這支腳本本身就是「解密+存入」一步到位(不是只解密給你看),成功執行完就代表新Token已經生效。
    """
    if "file" not in request.files or not request.files["file"].filename:
        return jsonify({"ok": False, "message": "沒有選擇檔案"}), 400

    f = request.files["file"]
    tmp_path = os.path.join("/tmp", f"dep-token-upload-{int(time.time())}.p7m")
    f.save(tmp_path)

    try:
        script = CFG["paths"]["cfg_decrypt_tokens_script"]
        env = get_nanodep_script_env()
        cwd = os.path.dirname(script) or None
        rc, out, err = utils.run_cmd([script, tmp_path], timeout=30, env=env, cwd=cwd)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if rc != 0:
        log_activity_entry("憑證管理-DEP Token更新", False, detail=err or out)
        return jsonify({"ok": False, "message": f"解密/匯入失敗: {err or out}"}), 500

    log_activity_entry("憑證管理-DEP Token更新", True, detail=out[:300] if out else None)
    return jsonify({
        "ok": True,
        "message": "已成功解密並存入新的DEP Token,不需要重啟服務,nanodep之後的DEP API呼叫會自動使用新Token。",
        "detail": out,
    })


@app.route("/api/cert-status/nanoaxm/update", methods=["POST"])
@login_required
def api_cert_nanoaxm_update():
    """更新NanoAXM的API憑證(client_id/key_id/私鑰),透過官方cfg-authcreds.sh腳本執行,
    不直接寫資料庫。屬於高風險操作(換掉整組API身分),需要先驗證目前登入者的密碼。
    """
    password = request.form.get("password", "")
    if not verify_current_user_password(password):
        log_activity_entry("憑證管理-NanoAXM憑證更新", False, detail="密碼驗證失敗,操作已取消")
        return jsonify({"ok": False, "message": "密碼不正確,操作已取消"}), 403

    client_id = (request.form.get("client_id") or "").strip()
    key_id = (request.form.get("key_id") or "").strip()
    if not client_id or not key_id:
        return jsonify({"ok": False, "message": "缺少 Client ID 或 Key ID"}), 400
    if "private_key" not in request.files or not request.files["private_key"].filename:
        return jsonify({"ok": False, "message": "沒有選擇私鑰檔案"}), 400

    f = request.files["private_key"]
    tmp_path = os.path.join("/tmp", f"nanoaxm-key-{int(time.time())}.pem")
    f.save(tmp_path)

    try:
        script = CFG["paths"]["cfg_authcreds_script"]
        env = get_nanoaxm_script_env()
        cwd = os.path.dirname(script) or None
        rc, out, err = utils.run_cmd([script, client_id, key_id, tmp_path], timeout=20, env=env, cwd=cwd)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if rc != 0:
        log_activity_entry("憑證管理-NanoAXM憑證更新", False, detail=f"client_id={client_id}, key_id={key_id}, error={err or out}")
        return jsonify({"ok": False, "message": f"更新失敗: {err or out}"}), 500

    log_activity_entry("憑證管理-NanoAXM憑證更新", True, detail=f"client_id={client_id}, key_id={key_id}")

    # 關鍵步驟:清除舊的ca_token快取。實際查證發現nanoaxm的/authcreds端點更新憑證時
    # 不會連帶清掉舊的token快取,導致拿舊憑證簽出來的快取跟新憑證的私鑰對不起來,
    # 造成後續查詢一律回報invalid_client。這裡直接清資料庫欄位,強制下次查詢時
    # nanoaxm用「目前」存的client_id/私鑰重新跟Apple簽發一次全新的token。
    _, _, _, axm_name_for_cache = get_nanoaxm_conn()
    cache_clear_ok, cache_clear_err = clear_nanoaxm_token_cache(axm_name_for_cache)
    log_activity_entry(
        "憑證管理-NanoAXM清除舊Token快取", cache_clear_ok,
        detail=cache_clear_err if not cache_clear_ok else f"axm_name={axm_name_for_cache}",
    )

    # nanoaxm現在已經接上MySQL持久化儲存(docker-compose.yml補上-storage=mysql後,
    # 使用者已經實測確認完全正常運作),重啟容器不會再把剛設定的憑證洗掉,
    # 恢復成跟其他五項憑證管理功能一致的「更新後自動重啟套用新設定」行為。
    restart_ok, restart_err = restart_service_and_log("docker", "nanoaxm-server", "更新API憑證後重啟套用新設定")

    message = "已成功更新NanoAXM憑證"
    if not cache_clear_ok:
        message += f",但清除舊Token快取失敗({cache_clear_err}),可能仍會遇到invalid_client錯誤,請手動清除 axm_names 表的 ca_token 欄位"
    message += ",服務已自動重啟。" if restart_ok else f",但服務重啟失敗: {restart_err},請手動確認 nanoaxm-server 狀態"

    return jsonify({"ok": True, "message": message, "detail": out})


@app.route("/api/cert-status/apns/current-topics")
@login_required
def api_cert_apns_current_topics():
    """回傳目前nanomdm裡已知的所有push cert topic,給前端在上傳新憑證前顯示,
    方便使用者上傳前先核對、上傳後比對topic是否有變(topic不同=所有裝置需要重新註冊)。
    """
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    results = utils_certs.check_apns_certs(CFG["mysql"], db_password, mobileconfig_dir=CFG["paths"]["mobileconfig_dir"])
    topics = []
    for r in results:
        detail = r.get("detail") or ""
        if detail.startswith("topic="):
            topics.append(detail[len("topic="):])
    return jsonify({"ok": True, "topics": topics, "results": results})


@app.route("/api/cert-status/apns/detect-topic", methods=["POST"])
@login_required
def api_cert_apns_detect_topic():
    """上傳新憑證前,不用真的送去nanomdm就能先知道這張憑證的topic是什麼,
    讓前端可以提早判斷「這會不會跟現有的topic不一樣」,加強警告文字。
    """
    if "cert" not in request.files or not request.files["cert"].filename:
        return jsonify({"ok": False, "message": "沒有選擇憑證檔案"}), 400

    cert_text = request.files["cert"].read().decode("utf-8", errors="ignore")
    topic, err = utils_certs.extract_topic_from_cert_pem(cert_text)
    if err:
        return jsonify({"ok": False, "message": err}), 400

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    results = utils_certs.check_apns_certs(CFG["mysql"], db_password, mobileconfig_dir=CFG["paths"]["mobileconfig_dir"])
    existing_topics = []
    for r in results:
        detail = r.get("detail") or ""
        if detail.startswith("topic="):
            existing_topics.append(detail[len("topic="):])

    return jsonify({
        "ok": True, "topic": topic,
        "topic_changed": bool(existing_topics) and topic not in existing_topics,
        "existing_topics": existing_topics,
    })


@app.route("/api/cert-status/apns/delete", methods=["POST"])
@login_required
def api_cert_apns_delete():
    """刪除指定topic的APNs Push憑證紀錄。僅剩一筆時強制拒絕刪除,
    這條規則不受密碼或任何確認影響——不能讓系統落入「完全沒有任何push憑證」的狀態,
    那樣所有裝置都會立刻失去推播管理能力。
    """
    data = request.json or {}
    topic = (data.get("topic") or "").strip()
    password = data.get("password", "")

    if not topic:
        return jsonify({"ok": False, "message": "缺少topic"}), 400
    if not verify_current_user_password(password):
        log_activity_entry("憑證管理-APNs憑證刪除", False, detail=f"密碼驗證失敗,topic={topic}")
        return jsonify({"ok": False, "message": "密碼不正確,操作已取消"}), 403

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    results = utils_certs.check_apns_certs(CFG["mysql"], db_password, mobileconfig_dir=CFG["paths"]["mobileconfig_dir"])
    existing_topics = [r["topic"] for r in results if r.get("topic")]

    if len(existing_topics) <= 1:
        log_activity_entry("憑證管理-APNs憑證刪除", False, detail=f"僅剩一筆,拒絕刪除,topic={topic}")
        return jsonify({"ok": False, "message": "目前只剩這一組APNs憑證,不能刪除,否則所有裝置都會立刻失去推播管理能力"}), 400
    if topic not in existing_topics:
        return jsonify({"ok": False, "message": "找不到這個topic對應的憑證"}), 404

    safe_topic = topic.replace("'", "''")
    args = [
        "docker", "exec", CFG["mysql"]["docker_container"], "mysql",
        f"-u{CFG['mysql']['db_user']}", f"-p{db_password}",
        "-N", "-B", "--raw", CFG["mysql"]["db_name"], "-e",
        f"DELETE FROM push_certs WHERE topic = '{safe_topic}';",
    ]
    rc, out, err = utils.run_cmd(args, timeout=15)
    ok = rc == 0
    log_activity_entry("憑證管理-APNs憑證刪除", ok, detail=f"topic={topic}" + (f", error={err or out}" if not ok else ""))

    if not ok:
        return jsonify({"ok": False, "message": f"刪除失敗: {err or out}"}), 500
    return jsonify({"ok": True, "message": f"已刪除 topic={topic} 這組憑證紀錄"})



@app.route("/api/cert-status/apns/upload", methods=["POST"])
@login_required
def api_cert_apns_upload():
    """上傳新的APNs Push憑證(cert+key兩個檔案),透過nanomdm官方 /v1/pushcert API寫入。
    高風險操作(換掉推播通道的身分),需要先驗證目前登入者的密碼。
    上傳後比對新舊topic是否一致:一致代表是正常續約(裝置不用重新註冊),
    不一致要明確警告使用者(所有裝置的推播通道已經失效,需要全部重新註冊)。
    """
    password = request.form.get("password", "")
    if not verify_current_user_password(password):
        log_activity_entry("憑證管理-APNs憑證更新", False, detail="密碼驗證失敗,操作已取消")
        return jsonify({"ok": False, "message": "密碼不正確,操作已取消"}), 403

    if "cert" not in request.files or not request.files["cert"].filename:
        return jsonify({"ok": False, "message": "沒有選擇憑證檔案(.pem)"}), 400
    if "key" not in request.files or not request.files["key"].filename:
        return jsonify({"ok": False, "message": "沒有選擇私鑰檔案"}), 400

    cert_bytes_for_topic_check = request.files["cert"].read()
    request.files["cert"].seek(0)  # 讀取後要把檔案指標倒回開頭,後面還要再讀一次完整內容

    # 伺服器端也要真的檢查topic有沒有變:前端雖然也做了同樣的偵測+二次密碼要求,
    # 但那只是提醒使用者、不能當作安全防線,一定要在後端這裡也獨立驗證一次,
    # 不能只靠前端傳來的欄位就相信「使用者已經看過警告」。
    env_for_topic_check = get_env_dict()
    db_password_for_topic_check = env_for_topic_check.get(CFG["mysql"]["db_password_env_key"], "")
    new_topic_preview, topic_extract_err = utils_certs.extract_topic_from_cert_pem(
        cert_bytes_for_topic_check.decode("utf-8", errors="ignore")
    )
    if not topic_extract_err:
        existing_results = utils_certs.check_apns_certs(CFG["mysql"], db_password_for_topic_check, mobileconfig_dir=CFG["paths"]["mobileconfig_dir"])
        existing_topics_for_check = [r["topic"] for r in existing_results if r.get("topic")]
        topic_will_change = bool(existing_topics_for_check) and new_topic_preview not in existing_topics_for_check
        if topic_will_change:
            confirm_password = request.form.get("confirm_password", "")
            if not confirm_password or not verify_current_user_password(confirm_password):
                log_activity_entry(
                    "憑證管理-APNs憑證更新", False,
                    detail=f"偵測到topic將變更為{new_topic_preview},但二次密碼確認未通過,操作已取消",
                )
                return jsonify({
                    "ok": False,
                    "message": "偵測到這張新憑證的topic跟現有的不一樣,請在「再次確認密碼」欄位正確輸入密碼才能繼續",
                    "topic_changed": True, "new_topic": new_topic_preview, "existing_topics": existing_topics_for_check,
                }), 400

    cert_bytes = request.files["cert"].read()
    key_bytes = request.files["key"].read()
    key_passphrase = request.form.get("key_passphrase", "")

    # 私鑰如果是加密的(PEM裡會看到ENCRYPTED標記),先嘗試解密再繼續。
    # openssl pkey是通用指令,RSA/EC等各種金鑰類型都能處理,不用像openssl rsa那樣只能處理RSA。
    # 密碼透過stdin傳給openssl(-passin stdin),不會出現在指令列引數裡,避免被其他系統使用者
    # 用 ps 看到密碼明文。
    key_text = key_bytes.decode("utf-8", errors="ignore")
    if "ENCRYPTED" in key_text:
        if not key_passphrase:
            return jsonify({
                "ok": False, "message": "私鑰是加密狀態,需要提供私鑰密碼才能繼續",
                "key_encrypted": True,
            }), 400

        tmp_key_path = os.path.join("/tmp", f"apns-key-encrypted-{int(time.time())}.pem")
        with open(tmp_key_path, "w") as f:
            f.write(key_text)
        try:
            rc, out, err = utils.run_cmd_with_stdin(
                ["openssl", "pkey", "-in", tmp_key_path, "-passin", "stdin"],
                key_passphrase, timeout=15,
            )
        finally:
            try:
                os.remove(tmp_key_path)
            except OSError:
                pass

        if rc != 0:
            log_activity_entry("憑證管理-APNs憑證更新", False, detail="私鑰解密失敗,可能是密碼不正確")
            return jsonify({
                "ok": False, "message": "私鑰解密失敗,請確認密碼是否正確",
                "key_encrypted": True,
            }), 400

        key_bytes = out.encode("utf-8")

    # 上傳前先取得目前已知的topic清單,上傳後才能比對有沒有變
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    old_results = utils_certs.check_apns_certs(CFG["mysql"], db_password, mobileconfig_dir=CFG["paths"]["mobileconfig_dir"])
    old_topics = set()
    for r in old_results:
        detail = r.get("detail") or ""
        if detail.startswith("topic="):
            old_topics.add(detail[len("topic="):])

    base_url, api_user, api_key = get_nanomdm_conn()
    if not base_url or not api_key:
        return jsonify({"ok": False, "message": ".env 內缺少 NANOMDM_BASE_URL 或 NANOMDM_API_KEY"}), 500

    combined = cert_bytes + b"\n" + key_bytes
    try:
        resp = requests.put(
            f"{base_url.rstrip('/')}/v1/pushcert",
            data=combined, auth=(api_user, api_key), timeout=20,
        )
    except requests.RequestException as e:
        log_activity_entry("憑證管理-APNs憑證更新", False, detail=str(e))
        return jsonify({"ok": False, "message": f"連線失敗: {e}"}), 500

    if resp.status_code >= 400:
        log_activity_entry("憑證管理-APNs憑證更新", False, detail=f"HTTP {resp.status_code}: {resp.text[:300]}")
        return jsonify({"ok": False, "message": f"上傳失敗(HTTP {resp.status_code}): {resp.text[:300]}"}), 500

    try:
        result_data = resp.json()
    except ValueError:
        result_data = {}
    new_topic = result_data.get("topic", "")
    not_after = result_data.get("not_after", "")

    topic_changed = bool(old_topics) and new_topic not in old_topics
    log_activity_entry(
        "憑證管理-APNs憑證更新", True,
        detail=f"新topic={new_topic}, 到期={not_after}, 舊topic清單={list(old_topics)}, topic是否變更={topic_changed}",
    )

    restart_ok, restart_err = restart_service_and_log("docker", "nanomdm-server", "更新APNs推播憑證後重啟套用新設定")

    # 自動同步精簡註冊描述檔的Topic欄位,讓之後新裝置註冊時能拿到正確的新topic。
    # 不管topic_changed是true還是false都呼叫,函式本身會判斷「已經是新值就不用更新」,
    # 這樣即使系統裡同時存在好幾組topic、這次上傳的剛好命中其中一組舊的但不是
    # enroll-template目前在用的那組,也能正確同步到位。
    sync_ok, sync_msg = utils_profiles.update_enroll_template_topic(CFG["paths"]["mobileconfig_dir"], new_topic)
    log_activity_entry("憑證管理-同步精簡註冊描述檔Topic", sync_ok, detail=sync_msg)

    # enroll-server.py是常駐程式,啟動時把範本檔案內容讀進記憶體一次就不再重讀,
    # 改了檔案內容不重啟這個服務不會生效(這是實際排查確認過的行為),
    # 所以真的有更新到內容時(不是「本來就是新值不用改」的情況),要連帶重啟它。
    enroll_restart_ok, enroll_restart_err = None, None
    if sync_ok and sync_msg.startswith("已將Topic從"):
        enroll_restart_ok, enroll_restart_err = restart_service_and_log(
            "systemd", "enroll-server.service", "同步精簡註冊描述檔Topic後重啟套用新內容"
        )

    other_files_with_mdm = utils_profiles.find_other_files_with_mdm_payload(CFG["paths"]["mobileconfig_dir"])

    message = f"已成功上傳新憑證(topic: {new_topic}, 到期日: {not_after})。"
    if topic_changed:
        message += "⚠️ 注意:新憑證的topic跟原本的不一樣!這代表所有已註冊裝置的推播通道已經失效,需要全部重新註冊才能恢復管理。"
    else:
        message += "topic跟原本相同,屬於正常續約,已註冊的裝置不需要重新註冊。"
    message += ",服務已自動重啟。" if restart_ok else f",但服務重啟失敗: {restart_err},請手動確認 nanomdm-server 狀態"
    message += f" 精簡註冊描述檔: {sync_msg}" if sync_ok else f" ⚠️ 精簡註冊描述檔同步失敗({sync_msg}),請自己手動確認並更新 Topic 欄位"
    if enroll_restart_ok is True:
        message += " enroll-server.service 已自動重啟套用新內容。"
    elif enroll_restart_ok is False:
        message += f" ⚠️ enroll-server.service 重啟失敗({enroll_restart_err}),新裝置註冊前請先手動重啟這個服務,否則會繼續拿到舊的Topic。"
    if other_files_with_mdm:
        message += f" ⚠️ 另外偵測到以下描述檔也包含MDM Payload,理論上一般群組描述檔不應該用到這個,請自行檢查是否需要一併更新: {', '.join(other_files_with_mdm)}"

    return jsonify({
        "ok": True, "message": message, "topic_changed": topic_changed,
        "new_topic": new_topic, "not_after": not_after, "old_topics": list(old_topics),
        "sync_ok": sync_ok, "other_files_with_mdm": other_files_with_mdm,
    })


@app.route("/api/cert-status/scep/current-info")
@login_required
def api_cert_scep_current_info():
    """回傳目前SCEP根CA的組織資訊(組織/OU/國別/簽發者名稱)與到期日,給前端在重新產生前顯示、預先帶入表單。
    順便回傳目前設定的站台標籤,當作「簽發者名稱(Common Name)」欄位的建議預設值。
    """
    ca_path = CFG["cert_status"]["scep_ca_path"]
    subject = utils_certs.get_cert_subject(ca_path)
    expiry_date, days_left, err = utils_certs.get_cert_enddate_from_file(ca_path)
    branding = config.reload_branding()
    return jsonify({
        "ok": True, "subject": subject,
        "expiry_date": expiry_date, "days_left": days_left, "expiry_error": err,
        "suggested_common_name": branding.get("site_label", ""),
    })


@app.route("/api/cert-status/scep/regenerate-stream")
@login_required
def api_cert_scep_regenerate_stream():
    """重新產生SCEP根CA(等同建立全新CA,不是延長既有CA效期)。
    這是六項憑證管理功能裡唯一真正不可逆的操作:所有已註冊裝置的身份憑證信任鏈會失效,
    需要全部重新註冊才能恢復管理。流程:
    1. 驗證密碼
    2. 備份現有ca.pem/ca.key(移出depot目錄,帶時間戳記,不是覆蓋掉)
    3. 用docker run執行 ca -init 產生全新CA
    4. 重啟nanomdm-scep(套用新CA簽發憑證)
    5. 重啟nanomdm-server(套用新CA做為信任根,-ca參數指向的檔案內容已經換新)
    """
    password = request.args.get("password", "")
    organization = request.args.get("organization", "").strip()
    organizational_unit = request.args.get("organizational_unit", "").strip()
    country = request.args.get("country", "").strip()
    years = request.args.get("years", "15").strip()
    common_name = request.args.get("common_name", "").strip()

    def generate():
        if not verify_current_user_password(password):
            log_activity_entry("憑證管理-SCEP根CA重新產生", False, detail="密碼驗證失敗,操作已取消")
            yield f"data: {json.dumps({'message': '密碼不正確,操作已取消', 'done': True, 'ok': False}, ensure_ascii=False)}\n\n"
            return

        if not organization or not country or not years.isdigit():
            yield f"data: {json.dumps({'message': '組織名稱/國別/效期年數為必填,且效期年數必須是數字', 'done': True, 'ok': False}, ensure_ascii=False)}\n\n"
            return

        depot_dir = CFG["cert_status"]["scep_depot_dir"]
        backup_dir = CFG["cert_status"]["scep_ca_backup_dir"]
        ca_pem_path = os.path.join(depot_dir, "ca.pem")
        ca_key_path = os.path.join(depot_dir, "ca.key")

        # ---- 步驟1: 備份現有的ca.pem/ca.key(移出depot目錄,不是覆蓋掉) ----
        yield f"data: {json.dumps({'message': '正在備份現有的CA檔案...', 'done': False}, ensure_ascii=False)}\n\n"
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backed_up = []
        try:
            for fname, src_path in [("ca.pem", ca_pem_path), ("ca.key", ca_key_path)]:
                if os.path.exists(src_path):
                    dst_path = os.path.join(backup_dir, f"{timestamp}_{fname}")
                    shutil.move(src_path, dst_path)
                    backed_up.append(dst_path)
        except Exception as e:
            log_activity_entry("憑證管理-SCEP根CA重新產生", False, detail=f"備份階段失敗: {e}")
            yield f"data: {json.dumps({'message': f'備份現有CA檔案時發生錯誤,已中止,沒有動到任何東西: {e}', 'done': True, 'ok': False}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({'message': f'已備份至: {backed_up}', 'done': False}, ensure_ascii=False)}\n\n"

        # ---- 步驟2: 執行 ca -init 產生全新CA ----
        yield f"data: {json.dumps({'message': '正在產生新的CA...', 'done': False}, ensure_ascii=False)}\n\n"

        # 關鍵:新產生的私鑰一定要用跟現有nanomdm-scep服務相同的密碼加密(docker-compose.yml裡
        # nanomdm-scep是用 -capass=${SCEP_CA_PASSWORD} 這個.env變數的值去解密ca.key),
        # 不然新金鑰會用空密碼產生,跟服務原本設定的密碼對不起來,導致服務啟動時解密失敗
        # (實際發生過的錯誤: "x509: decryption password incorrect")。
        env_for_scep = get_env_dict()
        scep_ca_password = env_for_scep.get("SCEP_CA_PASSWORD", "")
        if not scep_ca_password:
            for backup_path in backed_up:
                basename = os.path.basename(backup_path)
                if basename.endswith("_ca.pem"):
                    original_name = "ca.pem"
                elif basename.endswith("_ca.key"):
                    original_name = "ca.key"
                else:
                    continue
                try:
                    shutil.move(backup_path, os.path.join(depot_dir, original_name))
                except Exception:
                    pass
            log_activity_entry("憑證管理-SCEP根CA重新產生", False, detail=".env 內缺少 SCEP_CA_PASSWORD,已中止並還原備份")
            yield f"data: {json.dumps({'message': '.env 內缺少 SCEP_CA_PASSWORD,無法確保新金鑰密碼跟現有服務一致,已中止並還原備份,沒有動到任何東西', 'done': True, 'ok': False}, ensure_ascii=False)}\n\n"
            return

        docker_image = CFG["cert_status"]["scep_docker_image"]
        entrypoint = CFG["cert_status"]["scep_entrypoint"]
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{depot_dir}:/depot",
            "--entrypoint", entrypoint,
            docker_image,
            "ca", "-init", "-depot", "/depot",
            "-organization", organization,
            "-organizational_unit", organizational_unit,
            "-country", country,
            "-years", years,
            "-key-password", scep_ca_password,
        ]
        if common_name:
            cmd.extend(["-common_name", common_name])
        rc, out, err = utils_sysstatus.run_cmd(cmd, timeout=60)
        if rc != 0:
            log_activity_entry("憑證管理-SCEP根CA重新產生", False, detail=f"ca -init 失敗: {err or out}")
            # 產生失敗,把備份的檔案還原回去,不要讓系統處於「舊CA被搬走、新CA又沒產生成功」的中間狀態
            for backup_path in backed_up:
                basename = os.path.basename(backup_path)
                if basename.endswith("_ca.pem"):
                    original_name = "ca.pem"
                elif basename.endswith("_ca.key"):
                    original_name = "ca.key"
                else:
                    continue  # 不是我們自己備份出來的已知檔名格式,不處理,避免誤動作
                try:
                    shutil.move(backup_path, os.path.join(depot_dir, original_name))
                except Exception:
                    pass
            yield f"data: {json.dumps({'message': f'產生新CA失敗,已還原備份: {err or out}', 'done': True, 'ok': False}, ensure_ascii=False)}\n\n"
            return

        yield f"data: {json.dumps({'message': '新CA已產生,正在重啟 nanomdm-scep...', 'done': False}, ensure_ascii=False)}\n\n"

        # ---- 步驟3+4: 重啟兩個容器 ----
        scep_ok, scep_err = restart_service_and_log("docker", "nanomdm-scep", "SCEP根CA重新產生後重啟套用新CA")
        yield f"data: {json.dumps({'message': ('nanomdm-scep 已重啟' if scep_ok else f'nanomdm-scep 重啟失敗: {scep_err}') + ',正在重啟 nanomdm-server...', 'done': False}, ensure_ascii=False)}\n\n"

        mdm_ok, mdm_err = restart_service_and_log("docker", "nanomdm-server", "SCEP根CA重新產生後重啟套用新CA信任根")

        log_activity_entry(
            "憑證管理-SCEP根CA重新產生", True,
            detail=f"簽發者={common_name or 'MICROMDM SCEP CA(預設)'}, 組織={organization}, OU={organizational_unit}, 國別={country}, 效期={years}年, 備份位置={backed_up}",
        )

        message = (
            f"✅ 新的SCEP根CA已產生完成(效期 {years} 年)。"
            f"⚠️ 所有先前已註冊的裝置,其身份憑證信任鏈已經失效,需要全部清除並重新註冊才能恢復管理。"
            f"舊的CA檔案已備份於: {backed_up}"
        )
        if not scep_ok:
            message += f" ⚠️ nanomdm-scep 重啟失敗({scep_err}),請手動確認狀態。"
        if not mdm_ok:
            message += f" ⚠️ nanomdm-server 重啟失敗({mdm_err}),請手動確認狀態。"

        yield f"data: {json.dumps({'message': message, 'done': True, 'ok': True}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")




# ---------------------------------------------------------------------------
# ASM 所有裝置 / MDM Server 管理 (透過 nanoAXM)
# 比照 ASM 軟體資訊的做法:背景排程 30 分鐘同步一次到 CSV 快取,
# 頁面預設讀快取,可以手動重新整理即時查詢。
# ---------------------------------------------------------------------------
_asm_devices_cache_lock = threading.Lock()


def refresh_asm_devices_cache_once():
    base_url, api_key, org_type, axm_name = get_nanoaxm_conn()
    if not base_url or not api_key or not axm_name:
        return False, ".env 內缺少 NANOAXM 設定", 0
    try:
        server_rows, device_by_server, unassigned = utils_asm.build_asm_overview(base_url, api_key, org_type, axm_name)
    except Exception as e:
        return False, str(e), 0
    with _asm_devices_cache_lock:
        utils_asm.write_asm_devices_cache(
            CFG["asm_devices_cache"]["servers_csv"], CFG["asm_devices_cache"]["devices_csv"],
            server_rows, device_by_server, unassigned,
        )
    return True, None, len(server_rows)


def _asm_devices_scheduler_loop():
    while True:
        try:
            ok, msg, count = refresh_asm_devices_cache_once()
            print(f"[ASM裝置排程] {'同步完成,共 ' + str(count) + ' 台伺服器' if ok else '同步失敗: ' + str(msg)}")
            log_system_activity_entry("ASM所有裝置-自動同步", ok, detail=msg if not ok else f"共 {count} 台伺服器")
        except Exception as e:
            print(f"[ASM裝置排程] 發生例外: {e}")
            log_system_activity_entry("ASM所有裝置-自動同步", False, detail=str(e))
        time.sleep(CFG["asm_devices_cache"]["refresh_interval_seconds"])


def start_asm_devices_scheduler():
    t = threading.Thread(target=_asm_devices_scheduler_loop, daemon=True)
    t.start()


@app.route("/asm-devices")
@login_required
def asm_devices_page():
    return render_template(
        "asm_devices.html", active="asm_devices",
        refresh_interval_minutes=CFG["asm_devices_cache"]["refresh_interval_seconds"] // 60,
    )


@app.route("/api/asm-devices/cache")
@login_required
def api_asm_devices_cache():
    server_rows, device_by_server, unassigned, mtime = utils_asm.read_asm_devices_cache(
        CFG["asm_devices_cache"]["servers_csv"], CFG["asm_devices_cache"]["devices_csv"]
    )
    return jsonify({
        "ok": True,
        "servers": server_rows,
        "device_by_server": device_by_server,
        "unassigned_devices": unassigned,
        "unassigned_count": len(unassigned),
        "last_sync": _format_last_sync(mtime) if mtime else None,
    })


@app.route("/api/asm-devices/refresh-stream")
@login_required
def api_asm_devices_refresh_stream():
    base_url, api_key, org_type, axm_name = get_nanoaxm_conn()
    if not base_url or not api_key or not axm_name:
        return jsonify({"ok": False, "message": ".env 內缺少 NANOAXM 設定"}), 500

    def generate():
        try:
            for update in utils_asm.build_asm_overview_stream(base_url, api_key, org_type, axm_name):
                if update.get("done"):
                    with _asm_devices_cache_lock:
                        utils_asm.write_asm_devices_cache(
                            CFG["asm_devices_cache"]["servers_csv"], CFG["asm_devices_cache"]["devices_csv"],
                            update["server_rows"], update["device_by_server"], update["unassigned_devices"],
                        )
                    mtime = os.path.getmtime(CFG["asm_devices_cache"]["devices_csv"])
                    log_activity_entry("ASM所有裝置-手動同步", True, detail=update["message"])
                    yield f"data: {json.dumps({'done': True, 'message': update['message'], 'last_sync': _format_last_sync(mtime)}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'message': update['message'], 'done': False}, ensure_ascii=False)}\n\n"
        except Exception as e:
            log_activity_entry("ASM所有裝置-手動同步", False, detail=str(e))
            yield f"data: {json.dumps({'error': str(e), 'done': True}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/asm-devices/export")
@login_required
def api_asm_devices_export():
    server_rows, device_by_server, unassigned, mtime = utils_asm.read_asm_devices_cache(
        CFG["asm_devices_cache"]["servers_csv"], CFG["asm_devices_cache"]["devices_csv"]
    )
    if mtime is None:
        return jsonify({"ok": False, "message": "尚無快取資料,請先按「重新整理」執行一次同步"}), 404

    export_path = os.path.join(os.path.dirname(CFG["asm_devices_cache"]["devices_csv"]), "asm_devices_editable.csv")
    utils_asm.export_editable_csv(export_path, server_rows, device_by_server, unassigned)
    return send_file(export_path, as_attachment=True, download_name="asm_devices_editable.csv")


@app.route("/api/asm-devices/import/preview", methods=["POST"])
@login_required
def api_asm_devices_import_preview():
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "沒有上傳檔案"}), 400

    server_rows, device_by_server, unassigned, mtime = utils_asm.read_asm_devices_cache(
        CFG["asm_devices_cache"]["servers_csv"], CFG["asm_devices_cache"]["devices_csv"]
    )
    if mtime is None:
        return jsonify({"ok": False, "message": "尚無快取資料,請先按「重新整理」執行一次同步"}), 400

    try:
        content = request.files["file"].read().decode("utf-8-sig")
    except Exception as e:
        return jsonify({"ok": False, "message": f"檔案讀取失敗: {e}"}), 400

    uploaded_rows = utils_asm.parse_editable_csv(content)
    changes = utils_asm.diff_editable_import(uploaded_rows, server_rows, device_by_server, unassigned)
    return jsonify({"ok": True, "changes": changes, "change_count": len(changes)})


@app.route("/api/asm-devices/import/apply", methods=["POST"])
@login_required
def api_asm_devices_import_apply():
    data = request.json or {}
    changes = data.get("changes") or []  # [{device_id, target_server_id}, ...],只送matched=True的項目

    groups = {}
    for c in changes:
        target_id = c.get("target_server_id")
        device_id = c.get("device_id")
        if not target_id or not device_id:
            continue
        groups.setdefault(target_id, []).append(device_id)

    if not groups:
        return jsonify({"ok": False, "message": "沒有任何可套用的變更"}), 400

    base_url, api_key, org_type, axm_name = get_nanoaxm_conn()
    if not base_url or not api_key or not axm_name:
        return jsonify({"ok": False, "message": ".env 內缺少 NANOAXM 設定"}), 500

    activities = []
    for target_id, device_ids in groups.items():
        try:
            result = utils_asm.reassign_devices(base_url, api_key, org_type, axm_name, target_id, device_ids)
            activity_id = result.get("data", {}).get("id")
            activities.append({
                "target_server_id": target_id, "activity_id": activity_id,
                "device_count": len(device_ids), "ok": bool(activity_id),
            })
            log_activity_entry(
                "ASM裝置改派-CSV匯入", bool(activity_id),
                detail=f"目標伺服器={target_id}, 裝置數={len(device_ids)}, activity_id={activity_id}",
            )
        except utils_asm.AsmError as e:
            activities.append({
                "target_server_id": target_id, "activity_id": None,
                "device_count": len(device_ids), "ok": False, "error": str(e),
            })
            log_activity_entry(
                "ASM裝置改派-CSV匯入", False,
                detail=f"目標伺服器={target_id}, 裝置數={len(device_ids)}, error={e}",
            )

    return jsonify({"ok": True, "activities": activities})


@app.route("/api/asm-devices/server/<server_id>")
@login_required
def api_asm_devices_server_detail(server_id):
    base_url, api_key, org_type, axm_name = get_nanoaxm_conn()
    if not base_url or not api_key or not axm_name:
        return jsonify({"ok": False, "message": ".env 內缺少 NANOAXM 設定"}), 500
    try:
        detail = utils_asm.get_server_detail(base_url, api_key, org_type, axm_name, server_id)
        return jsonify({"ok": True, "detail": detail})
    except utils_asm.AsmError as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/asm-devices/reassign/start", methods=["POST"])
@login_required
def api_asm_devices_reassign_start():
    data = request.json or {}
    device_ids = data.get("device_ids") or []
    target_server_id = (data.get("target_server_id") or "").strip()
    if not device_ids or not target_server_id:
        return jsonify({"ok": False, "message": "缺少裝置清單或目標伺服器"}), 400

    base_url, api_key, org_type, axm_name = get_nanoaxm_conn()
    if not base_url or not api_key or not axm_name:
        return jsonify({"ok": False, "message": ".env 內缺少 NANOAXM 設定"}), 500

    try:
        result = utils_asm.reassign_devices(base_url, api_key, org_type, axm_name, target_server_id, device_ids)
    except utils_asm.AsmError as e:
        log_activity_entry("ASM裝置改派", False, detail=f"目標伺服器={target_server_id}, 裝置數={len(device_ids)}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 500

    activity_id = result.get("data", {}).get("id")
    if not activity_id:
        log_activity_entry("ASM裝置改派", False, detail=f"沒有拿到activity id: {result}")
        return jsonify({"ok": False, "message": f"建立改派作業後沒有拿到 activity id: {result}"}), 500

    log_activity_entry("ASM裝置改派", True, detail=f"目標伺服器={target_server_id}, 裝置數={len(device_ids)}, activity_id={activity_id}")
    return jsonify({"ok": True, "activity_id": activity_id, "initial": result})


@app.route("/api/asm-devices/reassign/progress/<activity_id>")
@login_required
def api_asm_devices_reassign_progress(activity_id):
    base_url, api_key, org_type, axm_name = get_nanoaxm_conn()

    def generate():
        try:
            for update in utils_asm.poll_activity_until_done(base_url, api_key, org_type, axm_name, activity_id):
                yield f"data: {json.dumps(update, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e), 'done': True}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# 裝置註冊狀態 (交叉比對 Apple DEP 即時清單 / devices.csv / nanomdm enrollment)
# ---------------------------------------------------------------------------
@app.route("/device-enrollment-status")
@login_required
def device_enrollment_status_page():
    return render_template("device_enrollment_status.html", active="device_enrollment_status")


OS_UPDATE_COMPOSITE_COMMANDS = {"CheckOSUpdate", "DownloadOSUpdate", "InstallOSUpdate"}


def get_latest_available_update_product_key(enrollment_id):
    """從最近的指令歷史裡找出最新一筆 AvailableOSUpdates 回應,取出第一筆(最新)可用更新的 ProductKey。
    「下載更新」「安裝更新」這兩個複合指令靠這個自動代查,使用者不用自己去翻回應記錄複製貼上。
    找不到就回傳 (None, 說明原因的訊息)。
    """
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    rows, rc, err = utils.query_command_history(CFG["mysql"], db_password, enrollment_id, limit=30)
    if rc != 0:
        return None, f"查詢指令歷史失敗: {err}"
    for row in rows:  # 已經依時間新到舊排序
        if row.get("request_type") != "AvailableOSUpdates" or not row.get("result"):
            continue
        parsed = utils.parse_plist_text(row["result"])
        updates = parsed.get("AvailableOSUpdates") if parsed else None
        if updates:
            product_key = updates[0].get("ProductKey")
            if product_key:
                return product_key, None
    return None, "找不到可用更新資訊,請先執行「查詢更新」,裝置連線回報後再試一次"


def dispatch_os_update_command(request_type, base_url, api_user, api_key, enrollment_id):
    """處理「查詢更新/下載更新/安裝更新」這三個複合指令:
    - 查詢更新:自動組合「觸發掃描」+「查詢可用更新」兩個底層指令一起送出
      (AvailableOSUpdates 如果沒先掃描過會查不到資料,這是Apple官方論壇證實的行為)
    - 下載/安裝更新:自動代查最近一次掃描到的 ProductKey,使用者不用手動輸入
    回傳 (ok, result_or_message)
    """
    if request_type == "CheckOSUpdate":
        status1, result1 = utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, "ScheduleOSUpdateScan", {"Force": True})
        status2, result2 = utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, "AvailableOSUpdates", {})
        ok = status1 < 400 and status2 < 400
        return ok, {"觸發掃描": result1, "查詢可用更新": result2}

    if request_type in ("DownloadOSUpdate", "InstallOSUpdate"):
        product_key, err = get_latest_available_update_product_key(enrollment_id)
        if not product_key:
            return False, err
        install_action = "DownloadOnly" if request_type == "DownloadOSUpdate" else "InstallASAP"
        status, result = utils.send_mdm_command(
            base_url, api_user, api_key, enrollment_id, "ScheduleOSUpdate",
            {"Updates": [{"ProductKey": product_key, "InstallAction": install_action}]},
        )
        return status < 400, result

    return None, None  # 不是這幾個複合指令


def get_wifi_mac_lookup():
    """從「ASM 所有裝置」的快取(all_asm_server.csv/all_asm_devices.csv)建立 序號->WiFi MAC 對照表。
    這是 classic DEP API 沒有、只有新版 ABM/ASM API 才有的欄位,所以要跨頁面共用同一份快取來查。
    """
    _, asm_device_by_server, asm_unassigned, _ = utils_asm.read_asm_devices_cache(
        CFG["asm_devices_cache"]["servers_csv"], CFG["asm_devices_cache"]["devices_csv"]
    )
    lookup = {}
    for rows in asm_device_by_server.values():
        for d in rows:
            if d.get("serialNumber"):
                lookup[d["serialNumber"]] = utils_asm.normalize_mac(d.get("wifiMacAddress", ""))
    for d in asm_unassigned:
        if d.get("serialNumber"):
            lookup[d["serialNumber"]] = utils_asm.normalize_mac(d.get("wifiMacAddress", ""))
    return lookup


def enrich_rows_with_wifi_mac(rows, wifi_mac_lookup=None):
    """幫每一列補上最新的WiFi MAC:ASM快取查得到就優先用(比較即時),
    查不到才退回devices.csv裡上次記錄的值(rows裡原本就有的wifi_mac欄位)。
    """
    lookup = wifi_mac_lookup if wifi_mac_lookup is not None else get_wifi_mac_lookup()
    for row in rows:
        serial = row.get("serial_number")
        row["wifi_mac"] = lookup.get(serial) or row.get("wifi_mac", "")
    return rows


def get_device_enrollment_overview():
    """即時抓取 Apple DEP 裝置清單,交叉比對 devices.csv 與 nanomdm enrollment 狀態。
    回傳 (rows, error_message) - 供頁面API、匯出、匯入比對共用,避免各自重寫一份。
    """
    base_url, api_key, dep_name, _ = get_nanodep_conn()
    if not base_url or not api_key or not dep_name:
        return None, ".env 內缺少 NANODEP_BASE_URL / NANODEP_API_KEY / NANODEP_NAME"

    try:
        dep_devices = utils_depprofile.fetch_all_dep_devices(base_url, api_key, dep_name)
    except utils_depprofile.ApplyError as e:
        return None, str(e)
    except Exception as e:
        return None, f"連線失敗: {e}"

    devices_csv = utils.read_devices_csv(CFG["paths"]["devices_csv"])

    # 交叉比對ASM裝置快取取得WiFi MAC(classic DEP API本身沒有這個欄位,
    # 只有新版ABM/ASM API的orgDevices有,所以要跟"ASM所有裝置"頁已經在維護的快取比對)
    wifi_mac_by_serial = get_wifi_mac_lookup()

    profile_templates = utils_depprofile.list_dep_profiles(CFG["paths"]["dep_profiles_dir"], CFG["paths"]["groups_json"])
    uuid_to_filename = {
        p["last_applied_uuid"]: p["filename"]
        for p in profile_templates if p.get("last_applied_uuid")
    }

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    serial_to_enrollment = {}
    merged, rc, _ = utils.query_and_merge_devices(CFG["mysql"], db_password, CFG["paths"]["devices_csv"])
    if rc == 0:
        serial_to_enrollment = {r["serial_number"]: r["enrollment_id"] for r in merged}

    rows = []
    for d in dep_devices:
        serial = d.get("serial_number", "")
        csv_info = devices_csv.get(serial, {"device_name": "", "group": "", "wifi_mac": ""})
        profile_uuid = d.get("profile_uuid") or ""
        # ASM快取裡查得到的WiFi MAC優先(比較即時),查不到才退回devices.csv上次記錄的值
        wifi_mac = wifi_mac_by_serial.get(serial) or csv_info.get("wifi_mac", "")
        rows.append({
            "serial_number": serial,
            "wifi_mac": wifi_mac,
            "model": d.get("model", ""),
            "description": d.get("description", ""),
            "color": d.get("color", ""),
            "device_name": csv_info.get("device_name", ""),
            "group": csv_info.get("group", ""),
            "profile_uuid": profile_uuid,
            "profile_filename": uuid_to_filename.get(profile_uuid, ""),
            "profile_status": d.get("profile_status") or "empty",
            "profile_push_time": d.get("profile_push_time") or "",
            "enrollment_id": serial_to_enrollment.get(serial, ""),
        })
    return rows, None


@app.route("/api/device-enrollment-status")
@login_required
def api_device_enrollment_status():
    rows, error = get_device_enrollment_overview()
    if error:
        log_activity_entry("裝置註冊狀態-資料擷取", False, detail=error)
        return jsonify({"ok": False, "message": error}), 500
    log_activity_entry("裝置註冊狀態-資料擷取", True, detail=f"共 {len(rows)} 筆")
    return jsonify({"ok": True, "rows": rows})


def apply_group_change_effects(serial_number, new_group_name, enrollment_id):
    """群組變更後,自動套用新群組配對的:
    1. enroll json 的 profile_uuid -> 透過 nanodep 重新指派這台裝置(DEP)
    2. mobileconfig -> 透過 nanomdm 送 InstallProfile 指令推送(只有裝置已經完成MDM註冊、有enrollment_id才能推送)
    回傳 steps dict,不會因為任何一步失敗就中斷另一步。
    """
    steps = {}
    groups = utils.load_groups(CFG["paths"]["groups_json"])
    group_info = groups.get(new_group_name, {}) if new_group_name else {}

    # Step 1: DEP profile 重新指派
    enroll_json = group_info.get("enroll_json")
    if not new_group_name:
        steps["dep_reassign"] = {"ok": False, "skipped": True, "message": "群組設為空白(未分類),不會變動 DEP 指派"}
    elif not enroll_json:
        steps["dep_reassign"] = {"ok": False, "message": f"群組「{new_group_name}」目前沒有配對註冊檔(enroll json),略過"}
    else:
        try:
            dep_data = utils_depprofile.read_dep_profile(CFG["paths"]["dep_profiles_dir"], enroll_json)
            profile_uuid = dep_data.get("last_applied_uuid")
            if not profile_uuid:
                steps["dep_reassign"] = {"ok": False, "message": f"註冊檔 {enroll_json} 尚未套用過(沒有 profile_uuid),請先到「ADE 註冊設定」套用一次"}
            else:
                base_url, api_key, dep_name, _ = get_nanodep_conn()
                if not base_url or not api_key or not dep_name:
                    steps["dep_reassign"] = {"ok": False, "message": ".env 內缺少 NANODEP 設定"}
                else:
                    result = utils_depprofile.assign_single_device(base_url, api_key, dep_name, profile_uuid, serial_number)
                    steps["dep_reassign"] = {"ok": True, "profile_uuid": profile_uuid, "enroll_json": enroll_json, "result": result}
        except utils_depprofile.ApplyError as e:
            steps["dep_reassign"] = {"ok": False, "message": str(e)}
        except Exception as e:
            steps["dep_reassign"] = {"ok": False, "message": f"未預期錯誤: {e}"}

    # Step 2: mobileconfig 推送(需要裝置已經完成MDM註冊)
    mobileconfig = group_info.get("mobileconfig")
    if not new_group_name:
        steps["mobileconfig_push"] = {"ok": False, "skipped": True, "message": "群組設為空白(未分類),不會推送描述檔"}
    elif not mobileconfig:
        steps["mobileconfig_push"] = {"ok": False, "message": f"群組「{new_group_name}」目前沒有配對描述檔(mobileconfig),略過"}
    elif not enrollment_id:
        steps["mobileconfig_push"] = {"ok": False, "message": "這台裝置尚未完成 MDM 註冊(沒有 enrollment_id),暫時無法推送,待裝置完成註冊後請手動重新整理再處理一次"}
    else:
        try:
            mc_path = os.path.join(CFG["paths"]["mobileconfig_dir"], mobileconfig)
            with open(mc_path, "rb") as f:
                mc_bytes = f.read()
            base_url, api_user, api_key = get_nanomdm_conn()
            if not base_url or not api_key:
                steps["mobileconfig_push"] = {"ok": False, "message": ".env 內缺少 NANOMDM 設定"}
            else:
                status_code, result = utils.send_mdm_command(
                    base_url, api_user, api_key, enrollment_id, "InstallProfile", {"Payload": mc_bytes}
                )
                steps["mobileconfig_push"] = {"ok": status_code < 400, "mobileconfig": mobileconfig, "status_code": status_code, "result": result}
        except FileNotFoundError:
            steps["mobileconfig_push"] = {"ok": False, "message": f"找不到描述檔案 {mobileconfig}"}
        except Exception as e:
            steps["mobileconfig_push"] = {"ok": False, "message": f"未預期錯誤: {e}"}

    log_activity_entry(
        "群組變更-DEP重新指派", bool(steps.get("dep_reassign", {}).get("ok")),
        detail=steps.get("dep_reassign", {}).get("message", ""), serial=serial_number, group=new_group_name,
    )
    log_activity_entry(
        "群組變更-推送描述檔", bool(steps.get("mobileconfig_push", {}).get("ok")),
        detail=steps.get("mobileconfig_push", {}).get("message", ""), serial=serial_number, group=new_group_name,
    )
    return steps


@app.route("/api/device-enrollment-status/save", methods=["POST"])
@login_required
def api_device_enrollment_status_save():
    data = request.json or {}
    serial = (data.get("serial_number") or "").strip()
    device_name = (data.get("device_name") or "").strip()
    group = (data.get("group") or "").strip()
    enrollment_id = (data.get("enrollment_id") or "").strip() or None
    wifi_mac = data.get("wifi_mac")  # 選填,前端會把目前畫面上看到的MAC一併帶回來持久化

    if not serial:
        return jsonify({"ok": False, "message": "缺少序號"}), 400

    devices_before = utils.read_devices_csv(CFG["paths"]["devices_csv"])
    old_group = devices_before.get(serial, {}).get("group", "")
    old_device_name = devices_before.get(serial, {}).get("device_name", "")

    try:
        saved = utils.upsert_device_row(CFG["paths"]["devices_csv"], serial, device_name, group, wifi_mac=wifi_mac)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    result = {"ok": True, "data": saved, "group_changed": False, "name_changed": False}

    # 裝置名稱有變更、且這台裝置已經完成MDM註冊(有enrollment_id)時,直接推送改名指令,
    # 不用再另外跑一趟「派送命令」手動改
    if device_name != old_device_name and device_name:
        result["name_changed"] = True
        if enrollment_id:
            try:
                base_url, api_user, api_key = get_nanomdm_conn()
                if not base_url or not api_key:
                    result["name_push"] = {"ok": False, "message": ".env 內缺少 NANOMDM_BASE_URL 或 NANOMDM_API_KEY"}
                else:
                    status_code, cmd_result = utils.send_mdm_command(
                        base_url, api_user, api_key, enrollment_id, "Settings",
                        {"Settings": [{"Item": "DeviceName", "DeviceName": device_name}]},
                    )
                    ok = status_code < 400
                    result["name_push"] = {"ok": ok, "result": cmd_result}
                    log_activity_entry("裝置註冊狀態-修改名稱", ok, detail=f"舊名稱={old_device_name} -> 新名稱={device_name}", serial=serial, device_name=device_name, group=group)
            except Exception as e:
                result["name_push"] = {"ok": False, "message": str(e)}
                log_activity_entry("裝置註冊狀態-修改名稱", False, detail=f"error={e}", serial=serial, device_name=device_name, group=group)
        else:
            result["name_push"] = {"ok": False, "message": "這台裝置還沒有完成 MDM 註冊(沒有 enrollment_id),暫時無法推送改名指令,待裝置完成註冊後名稱設定會在下次連線時套用"}
            log_activity_entry("裝置註冊狀態-修改名稱", False, detail=f"舊名稱={old_device_name} -> 新名稱={device_name}(裝置尚未MDM註冊,名稱僅存本地,未推送)", serial=serial, device_name=device_name, group=group)

    if group != old_group and group:
        result["group_changed"] = True
        log_activity_entry("裝置註冊狀態-變更群組", True, detail=f"舊群組={old_group or '(未分類)'} -> 新群組={group}", serial=serial, device_name=device_name, group=group)
        result["sync_steps"] = apply_group_change_effects(serial, group, enrollment_id)
    return jsonify(result)


@app.route("/api/device-enrollment-status/export/all")
@login_required
def api_device_enrollment_status_export_all():
    rows, error = get_device_enrollment_overview()
    if error:
        return jsonify({"ok": False, "message": error}), 500
    export_path = os.path.join(os.path.dirname(CFG["paths"]["devices_csv"]), "device_enrollment_export_all.csv")
    utils.write_device_enrollment_export_csv(export_path, rows)
    return send_file(export_path, as_attachment=True, download_name="device_enrollment_export_all.csv")


@app.route("/api/device-enrollment-status/export/unassigned")
@login_required
def api_device_enrollment_status_export_unassigned():
    rows, error = get_device_enrollment_overview()
    if error:
        return jsonify({"ok": False, "message": error}), 500
    rows = [r for r in rows if not r["device_name"] or not r["group"]]
    export_path = os.path.join(os.path.dirname(CFG["paths"]["devices_csv"]), "device_enrollment_export_unassigned.csv")
    utils.write_device_enrollment_export_csv(export_path, rows)
    return send_file(export_path, as_attachment=True, download_name="device_enrollment_export_unassigned.csv")


@app.route("/api/device-enrollment-status/import/preview", methods=["POST"])
@login_required
def api_device_enrollment_status_import_preview():
    if "file" not in request.files:
        return jsonify({"ok": False, "message": "沒有上傳檔案"}), 400

    rows, error = get_device_enrollment_overview()
    if error:
        return jsonify({"ok": False, "message": error}), 500

    try:
        content = request.files["file"].read().decode("utf-8-sig")
    except Exception as e:
        return jsonify({"ok": False, "message": f"檔案讀取失敗: {e}"}), 400

    groups = utils.load_groups(CFG["paths"]["groups_json"])
    try:
        uploaded_rows = utils.parse_device_enrollment_import_csv(content)
    except Exception as e:
        return jsonify({"ok": False, "message": f"CSV 格式解析失敗: {e}"}), 400

    changes, mismatches = utils.diff_device_enrollment_import(uploaded_rows, rows, groups)
    return jsonify({"ok": True, "changes": changes, "mismatches": mismatches})


@app.route("/api/device-enrollment-status/import/apply-stream", methods=["POST"])
@login_required
def api_device_enrollment_status_import_apply_stream():
    data = request.json or {}
    changes = data.get("changes") or []

    def generate():
        total = len(changes)
        for idx, change in enumerate(changes, start=1):
            serial = change.get("serial_number")
            device_name = change.get("device_name")
            group = change.get("group")
            wifi_mac = change.get("wifi_mac")
            enrollment_id = change.get("enrollment_id") or None
            name_changed = bool(change.get("name_changed"))
            group_changed = bool(change.get("group_changed"))

            step_result = {"serial_number": serial, "index": idx, "total": total}
            try:
                utils.upsert_device_row(CFG["paths"]["devices_csv"], serial, device_name, group, wifi_mac=wifi_mac)
                step_result["save"] = {"ok": True}
                log_activity_entry("裝置註冊狀態-CSV匯入存檔", True, serial=serial, device_name=device_name, group=group)
            except ValueError as e:
                step_result["save"] = {"ok": False, "message": str(e)}
                log_activity_entry("裝置註冊狀態-CSV匯入存檔", False, detail=str(e), serial=serial, device_name=device_name, group=group)
                yield f"data: {json.dumps(step_result, ensure_ascii=False)}\n\n"
                continue

            if name_changed and enrollment_id:
                try:
                    base_url, api_user, api_key = get_nanomdm_conn()
                    status_code, result = utils.send_mdm_command(
                        base_url, api_user, api_key, enrollment_id, "Settings",
                        {"Settings": [{"Item": "DeviceName", "DeviceName": device_name}]},
                    )
                    ok = status_code < 400
                    step_result["rename_command"] = {"ok": ok, "result": result}
                    log_activity_entry("裝置註冊狀態-CSV匯入改名", ok, serial=serial, device_name=device_name, group=group)
                except Exception as e:
                    step_result["rename_command"] = {"ok": False, "message": str(e)}
                    log_activity_entry("裝置註冊狀態-CSV匯入改名", False, detail=str(e), serial=serial, device_name=device_name, group=group)
            elif name_changed:
                step_result["rename_command"] = {"ok": False, "message": "裝置尚未完成MDM註冊,無法送出改名指令"}
                log_activity_entry("裝置註冊狀態-CSV匯入改名", False, detail="裝置尚未完成MDM註冊,無法送出改名指令", serial=serial, device_name=device_name, group=group)

            if group_changed:
                step_result["group_sync"] = apply_group_change_effects(serial, group, enrollment_id)

            yield f"data: {json.dumps(step_result, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': total}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# 所有裝置與命令頁
# ---------------------------------------------------------------------------
@app.route("/devices")
@login_required
def devices_page():
    return render_template(
        "devices.html", active="devices", command_defs=COMMAND_DEFS,
        refresh_interval_minutes=CFG["devices_status_cache"]["refresh_interval_seconds"] // 60,
    )


@app.route("/api/devices")
@login_required
def api_devices():
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    merged, rc, err = utils.query_and_merge_devices(CFG["mysql"], db_password, CFG["paths"]["devices_csv"])
    if rc != 0:
        return jsonify({"ok": False, "message": err, "rows": []}), 500

    merged = enrich_rows_with_wifi_mac(merged)
    status_cache, status_mtime = utils.read_devices_status_cache(CFG["devices_status_cache"]["csv_path"])

    rows = []
    for i, row in enumerate(merged, start=1):
        status = status_cache.get(row["serial_number"], {})
        rows.append({
            "seq": i, **row,
            "battery_level": status.get("battery_level", ""),
            "device_capacity": status.get("device_capacity", ""),
            "available_device_capacity": status.get("available_device_capacity", ""),
            "os_version": status.get("os_version", ""),
            "available_os_version": status.get("available_os_version", ""),
            "available_os_product_key": status.get("available_os_product_key", ""),
            "os_update_is_downloaded": status.get("os_update_is_downloaded", ""),
            "os_update_status": status.get("os_update_status", ""),
            "ip_address": status.get("ip_address", ""),
            "lost_mode_enabled": status.get("lost_mode_enabled", ""),
            "location_lat": status.get("location_lat", ""),
            "location_lng": status.get("location_lng", ""),
            "location_at": status.get("location_at", ""),
            "location_accuracy": status.get("location_accuracy", ""),
        })
    return jsonify({
        "ok": True, "rows": rows,
        "status_last_sync": _format_last_sync(status_mtime) if status_mtime else None,
    })


@app.route("/api/devices/save", methods=["POST"])
@login_required
def api_devices_save():
    data = request.json or {}
    serial_number = (data.get("serial_number") or "").strip()
    device_name = (data.get("device_name") or "").strip()
    group = (data.get("group") or "").strip()
    if not serial_number:
        return jsonify({"ok": False, "message": "缺少 serial_number"}), 400

    # 檢測:如果devices.csv裡這台裝置目前沒有記錄WiFi MAC,自動從ASM快取查詢並補上
    # (wifi_mac傳None給upsert_device_row代表「不異動」,只有真的查到新值時才會覆蓋)
    wifi_mac = None
    existing = utils.read_devices_csv(CFG["paths"]["devices_csv"]).get(serial_number, {})
    if not existing.get("wifi_mac"):
        found_mac = get_wifi_mac_lookup().get(serial_number)
        if found_mac:
            wifi_mac = found_mac

    try:
        saved = utils.upsert_device_row(CFG["paths"]["devices_csv"], serial_number, device_name, group, wifi_mac=wifi_mac)
        return jsonify({"ok": True, "message": f"已儲存 {serial_number}", "data": saved})
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/devices/details/<serial_number>")
@login_required
def api_device_details(serial_number):
    script = CFG["paths"]["dep_device_details_script"]
    rc, out, err = utils.run_dep_device_details(
        script, serial_number, env_file_path=CFG["paths"]["env_file"], extra_env=get_nanodep_script_env()
    )
    if rc != 0:
        return jsonify({"ok": False, "message": err or out}), 500
    try:
        parsed = json.loads(out)
        device_info = parsed.get("devices", {}).get(serial_number, parsed)
        return jsonify({"ok": True, "data": device_info, "raw": out})
    except json.JSONDecodeError:
        return jsonify({"ok": False, "message": "回傳內容不是合法 JSON", "raw": out}), 500


def build_mdm_command_params(request_type, params):
    """把前端傳來的 params 轉換成實際要送給 nanomdm 的 (request_type_actual, plist_params)。
    參數有誤時丟出 ValueError,由呼叫端轉成 400 錯誤回應。"""
    params = params or {}

    if request_type == "SetDeviceName":
        return "Settings", {
            "Settings": [{"Item": "DeviceName", "DeviceName": params.get("DeviceName", "")}]
        }

    if request_type == "InstallApplication":
        adam_id = params.get("iTunesStoreID", "")
        try:
            adam_id_int = int(adam_id)
        except ValueError:
            raise ValueError("adamId 必須是數字")
        return "InstallApplication", {
            "iTunesStoreID": adam_id_int,
            "Options": {"PurchaseMethod": 1},
            "ManagementFlags": 1,
        }

    if request_type == "RemoveApplication":
        identifier = (params.get("Identifier") or "").strip()
        if identifier.isdigit():
            raise ValueError(
                f"「{identifier}」看起來是 adamId(數字),不是 Bundle ID。"
                f"RemoveApplication 需要的是 Bundle ID(格式類似 com.xxx.yyy),"
                f"請從下拉建議清單選擇,或用「查詢App受管理狀態」指令查出正確的 Bundle ID。"
            )
        return "RemoveApplication", {"Identifier": identifier}

    if request_type == "ManagedApplicationList":
        identifiers_raw = (params.get("Identifiers") or "").strip()
        if not identifiers_raw:
            return "ManagedApplicationList", {}
        identifiers = [x.strip() for x in identifiers_raw.split(",") if x.strip()]
        return "ManagedApplicationList", {"Identifiers": identifiers}

    if request_type == "DeviceLock":
        plist_params = {}
        message = (params.get("Message") or "").strip()
        phone = (params.get("PhoneNumber") or "").strip()
        if message:
            plist_params["Message"] = message
        if phone:
            plist_params["PhoneNumber"] = phone
        return "DeviceLock", plist_params

    if request_type == "EnableLostMode":
        return "EnableLostMode", {
            "Message": params.get("Message", ""),
            "PhoneNumber": params.get("PhoneNumber", ""),
            "Footnote": params.get("Footnote", ""),
        }

    if request_type == "ScheduleOSUpdateScan":
        force_str = str(params.get("Force", "true")).strip().lower()
        return "ScheduleOSUpdateScan", {"Force": force_str in ("1", "true", "yes")}

    if request_type == "ScheduleOSUpdate":
        product_key = (params.get("ProductKey") or "").strip()
        if not product_key:
            raise ValueError("ScheduleOSUpdate 需要提供 ProductKey(請先執行「查詢可用的 iOS / 軟體更新」取得)")
        update_item = {
            "ProductKey": product_key,
            "InstallAction": (params.get("InstallAction") or "Default").strip() or "Default",
        }
        if params.get("ProductVersion"):
            update_item["ProductVersion"] = params.get("ProductVersion").strip()
        return "ScheduleOSUpdate", {"Updates": [update_item]}

    if request_type == "DeviceInformation":
        # 原本完全沒帶Queries參數送出,裝置不知道要回傳什麼資訊。
        # 這裡補上一組常用的查詢項目(BatteryLevel等,經查證都是Apple官方合法的query key)。
        default_queries = [
            "DeviceName", "OSVersion", "BuildVersion", "ModelName", "SerialNumber",
            "DeviceCapacity", "AvailableDeviceCapacity", "BatteryLevel", "WiFiMAC",
            "IsSupervised", "IsCloudBackupEnabled",
        ]
        queries = params.get("Queries") or default_queries
        return "DeviceInformation", {"Queries": queries}

    # 其餘都是不需要額外參數轉換的簡單指令(RestartDevice, DeviceLock, AvailableOSUpdates 等)
    return request_type, {}


@app.route("/api/vpp-apps-list")
@login_required
def api_vpp_apps_list():
    """給「安裝/移除 App」的輸入框做建議清單用,直接複用「ASM 軟體資訊」頁已經在維護的快取,
    不用另外呼叫API。回傳adamId跟bundleId的對照,避免使用者手動輸入時把兩種ID搞混
    (這是「移除App」出現「未受管理」錯誤的常見原因——把adamId打進bundle id欄位)。
    """
    rows, _ = utils.read_vpp_cache_csv(CFG["paths"]["vpp_cache_csv"])
    apps = [
        {
            "adam_id": r.get("Adam ID", ""), "bundle_id": r.get("Bundle ID", ""),
            "name": r.get("軟體名稱", ""), "available": r.get("剩餘量", ""),
        }
        for r in rows
    ]
    return jsonify({"ok": True, "apps": apps})


@app.route("/api/devices/command", methods=["POST"])
@login_required
def api_devices_command():
    data = request.json or {}
    enrollment_id = data.get("enrollment_id")
    request_type = data.get("request_type")
    params = data.get("params") or {}
    serial_number = data.get("serial_number")

    # 查出裝置名稱與群組,讓底下所有操作紀錄都能一併帶上,方便在系統紀錄頁識別是哪台裝置
    device_name, device_group = None, None
    if serial_number:
        devices_lookup = utils.read_devices_csv(CFG["paths"]["devices_csv"])
        device_info = devices_lookup.get(serial_number, {})
        device_name = device_info.get("device_name")
        device_group = device_info.get("group")

    if not enrollment_id or not request_type:
        return jsonify({"ok": False, "message": "缺少必要參數"}), 400
    if request_type not in COMMAND_DEFS:
        return jsonify({"ok": False, "message": f"不支援的指令類型: {request_type}"}), 400

    base_url, api_user, api_key = get_nanomdm_conn()
    if not base_url or not api_key:
        return jsonify({"ok": False, "message": ".env 內缺少 NANOMDM_BASE_URL 或 NANOMDM_API_KEY"}), 500

    # 查詢更新/下載更新/安裝更新是複合指令(自動組合多個底層指令、自動代查ProductKey),走專屬處理流程
    if request_type in OS_UPDATE_COMPOSITE_COMMANDS:
        try:
            ok, result = dispatch_os_update_command(request_type, base_url, api_user, api_key, enrollment_id)
            log_activity_entry(request_type, ok, detail=f"enrollment_id={enrollment_id}", serial=serial_number, device_name=device_name, group=device_group)
            if ok:
                return jsonify({"ok": True, "result": result})
            return jsonify({"ok": False, "message": str(result)}), 500
        except Exception as e:
            log_activity_entry(request_type, False, detail=f"enrollment_id={enrollment_id}, error={e}", serial=serial_number, device_name=device_name, group=device_group)
            return jsonify({"ok": False, "message": str(e)}), 500

    try:
        request_type_actual, plist_params = build_mdm_command_params(request_type, params)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    # 安裝App前一定要先指派VPP授權,否則Apple會回報「無法取得App許可證」
    if request_type == "InstallApplication":
        if not serial_number:
            return jsonify({"ok": False, "message": "缺少裝置序號,無法指派VPP授權"}), 400
        try:
            vpp_result = utils.assign_vpp_license(
                CFG["cert_status"]["vpp_token_path"], serial_number, plist_params["iTunesStoreID"]
            )
        except Exception as e:
            log_activity_entry(request_type, False, detail=f"VPP授權指派失敗: {e}", serial=serial_number, device_name=device_name, group=device_group)
            return jsonify({"ok": False, "message": f"VPP授權指派失敗: {e}"}), 500

        if vpp_result.get("status") not in (0, "0", None):
            log_activity_entry(request_type, False, detail=f"VPP授權指派失敗: {vpp_result}", serial=serial_number, device_name=device_name, group=device_group)
            return jsonify({"ok": False, "message": f"VPP授權指派失敗,未送出安裝指令: {vpp_result}"}), 500

    # ClearPasscode一定要帶UnlockToken(裝置完成註冊時回報給nanomdm的資料),
    # 沒有帶會被Apple判定成CommandFormatError
    if request_type == "ClearPasscode":
        env = get_env_dict()
        db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
        unlock_token, token_err = utils.get_unlock_token(CFG["mysql"], db_password, enrollment_id)
        if token_err:
            log_activity_entry(request_type, False, detail=f"查詢UnlockToken失敗: {token_err}", serial=serial_number, device_name=device_name, group=device_group)
            return jsonify({"ok": False, "message": f"查詢UnlockToken失敗: {token_err}"}), 500
        if not unlock_token:
            log_activity_entry(request_type, False, detail="這台裝置沒有記錄到UnlockToken", serial=serial_number, device_name=device_name, group=device_group)
            return jsonify({"ok": False, "message": "這台裝置沒有記錄到UnlockToken,無法清除密碼(裝置可能從未回報過這項資料)"}), 400
        plist_params["UnlockToken"] = unlock_token

    try:
        status_code, result = utils.send_mdm_command(
            base_url, api_user, api_key, enrollment_id, request_type_actual, plist_params
        )
        ok = status_code < 400
        log_activity_entry(request_type, ok, detail=f"enrollment_id={enrollment_id}", serial=serial_number, device_name=device_name, group=device_group)

        # 遺失模式的啟用/解除狀態只能靠我們自己記錄(不是查MDM就能即時知道),
        # 送出成功後順便更新本地追蹤的狀態快取,「取得裝置定位」按鈕才知道要不要顯示
        if ok and request_type in ("EnableLostMode", "DisableLostMode") and serial_number:
            try:
                utils.set_lost_mode_state(
                    CFG["devices_status_cache"]["csv_path"], serial_number,
                    enabled=(request_type == "EnableLostMode"),
                )
            except Exception:
                pass  # 狀態記錄失敗不該影響指令本身已經送出成功的結果

        return jsonify({"ok": ok, "status_code": status_code, "result": result})
    except Exception as e:
        log_activity_entry(request_type, False, detail=f"enrollment_id={enrollment_id}, error={e}", serial=serial_number, device_name=device_name, group=device_group)
        return jsonify({"ok": False, "message": str(e)}), 500


LATEST_INFO_RESULT_KEY = {
    "DeviceInformation": "QueryResponses",
    "AvailableOSUpdates": "AvailableOSUpdates",
}


@app.route("/api/devices/latest-info/<enrollment_id>")
@login_required
def api_devices_latest_info(enrollment_id):
    """從最近的指令歷史裡找出最新一筆指定類型指令的回應內容。
    DeviceInformation 的實際資料包在 QueryResponses 裡;AvailableOSUpdates 的
    資料則是直接在頂層的 AvailableOSUpdates 陣列裡,兩者結構不一樣要分開處理。
    """
    info_type = request.args.get("type", "DeviceInformation")
    if info_type not in LATEST_INFO_RESULT_KEY:
        return jsonify({"ok": False, "message": f"不支援的查詢類型: {info_type}"}), 400
    result_key = LATEST_INFO_RESULT_KEY[info_type]

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    rows, rc, err = utils.query_command_history(CFG["mysql"], db_password, enrollment_id, limit=30)
    if rc != 0:
        return jsonify({"ok": False, "message": err}), 500

    for row in rows:  # query_command_history 已經依 created_at 由新到舊排序
        if row.get("request_type") != info_type or not row.get("result"):
            continue
        parsed = utils.parse_plist_text(row["result"])
        payload = parsed.get(result_key) if parsed else None
        if payload:
            return jsonify({
                "ok": True, "found": True,
                "data": utils.json_safe(payload),
                "result_updated_at": row.get("result_updated_at"),
            })

    return jsonify({"ok": True, "found": False})


@app.route("/api/devices/command-history/<enrollment_id>")
@login_required
def api_device_command_history(enrollment_id):
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    rows, rc, err = utils.query_command_history(CFG["mysql"], db_password, enrollment_id, limit=30)
    if rc != 0:
        return jsonify({"ok": False, "message": err}), 500

    vpp_cache_path = CFG["paths"]["vpp_cache_csv"]

    result_rows = []
    for row in rows:
        parsed = utils.parse_plist_text(row.get("result"))
        detail = {}
        if parsed:
            for k, v in parsed.items():
                if k in ("Status", "CommandUUID", "UDID"):
                    continue
                detail[k] = v

        # InstallApplication/RemoveApplication這兩種指令類型,回應內容本身通常不會講
        # 「是哪個App」,這個資訊要從原始送出的指令內容(command欄位)裡取得,
        # 再透過VPP快取(adamId/Bundle ID對照軟體名稱)補上使用者看得懂的App資訊。
        app_info = None
        request_type = row.get("request_type")
        if request_type in ("InstallApplication", "RemoveApplication"):
            parsed_command = utils.parse_plist_text(row.get("command"))
            command_body = (parsed_command or {}).get("Command", {})
            if request_type == "InstallApplication":
                adam_id = command_body.get("iTunesStoreID")
                if adam_id:
                    vpp_info = utils.lookup_vpp_app_info(vpp_cache_path, adam_id=adam_id)
                    app_info = {
                        "adam_id": adam_id,
                        "bundle_id": vpp_info["bundle_id"] if vpp_info else None,
                        "name": vpp_info["name"] if vpp_info else None,
                    }
            elif request_type == "RemoveApplication":
                identifier = command_body.get("Identifier")
                if identifier:
                    vpp_info = utils.lookup_vpp_app_info(vpp_cache_path, bundle_id=identifier)
                    app_info = {
                        "bundle_id": identifier,
                        "name": vpp_info["name"] if vpp_info else None,
                    }

        result_rows.append({
            "command_uuid": row.get("command_uuid"),
            "request_type": row.get("request_type"),
            "status": row.get("status"),  # None/NULL 代表尚未收到回應(仍在等待中)
            "created_at": row.get("created_at"),
            "result_updated_at": row.get("result_updated_at"),
            "detail": utils.json_safe(detail),
            "raw_result": row.get("result"),
            "app_info": app_info,
        })

    return jsonify({"ok": True, "rows": result_rows})


@app.route("/api/devices/push/<enrollment_id>")
@login_required
def api_devices_push(enrollment_id):
    base_url, api_user, api_key = get_nanomdm_conn()
    try:
        status_code, result = utils.trigger_push(base_url, api_user, api_key, enrollment_id)
        return jsonify({"ok": status_code < 400, "status_code": status_code, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# ---------------------------------------------------------------------------
# 描述檔編輯 (mobileconfig)
# ---------------------------------------------------------------------------
@app.route("/profiles")
@login_required
def profiles_page():
    return render_template("profiles.html", active="profiles")


@app.route("/api/profiles/schema")
@login_required
def api_profiles_schema():
    return jsonify({
        "ok": True,
        "top_level_fields": utils_profiles.TOP_LEVEL_FIELDS,
        "payload_schema": utils_profiles.PAYLOAD_SCHEMA,
    })


@app.route("/api/profiles")
@login_required
def api_profiles_list():
    try:
        files = utils_profiles.list_mobileconfig_files(CFG["paths"]["mobileconfig_dir"], CFG["paths"]["groups_json"])
        return jsonify({"ok": True, "files": files})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/profiles/<filename>")
@login_required
def api_profiles_get(filename):
    try:
        form = utils_profiles.read_mobileconfig_as_form(CFG["paths"]["mobileconfig_dir"], filename)
        return jsonify({"ok": True, **form})
    except FileNotFoundError:
        return jsonify({"ok": False, "message": "找不到這個檔案"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": f"讀取或解析失敗: {e}"}), 500


@app.route("/api/profiles/save", methods=["POST"])
@login_required
def api_profiles_save():
    data = request.json or {}
    filename = (data.get("filename") or "").strip()
    top_level = data.get("top_level") or {}
    payloads = data.get("payloads") or {}
    is_new = bool(data.get("is_new"))

    try:
        utils_profiles.validate_filename(filename)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    mobileconfig_dir = CFG["paths"]["mobileconfig_dir"]
    full_path = os.path.join(mobileconfig_dir, filename)
    if is_new and os.path.exists(full_path):
        return jsonify({"ok": False, "message": f"檔案 {filename} 已經存在,請換一個檔名或改用編輯"}), 400

    payload_identifier = (top_level.get("PayloadIdentifier") or "").strip()
    duplicate_file = utils_profiles.check_duplicate_payload_identifier(
        mobileconfig_dir, payload_identifier, exclude_filename=filename
    )
    if duplicate_file:
        return jsonify({
            "ok": False,
            "message": f"PayloadIdentifier「{payload_identifier}」已經被「{duplicate_file}」使用了。"
                       f"兩份描述檔用一樣的識別碼,推送到裝置上時後推送的會直接取代先推送的那份,請換一個不重複的值。",
        }), 400

    existing_uuids = {} if is_new else utils_profiles.get_existing_uuids(mobileconfig_dir, filename)
    unmanaged_payloads = data.get("unmanaged_payloads") or []

    sign_kwargs = get_profile_signing_kwargs()

    try:
        warnings = utils_profiles.save_mobileconfig(
            mobileconfig_dir, filename, top_level, payloads, unmanaged_payloads, existing_uuids,
            **sign_kwargs,
        )
        log_activity_entry("群組描述檔-存檔", True, detail=filename)
        return jsonify({"ok": True, "message": f"已儲存 {filename}", "warnings": warnings})
    except Exception as e:
        log_activity_entry("群組描述檔-存檔", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": f"儲存失敗: {e}"}), 500


@app.route("/api/profiles/validate", methods=["POST"])
@login_required
def api_profiles_validate():
    """只做驗證,不寫入檔案,回傳警告清單與plist是否能正常組建"""
    data = request.json or {}
    top_level = data.get("top_level") or {}
    payloads = data.get("payloads") or {}
    unmanaged_payloads = data.get("unmanaged_payloads") or []
    try:
        plist_bytes, warnings = utils_profiles.build_mobileconfig(top_level, payloads, unmanaged_payloads)
        # 確認能被正確解析回來
        import plistlib as _plistlib
        _plistlib.loads(plist_bytes)
        return jsonify({"ok": True, "valid": True, "warnings": warnings, "size": len(plist_bytes)})
    except Exception as e:
        return jsonify({"ok": True, "valid": False, "warnings": [], "message": str(e)})


@app.route("/api/profiles/delete", methods=["POST"])
@login_required
def api_profiles_delete():
    data = request.json or {}
    filename = (data.get("filename") or "").strip()
    try:
        utils_profiles.delete_mobileconfig(CFG["paths"]["mobileconfig_dir"], filename, CFG["paths"]["groups_json"])
        log_activity_entry("群組描述檔-刪除", True, detail=filename)
        return jsonify({"ok": True, "message": f"已刪除 {filename}"})
    except FileNotFoundError as e:
        log_activity_entry("群組描述檔-刪除", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": str(e)}), 404
    except ValueError as e:
        log_activity_entry("群組描述檔-刪除", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        log_activity_entry("群組描述檔-刪除", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/profiles/download/<filename>")
@login_required
def api_profiles_download(filename):
    try:
        utils_profiles.validate_filename(filename)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    full_path = os.path.join(CFG["paths"]["mobileconfig_dir"], filename)
    if not os.path.exists(full_path):
        return jsonify({"ok": False, "message": "找不到這個檔案"}), 404
    return send_file(full_path, as_attachment=True, download_name=filename)


@app.route("/api/profiles/apply-to-group-stream")
@login_required
def api_profiles_apply_to_group_stream():
    """把這份mobileconfig透過MDM InstallProfile推送給它目前配對群組的所有裝置,逐台顯示進度。
    只有已經配對了群組的檔案才能用這個功能(系統保護檔案沒有單一配對群組,不適用)。
    """
    filename = request.args.get("filename")
    if not filename:
        return jsonify({"ok": False, "message": "缺少檔名"}), 400

    try:
        utils_profiles.validate_filename(filename)
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400

    groups = utils.load_groups(CFG["paths"]["groups_json"])
    target_group = utils.find_group_by_paired_file(groups, "mobileconfig", filename)

    def generate():
        if not target_group:
            yield f"data: {json.dumps({'error': '這份描述檔目前沒有配對任何群組,無法批次推送', 'done': True}, ensure_ascii=False)}\n\n"
            return

        mc_path = os.path.join(CFG["paths"]["mobileconfig_dir"], filename)
        try:
            with open(mc_path, "rb") as f:
                mc_bytes = f.read()
        except Exception as e:
            yield f"data: {json.dumps({'error': f'讀取描述檔失敗: {e}', 'done': True}, ensure_ascii=False)}\n\n"
            return

        env = get_env_dict()
        db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
        merged, rc, err = utils.query_and_merge_devices(CFG["mysql"], db_password, CFG["paths"]["devices_csv"])
        if rc != 0:
            yield f"data: {json.dumps({'error': f'查詢裝置清單失敗: {err}', 'done': True}, ensure_ascii=False)}\n\n"
            return

        target_devices = [d for d in merged if d.get("group") == target_group]
        total = len(target_devices)
        yield f"data: {json.dumps({'message': f'群組「{target_group}」共 {total} 台裝置,開始推送...', 'done': False}, ensure_ascii=False)}\n\n"

        base_url, api_user, api_key = get_nanomdm_conn()
        success_count = 0
        for idx, dev in enumerate(target_devices, start=1):
            enrollment_id = dev.get("enrollment_id")
            serial = dev.get("serial_number")
            if not enrollment_id:
                yield f"data: {json.dumps({'index': idx, 'total': total, 'serial_number': serial, 'ok': False, 'message': '尚未完成MDM註冊,略過'}, ensure_ascii=False)}\n\n"
                continue
            try:
                status_code, result = utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, "InstallProfile", {"Payload": mc_bytes})
                ok = status_code < 400
                if ok:
                    success_count += 1
                log_activity_entry(f"批次推送描述檔-{filename}", ok, detail=f"群組={target_group}, 序號={serial}")
                yield f"data: {json.dumps({'index': idx, 'total': total, 'serial_number': serial, 'ok': ok, 'result': result}, ensure_ascii=False)}\n\n"
            except Exception as e:
                log_activity_entry(f"批次推送描述檔-{filename}", False, detail=f"群組={target_group}, 序號={serial}, error={e}")
                yield f"data: {json.dumps({'index': idx, 'total': total, 'serial_number': serial, 'ok': False, 'message': str(e)}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'done': True, 'total': total, 'success_count': success_count, 'target_group': target_group}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/profiles/duplicate", methods=["POST"])
@login_required
def api_profiles_duplicate():
    data = request.json or {}
    source_filename = (data.get("source_filename") or "").strip()
    new_filename = (data.get("new_filename") or "").strip()
    try:
        utils_profiles.duplicate_mobileconfig(
            CFG["paths"]["mobileconfig_dir"], source_filename, new_filename, **get_profile_signing_kwargs()
        )
        log_activity_entry("群組描述檔-再製", True, detail=f"來源={source_filename}, 新檔名={new_filename}")
        return jsonify({"ok": True, "message": f"已複製為 {new_filename}"})
    except (FileNotFoundError, ValueError) as e:
        log_activity_entry("群組描述檔-再製", False, detail=f"來源={source_filename}, 新檔名={new_filename}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        log_activity_entry("群組描述檔-再製", False, detail=f"來源={source_filename}, 新檔名={new_filename}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/profiles/unpaired-groups")
@login_required
def api_profiles_unpaired_groups():
    current_filename = request.args.get("filename")
    groups = utils_profiles.get_unpaired_groups(CFG["paths"]["groups_json"], current_filename)
    return jsonify({"ok": True, "groups": groups})


@app.route("/api/profiles/assign", methods=["POST"])
@login_required
def api_profiles_assign():
    data = request.json or {}
    filename = (data.get("filename") or "").strip()
    group_name = data.get("group_name") or None
    try:
        utils_profiles.assign_mobileconfig_to_group(CFG["paths"]["groups_json"], filename, group_name)
        message = f"已將 {filename} 指派給群組「{group_name}」" if group_name else f"已取消 {filename} 的群組指派"
        return jsonify({"ok": True, "message": message})
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# ---------------------------------------------------------------------------
# ADE (DEP) 註冊 profile 管理
# ---------------------------------------------------------------------------
@app.route("/dep-profiles")
@login_required
def dep_profiles_page():
    return render_template("dep_profiles.html", active="dep_profiles")


@app.route("/api/dep-profiles/schema")
@login_required
def api_dep_profiles_schema():
    return jsonify({
        "ok": True,
        "fields": utils_depprofile.DEP_PROFILE_FIELDS,
        "skip_setup_items": utils_depprofile.SKIP_SETUP_ITEMS,
        "skip_setup_item_labels": utils_depprofile.SKIP_SETUP_ITEM_LABELS,
        "unverified_skip_items": utils_depprofile.UNVERIFIED_SKIP_SETUP_ITEMS,
    })


@app.route("/api/dep-profiles")
@login_required
def api_dep_profiles_list():
    try:
        files = utils_depprofile.list_dep_profiles(CFG["paths"]["dep_profiles_dir"], CFG["paths"]["groups_json"])
        return jsonify({"ok": True, "files": files})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/dep-profiles/<filename>")
@login_required
def api_dep_profiles_get(filename):
    try:
        data = utils_depprofile.read_dep_profile(CFG["paths"]["dep_profiles_dir"], filename)
        return jsonify({"ok": True, **data})
    except FileNotFoundError:
        return jsonify({"ok": False, "message": "找不到這個檔案"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": f"讀取失敗: {e}"}), 500


@app.route("/api/dep-profiles/save", methods=["POST"])
@login_required
def api_dep_profiles_save():
    data = request.json or {}
    filename = (data.get("filename") or "").strip()
    save_data = {
        "apple_profile": data.get("apple_profile") or {},
        "last_applied_uuid": data.get("last_applied_uuid"),
        "last_applied_at": data.get("last_applied_at"),
    }
    try:
        utils_depprofile.save_dep_profile(CFG["paths"]["dep_profiles_dir"], filename, save_data)
        log_activity_entry("群組註冊檔-存檔", True, detail=filename)
        return jsonify({"ok": True, "message": f"已儲存 {filename}"})
    except ValueError as e:
        log_activity_entry("群組註冊檔-存檔", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        log_activity_entry("群組註冊檔-存檔", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": f"儲存失敗: {e}"}), 500


@app.route("/api/dep-profiles/duplicate", methods=["POST"])
@login_required
def api_dep_profiles_duplicate():
    data = request.json or {}
    source_filename = (data.get("source_filename") or "").strip()
    new_filename = (data.get("new_filename") or "").strip()
    try:
        utils_depprofile.duplicate_dep_profile(CFG["paths"]["dep_profiles_dir"], source_filename, new_filename)
        log_activity_entry("群組註冊檔-再製", True, detail=f"來源={source_filename}, 新檔名={new_filename}")
        return jsonify({"ok": True, "message": f"已複製為 {new_filename}"})
    except (FileNotFoundError, ValueError) as e:
        log_activity_entry("群組註冊檔-再製", False, detail=f"來源={source_filename}, 新檔名={new_filename}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        log_activity_entry("群組註冊檔-再製", False, detail=f"來源={source_filename}, 新檔名={new_filename}, error={e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/dep-profiles/unpaired-groups")
@login_required
def api_dep_profiles_unpaired_groups():
    current_filename = request.args.get("filename")
    groups = utils_depprofile.get_unpaired_groups(CFG["paths"]["groups_json"], current_filename)
    return jsonify({"ok": True, "groups": groups})


@app.route("/api/dep-profiles/assign", methods=["POST"])
@login_required
def api_dep_profiles_assign():
    data = request.json or {}
    filename = (data.get("filename") or "").strip()
    group_name = data.get("group_name") or None
    try:
        utils_depprofile.assign_dep_profile_to_group(CFG["paths"]["groups_json"], filename, group_name)
        message = f"已將 {filename} 指派給群組「{group_name}」" if group_name else f"已取消 {filename} 的群組指派"
        return jsonify({"ok": True, "message": message})
    except ValueError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/dep-profiles/delete", methods=["POST"])
@login_required
def api_dep_profiles_delete():
    data = request.json or {}
    filename = (data.get("filename") or "").strip()
    try:
        utils_depprofile.delete_dep_profile(CFG["paths"]["dep_profiles_dir"], filename, CFG["paths"]["groups_json"])
        log_activity_entry("群組註冊檔-刪除", True, detail=filename)
        return jsonify({"ok": True, "message": f"已刪除 {filename}"})
    except FileNotFoundError as e:
        log_activity_entry("群組註冊檔-刪除", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": str(e)}), 404
    except ValueError as e:
        log_activity_entry("群組註冊檔-刪除", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": str(e)}), 400
    except Exception as e:
        log_activity_entry("群組註冊檔-刪除", False, detail=f"{filename}: {e}")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/dep-profiles/apply", methods=["POST"])
@login_required
def api_dep_profiles_apply():
    """實際套用:呼叫nanodep完成 define + (set-assigner 或 assign-to-group-devices) + verify + 重啟depsyncer"""
    data = request.json or {}
    filename = (data.get("filename") or "").strip()

    try:
        profile_data = utils_depprofile.read_dep_profile(CFG["paths"]["dep_profiles_dir"], filename)
    except Exception as e:
        return jsonify({"ok": False, "message": f"讀取檔案失敗: {e}"}), 400

    base_url, api_key, dep_name, restart_cmd = get_nanodep_conn()
    if not base_url or not api_key or not dep_name:
        return jsonify({"ok": False, "message": ".env 內缺少 NANODEP_BASE_URL / NANODEP_API_KEY / NANODEP_NAME"}), 500

    def group_serials_lookup(group_name):
        devices = utils.read_devices_csv(CFG["paths"]["devices_csv"])
        return [sn for sn, info in devices.items() if info.get("group") == group_name]

    # 從 groups.json 反查目前這份檔案配對的群組(單一真相來源,不再各自存一份target_group避免失步)
    if filename == utils_depprofile.DEFAULT_ENROLL_FILENAME:
        target_group = None
    else:
        groups = utils.load_groups(CFG["paths"]["groups_json"])
        target_group = utils.find_group_by_paired_file(groups, "enroll_json", filename)

    try:
        result = utils_depprofile.apply_dep_profile(
            base_url, api_key, dep_name,
            profile_data["apple_profile"],
            target_group,
            group_serials_lookup,
            restart_cmd,
            utils.run_shell_command,
        )
    except utils_depprofile.ApplyError as e:
        log_activity_entry(f"套用註冊檔-{filename}", False, detail=str(e))
        return jsonify({"ok": False, "message": str(e)}), 500
    except Exception as e:
        log_activity_entry(f"套用註冊檔-{filename}", False, detail=f"未預期錯誤: {e}")
        return jsonify({"ok": False, "message": f"套用時發生未預期錯誤: {e}"}), 500

    log_activity_entry(f"套用註冊檔-{filename}", True, detail=f"新profile_uuid={result['new_profile_uuid']}, 目標群組={target_group or '預設'}")

    # 套用成功,把新UUID跟時間記回這個範本檔案
    profile_data["last_applied_uuid"] = result["new_profile_uuid"]
    profile_data["last_applied_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        utils_depprofile.save_dep_profile(CFG["paths"]["dep_profiles_dir"], filename, profile_data)
    except Exception as e:
        result["save_warning"] = f"套用成功,但寫回本地紀錄時發生錯誤: {e}"

    return jsonify({"ok": True, "result": result, "last_applied_uuid": result["new_profile_uuid"]})


# ---------------------------------------------------------------------------
# 所有群組頁
# ---------------------------------------------------------------------------
@app.route("/groups")
@login_required
def groups_page():
    return render_template("groups.html", active="groups", command_defs=COMMAND_DEFS)


@app.route("/api/groups")
@login_required
def api_groups():
    groups = utils.load_groups(CFG["paths"]["groups_json"])
    devices_csv = utils.read_devices_csv(CFG["paths"]["devices_csv"])
    vpp_rows, vpp_mtime = utils.read_vpp_cache_csv(CFG["paths"]["vpp_cache_csv"])
    vpp_cache_missing = vpp_mtime is None

    # 計算每個群組的裝置數量
    device_count_by_group = {}
    for sn, info in devices_csv.items():
        g = info.get("group", "")
        if g:
            device_count_by_group[g] = device_count_by_group.get(g, 0) + 1

    rows = []
    for name, info in sorted(groups.items()):
        rows.append({
            "group_name": name,
            "description": info.get("description", ""),
            "device_count": device_count_by_group.get(name, 0),
            "app_count": len(info.get("apps", [])),
            "enroll_json": info.get("enroll_json"),
            "mobileconfig": info.get("mobileconfig"),
        })

    return jsonify({"ok": True, "rows": rows, "vpp_cache_missing": vpp_cache_missing})


@app.route("/api/groups/save", methods=["POST"])
@login_required
def api_groups_save():
    data = request.json or {}
    group_name = (data.get("group_name") or "").strip()
    old_group_name = (data.get("old_group_name") or "").strip()
    description = (data.get("description") or "").strip()
    is_new = bool(data.get("is_new"))
    enroll_json = (data.get("enroll_json") or "").strip() or None
    mobileconfig = (data.get("mobileconfig") or "").strip() or None

    if not utils.VALID_NAME_RE.match(group_name or ""):
        return jsonify({"ok": False, "message": "群組名稱不可包含逗號、雙引號或控制字元,且長度需在 1~64 字元內"}), 400

    groups = utils.load_groups(CFG["paths"]["groups_json"])

    if is_new:
        if group_name in groups:
            return jsonify({"ok": False, "message": f"群組「{group_name}」已經存在"}), 400
        if not enroll_json or not mobileconfig:
            return jsonify({"ok": False, "message": "新增群組時必須選擇這個群組要使用的註冊檔(enroll json)與描述檔(mobileconfig),如果沒有可選的項目,請先到「ADE註冊設定」或「群組描述檔」頁面新增。"}), 400

        groups[group_name] = {"description": description, "apps": [], "enroll_json": None, "mobileconfig": None}
        utils.save_groups(CFG["paths"]["groups_json"], groups)

        try:
            utils_depprofile.assign_dep_profile_to_group(CFG["paths"]["groups_json"], enroll_json, group_name)
            utils_profiles.assign_mobileconfig_to_group(CFG["paths"]["groups_json"], mobileconfig, group_name)
        except ValueError as e:
            log_activity_entry("群組-新增", False, detail=f"{e}", group=group_name)
            return jsonify({"ok": False, "message": f"群組已建立,但配對檔案時發生錯誤: {e}"}), 400

        log_activity_entry("群組-新增", True, detail=f"註冊檔={enroll_json}, 描述檔={mobileconfig}", group=group_name)
        return jsonify({"ok": True, "message": f"已建立群組 {group_name}"})

    is_rename = bool(old_group_name) and old_group_name != group_name
    if is_rename and group_name in groups:
        return jsonify({"ok": False, "message": f"群組名稱「{group_name}」已經存在"}), 400
    if is_rename and old_group_name not in groups:
        return jsonify({"ok": False, "message": f"找不到原本的群組「{old_group_name}」"}), 404

    if is_rename:
        existing = groups.pop(old_group_name)
        existing["description"] = description
        groups[group_name] = existing
        # 同步更新 devices.csv 裡所有原本屬於這個群組的裝置
        devices = utils.read_devices_csv(CFG["paths"]["devices_csv"])
        changed = False
        for sn, info in devices.items():
            if info.get("group") == old_group_name:
                info["group"] = group_name
                changed = True
        if changed:
            utils.write_devices_csv(CFG["paths"]["devices_csv"], devices)
    else:
        existing = groups.get(group_name, {"apps": [], "enroll_json": None, "mobileconfig": None})
        existing["description"] = description
        groups[group_name] = existing

    utils.save_groups(CFG["paths"]["groups_json"], groups)
    message = f"已將「{old_group_name}」改名為「{group_name}」並同步更新裝置" if is_rename else f"已儲存群組 {group_name}"
    log_activity_entry("群組-修改", True, detail=message, group=group_name)
    return jsonify({"ok": True, "message": message})


@app.route("/api/groups/available-files")
@login_required
def api_groups_available_files():
    """給「新增群組」用:列出目前還沒被任何群組佔用的enroll json與mobileconfig(排除系統保護檔案)"""
    dep_files = utils_depprofile.list_dep_profiles(CFG["paths"]["dep_profiles_dir"], CFG["paths"]["groups_json"])
    available_enroll = [f["filename"] for f in dep_files if not f["is_protected"] and not f.get("assigned_group")]

    mc_files = utils_profiles.list_mobileconfig_files(CFG["paths"]["mobileconfig_dir"], CFG["paths"]["groups_json"])
    available_mc = [f["filename"] for f in mc_files if not f["is_protected"] and not f.get("assigned_group")]

    return jsonify({"ok": True, "available_enroll_json": available_enroll, "available_mobileconfig": available_mc})


@app.route("/api/groups/duplicate", methods=["POST"])
@login_required
def api_groups_duplicate():
    data = request.json or {}
    source_group = (data.get("source_group") or "").strip()
    new_group_name = (data.get("new_group_name") or "").strip()
    new_description = (data.get("new_description") or "").strip()

    if not utils.VALID_NAME_RE.match(new_group_name or ""):
        return jsonify({"ok": False, "message": "群組名稱不可包含逗號、雙引號或控制字元,且長度需在 1~64 字元內"}), 400

    groups = utils.load_groups(CFG["paths"]["groups_json"])
    if source_group not in groups:
        return jsonify({"ok": False, "message": f"找不到來源群組 {source_group}"}), 404
    if new_group_name in groups:
        return jsonify({"ok": False, "message": f"群組「{new_group_name}」已經存在"}), 400

    source_info = groups[source_group]

    new_enroll_json = None
    if source_info.get("enroll_json"):
        new_enroll_json = f"{new_group_name}-enroll.json"
        try:
            utils_depprofile.duplicate_dep_profile(CFG["paths"]["dep_profiles_dir"], source_info["enroll_json"], new_enroll_json)
        except Exception as e:
            return jsonify({"ok": False, "message": f"複製註冊檔失敗: {e}"}), 500

    new_mobileconfig = None
    if source_info.get("mobileconfig"):
        new_mobileconfig = f"{new_group_name}.mobileconfig"
        try:
            utils_profiles.duplicate_mobileconfig(
                CFG["paths"]["mobileconfig_dir"], source_info["mobileconfig"], new_mobileconfig,
                **get_profile_signing_kwargs()
            )
        except Exception as e:
            return jsonify({"ok": False, "message": f"複製描述檔失敗: {e}"}), 500

    groups[new_group_name] = {
        "description": new_description or source_info.get("description", ""),
        "apps": list(source_info.get("apps", [])),
        "enroll_json": None,
        "mobileconfig": None,
    }
    utils.save_groups(CFG["paths"]["groups_json"], groups)

    if new_enroll_json:
        utils_depprofile.assign_dep_profile_to_group(CFG["paths"]["groups_json"], new_enroll_json, new_group_name)
    if new_mobileconfig:
        utils_profiles.assign_mobileconfig_to_group(CFG["paths"]["groups_json"], new_mobileconfig, new_group_name)

    log_activity_entry("群組-再製", True, detail=f"來源群組={source_group}, 新註冊檔={new_enroll_json}, 新描述檔={new_mobileconfig}", group=new_group_name)
    return jsonify({
        "ok": True, "message": f"已複製群組為 {new_group_name}",
        "new_enroll_json": new_enroll_json, "new_mobileconfig": new_mobileconfig,
    })


@app.route("/api/groups/delete", methods=["POST"])
@login_required
def api_groups_delete():
    data = request.json or {}
    group_name = (data.get("group_name") or "").strip()
    groups = utils.load_groups(CFG["paths"]["groups_json"])
    if group_name in groups:
        del groups[group_name]
        utils.save_groups(CFG["paths"]["groups_json"], groups)
        log_activity_entry("群組-刪除", True, group=group_name)
        return jsonify({"ok": True, "message": f"已刪除群組 {group_name}"})
    log_activity_entry("群組-刪除", False, detail="找不到這個群組", group=group_name)
    return jsonify({"ok": False, "message": "找不到這個群組"}), 404


@app.route("/api/groups/<group_name>/devices")
@login_required
def api_group_devices(group_name):
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    merged, rc, err = utils.query_and_merge_devices(CFG["mysql"], db_password, CFG["paths"]["devices_csv"])
    if rc != 0:
        return jsonify({"ok": False, "message": err, "rows": []}), 500

    # merged 只包含「已經完成MDM註冊」的裝置(跟enrollments表JOIN過),
    # 所以這裡的數量就是「已註冊」數量。要知道「未註冊」數量,要另外去devices.csv
    # 算這個群組總共指派了多少裝置,兩者相減。
    registered = [r for r in merged if r.get("group") == group_name]

    all_devices_csv = utils.read_devices_csv(CFG["paths"]["devices_csv"])
    total_count = sum(1 for info in all_devices_csv.values() if info.get("group") == group_name)
    registered_count = len(registered)
    unregistered_count = max(0, total_count - registered_count)

    registered = enrich_rows_with_wifi_mac(registered)
    status_cache, status_mtime = utils.read_devices_status_cache(CFG["devices_status_cache"]["csv_path"])

    rows = []
    for i, row in enumerate(registered, start=1):
        status = status_cache.get(row["serial_number"], {})
        rows.append({
            "seq": i, **row,
            "battery_level": status.get("battery_level", ""),
            "device_capacity": status.get("device_capacity", ""),
            "available_device_capacity": status.get("available_device_capacity", ""),
            "os_version": status.get("os_version", ""),
            "available_os_version": status.get("available_os_version", ""),
            "available_os_product_key": status.get("available_os_product_key", ""),
            "os_update_is_downloaded": status.get("os_update_is_downloaded", ""),
            "os_update_status": status.get("os_update_status", ""),
            "ip_address": status.get("ip_address", ""),
            "lost_mode_enabled": status.get("lost_mode_enabled", ""),
            "location_lat": status.get("location_lat", ""),
            "location_lng": status.get("location_lng", ""),
            "location_at": status.get("location_at", ""),
            "location_accuracy": status.get("location_accuracy", ""),
        })

    return jsonify({
        "ok": True, "rows": rows,
        "registered_count": registered_count,
        "unregistered_count": unregistered_count,
        "total_count": total_count,
        "status_last_sync": _format_last_sync(status_mtime) if status_mtime else None,
    })


@app.route("/api/groups/<group_name>/apps")
@login_required
def api_group_apps(group_name):
    groups = utils.load_groups(CFG["paths"]["groups_json"])
    group_info = groups.get(group_name)
    if group_info is None:
        return jsonify({"ok": False, "message": "找不到這個群組"}), 404

    vpp_rows, vpp_mtime = utils.read_vpp_cache_csv(CFG["paths"]["vpp_cache_csv"])
    vpp_by_adam = {r["Adam ID"]: r for r in vpp_rows}

    assigned_adam_ids = group_info.get("apps", [])
    assigned_apps = []
    for adam_id in assigned_adam_ids:
        info = vpp_by_adam.get(adam_id)
        if info:
            assigned_apps.append(info)
        else:
            assigned_apps.append({
                "Adam ID": adam_id, "Bundle ID": "(不在快取中)",
                "軟體名稱": "(找不到資料,可能已從 VPP 移除)", "總數量": "-", "剩餘量": "-",
            })

    available_to_add = [r for r in vpp_rows if r["Adam ID"] not in assigned_adam_ids]

    return jsonify({
        "ok": True,
        "assigned_apps": assigned_apps,
        "available_to_add": available_to_add,
        "vpp_cache_missing": vpp_mtime is None,
    })


@app.route("/api/groups/<group_name>/apps/add", methods=["POST"])
@login_required
def api_group_apps_add(group_name):
    data = request.json or {}
    adam_id = (data.get("adam_id") or "").strip()
    if not adam_id:
        return jsonify({"ok": False, "message": "缺少 adam_id"}), 400

    groups = utils.load_groups(CFG["paths"]["groups_json"])
    if group_name not in groups:
        return jsonify({"ok": False, "message": "找不到這個群組"}), 404

    apps = groups[group_name].setdefault("apps", [])
    if adam_id not in apps:
        apps.append(adam_id)
        utils.save_groups(CFG["paths"]["groups_json"], groups)
    return jsonify({"ok": True, "message": f"已加入 {adam_id}"})


@app.route("/api/groups/<group_name>/apps/remove", methods=["POST"])
@login_required
def api_group_apps_remove(group_name):
    data = request.json or {}
    adam_id = (data.get("adam_id") or "").strip()

    groups = utils.load_groups(CFG["paths"]["groups_json"])
    if group_name not in groups:
        return jsonify({"ok": False, "message": "找不到這個群組"}), 404

    apps = groups[group_name].setdefault("apps", [])
    if adam_id in apps:
        apps.remove(adam_id)
        utils.save_groups(CFG["paths"]["groups_json"], groups)
    return jsonify({"ok": True, "message": f"已移除 {adam_id}"})


@app.route("/api/groups/<group_name>/command", methods=["POST"])
@login_required
def api_group_command(group_name):
    data = request.json or {}
    request_type = data.get("request_type")
    params = data.get("params") or {}

    if not request_type:
        return jsonify({"ok": False, "message": "缺少必要參數"}), 400
    if request_type not in COMMAND_DEFS:
        return jsonify({"ok": False, "message": f"不支援的指令類型: {request_type}"}), 400

    is_composite = request_type in OS_UPDATE_COMPOSITE_COMMANDS
    if not is_composite:
        try:
            request_type_actual, plist_params = build_mdm_command_params(request_type, params)
        except ValueError as e:
            return jsonify({"ok": False, "message": str(e)}), 400

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    merged, rc, err = utils.query_and_merge_devices(CFG["mysql"], db_password, CFG["paths"]["devices_csv"])
    if rc != 0:
        return jsonify({"ok": False, "message": err}), 500

    target_devices = [r for r in merged if r.get("group") == group_name]
    if not target_devices:
        return jsonify({"ok": False, "message": "這個群組目前沒有任何裝置"}), 400

    base_url, api_user, api_key = get_nanomdm_conn()
    if not base_url or not api_key:
        return jsonify({"ok": False, "message": ".env 內缺少 NANOMDM_BASE_URL 或 NANOMDM_API_KEY"}), 500

    results = []
    for dev in target_devices:
        try:
            # 查詢/下載/安裝更新是複合指令,每台裝置的可用更新版本可能不同,
            # 要逐台各自查詢/代查ProductKey,不能沿用同一組plist_params
            if is_composite:
                ok, result = dispatch_os_update_command(request_type, base_url, api_user, api_key, dev["enrollment_id"])
                log_activity_entry(request_type, ok, detail=f"群組={group_name}, 序號={dev['serial_number']}")
                results.append({
                    "serial_number": dev["serial_number"], "device_name": dev["device_name"],
                    "ok": ok, "message": None if ok else str(result),
                })
                continue

            if request_type == "InstallApplication":
                vpp_result = utils.assign_vpp_license(
                    CFG["cert_status"]["vpp_token_path"], dev["serial_number"], plist_params["iTunesStoreID"]
                )
                if vpp_result.get("status") not in (0, "0", None):
                    log_activity_entry(request_type, False, detail=f"群組={group_name}, 序號={dev['serial_number']}, VPP授權指派失敗: {vpp_result}")
                    results.append({
                        "serial_number": dev["serial_number"], "device_name": dev["device_name"],
                        "ok": False, "message": f"VPP授權指派失敗,未送出安裝指令: {vpp_result}",
                    })
                    continue

            # ClearPasscode一定要帶UnlockToken,每台裝置各自不同,要逐台代查
            # (用copy避免同一份plist_params字典在多次迴圈之間互相殘留上一台裝置的token)
            device_plist_params = plist_params
            if request_type == "ClearPasscode":
                unlock_token, token_err = utils.get_unlock_token(CFG["mysql"], db_password, dev["enrollment_id"])
                if token_err or not unlock_token:
                    reason = token_err or "這台裝置沒有記錄到UnlockToken"
                    log_activity_entry(request_type, False, detail=f"群組={group_name}, 序號={dev['serial_number']}, {reason}")
                    results.append({
                        "serial_number": dev["serial_number"], "device_name": dev["device_name"],
                        "ok": False, "message": reason,
                    })
                    continue
                device_plist_params = dict(plist_params)
                device_plist_params["UnlockToken"] = unlock_token

            status_code, result = utils.send_mdm_command(
                base_url, api_user, api_key, dev["enrollment_id"], request_type_actual, device_plist_params
            )
            ok = status_code < 400
            log_activity_entry(request_type, ok, detail=f"群組={group_name}, 序號={dev['serial_number']}")

            if ok and request_type in ("EnableLostMode", "DisableLostMode"):
                try:
                    utils.set_lost_mode_state(
                        CFG["devices_status_cache"]["csv_path"], dev["serial_number"],
                        enabled=(request_type == "EnableLostMode"),
                    )
                except Exception:
                    pass

            results.append({
                "serial_number": dev["serial_number"],
                "device_name": dev["device_name"],
                "ok": ok,
                "status_code": status_code,
            })
        except Exception as e:
            log_activity_entry(request_type, False, detail=f"群組={group_name}, 序號={dev['serial_number']}, error={e}")
            results.append({
                "serial_number": dev["serial_number"],
                "device_name": dev["device_name"],
                "ok": False,
                "message": str(e),
            })

    success_count = sum(1 for r in results if r["ok"])
    return jsonify({
        "ok": True,
        "results": results,
        "success_count": success_count,
        "total": len(results),
    })


# ---------------------------------------------------------------------------
# ASM 軟體資訊 - 背景排程 (預設每 5 分鐘同步一次 vpp_license.csv 快取,可在系統參數頁面調整)
# ---------------------------------------------------------------------------
_vpp_cache_lock = threading.Lock()


def refresh_vpp_cache_once():
    """實際執行 check_vpp_license.sh 並把結果寫入快取 CSV,回傳 (ok, message, row_count)"""
    script = CFG["paths"]["check_vpp_license_script"]
    cache_path = CFG["paths"]["vpp_cache_csv"]
    cwd = os.path.dirname(script) or None
    env = utils.build_subprocess_env(CFG["paths"]["env_file"])
    rc, out, err = utils.run_cmd([script], timeout=300, env=env, cwd=cwd)
    if rc != 0:
        return False, (err or out or "未知錯誤"), 0
    rows = utils.parse_vpp_table_output(out)
    with _vpp_cache_lock:
        utils.write_vpp_cache_csv(cache_path, rows)
    return True, None, len(rows)


def _vpp_scheduler_loop():
    while True:
        try:
            ok, msg, count = refresh_vpp_cache_once()
            if ok:
                print(f"[VPP排程] 同步完成,共 {count} 筆")
            else:
                print(f"[VPP排程] 同步失敗: {msg}")
            log_system_activity_entry("ASM軟體資訊-自動同步", ok, detail=msg if not ok else f"共 {count} 筆")
        except Exception as e:
            print(f"[VPP排程] 發生例外: {e}")
            log_system_activity_entry("ASM軟體資訊-自動同步", False, detail=str(e))
        time.sleep(CFG["vpp_cache"]["refresh_interval_seconds"])


def start_vpp_scheduler():
    t = threading.Thread(target=_vpp_scheduler_loop, daemon=True)
    t.start()


def _format_last_sync(mtime):
    if not mtime:
        return None
    return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")


@app.route("/asm")
@login_required
def asm_page():
    return render_template(
        "asm.html", active="asm",
        refresh_interval_minutes=CFG["vpp_cache"]["refresh_interval_seconds"] // 60,
    )


@app.route("/api/asm/cache")
@login_required
def api_asm_cache():
    rows, mtime = utils.read_vpp_cache_csv(CFG["paths"]["vpp_cache_csv"])
    return jsonify({"ok": True, "rows": rows, "last_sync": _format_last_sync(mtime)})


@app.route("/api/asm/download")
@login_required
def api_asm_download():
    cache_path = CFG["paths"]["vpp_cache_csv"]
    if not os.path.exists(cache_path):
        return jsonify({"ok": False, "message": "尚無快取檔案,請先執行一次查詢"}), 404
    return send_file(cache_path, as_attachment=True, download_name="vpp_license.csv")


@app.route("/api/asm/stream")
@login_required
def api_asm_stream():
    """手動查詢:逐行即時顯示進度,查詢完成後順便寫入快取 CSV"""
    script = CFG["paths"]["check_vpp_license_script"]

    def generate():
        collected_lines = []
        try:
            for line in utils.stream_check_vpp_license(script, env_file_path=CFG["paths"]["env_file"]):
                collected_lines.append(line)
                yield f"data: {json.dumps({'line': line})}\n\n"

            full_output = "\n".join(collected_lines)
            rows = utils.parse_vpp_table_output(full_output)
            if rows:
                with _vpp_cache_lock:
                    utils.write_vpp_cache_csv(CFG["paths"]["vpp_cache_csv"], rows)
                mtime = os.path.getmtime(CFG["paths"]["vpp_cache_csv"])
                log_activity_entry("ASM軟體資訊-手動同步", True, detail=f"共 {len(rows)} 筆")
                yield f"data: {json.dumps({'done': True, 'cached': True, 'count': len(rows), 'last_sync': _format_last_sync(mtime)})}\n\n"
            else:
                log_activity_entry("ASM軟體資訊-手動同步", False, detail="未解析到任何資料,快取未更新")
                yield f"data: {json.dumps({'done': True, 'cached': False, 'message': '未解析到任何資料,快取未更新'})}\n\n"
        except Exception as e:
            log_activity_entry("ASM軟體資訊-手動同步", False, detail=str(e))
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# 裝置即時狀態快取 (電量/容量/系統版本/可更新版本)
# ---------------------------------------------------------------------------
def sync_single_device_status(serial, enrollment_id, wait_for_response_seconds=5):
    """對單一裝置強制重新查詢狀態,只更新 devices-status.csv 裡這一台裝置的那一列,
    其他裝置的資料完全不受影響。給裝置列表裡逐列的「同步」按鈕用。

    重要:不能直接把 build_devices_status_rows() 的結果整批寫入覆蓋整份CSV——
    那個函式的 existing_cache 只會保留 lost_mode_enabled/定位這幾個欄位,其他裝置
    (不在這次查詢範圍內的)會因此掉失電量/系統版本等資料。這裡改成:先讀出完整的
    既有CSV,只替換這一台裝置對應的那一列,其他裝置原封不動地寫回去。

    回傳 (ok, message)。
    """
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")

    existing_cache, _ = utils.read_devices_status_cache(CFG["devices_status_cache"]["csv_path"])
    old_row = existing_cache.get(serial, {})

    base_url, api_user, api_key = get_nanomdm_conn()
    if not (base_url and api_key):
        return False, "找不到 nanomdm 連線設定"

    _, di_params = build_mdm_command_params("DeviceInformation", {})
    _, aou_params = build_mdm_command_params("AvailableOSUpdates", {})

    try:
        utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, "DeviceInformation", di_params)
        utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, "AvailableOSUpdates", aou_params)
        utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, "OSUpdateStatus", {})
        # 只對本地記錄「目前已啟用遺失模式」的裝置額外查詢定位,邏輯跟整批排程一致
        if old_row.get("lost_mode_enabled") == "true":
            utils.send_mdm_command(base_url, api_user, api_key, enrollment_id, "DeviceLocation", {})
    except Exception as e:
        return False, f"送出查詢指令失敗: {e}"

    if wait_for_response_seconds > 0:
        time.sleep(wait_for_response_seconds)

    status_rows, rc, err = utils.query_all_devices_latest_status(CFG["mysql"], db_password, enrollment_id=enrollment_id)
    if rc != 0:
        return False, f"查詢最新狀態失敗: {err}"

    updated_rows = utils.build_devices_status_rows(
        status_rows, {enrollment_id: serial}, existing_cache={serial: old_row},
    )
    updated_row = next((r for r in updated_rows if r["serial_number"] == serial), None)
    if updated_row is None:
        return False, "查無這台裝置的最新狀態資料(裝置可能從未回應過任何查詢)"

    # 從nanomdm的docker log解析這台裝置最後連線的來源IP(Apple MDM協定本身不提供這項
    # 資訊,是從nanomdm服務自己的HTTP request log裡,用trace_id把x_forwarded_for的IP
    # 兜出來的)。build_devices_status_rows()的existing_cache保留邏輯不包含ip_address
    # 這個欄位,所以這裡沒查到新的IP時,要自己明確保留舊值,不然會被清空成空字串。
    try:
        container_name = CFG["nanomdm_docker"]["container_name"]
        tail_lines = CFG["nanomdm_docker"]["log_tail_lines"]
        log_rc, log_text, log_err = utils.run_docker_logs(container_name, tail=tail_lines)
        if log_rc == 0:
            ip_by_enrollment_id = utils.extract_device_ips_from_nanomdm_logs(log_text)
            if enrollment_id in ip_by_enrollment_id:
                updated_row["ip_address"] = ip_by_enrollment_id[enrollment_id]["ip"]
            else:
                # 這次的log範圍內沒找到這台裝置的連線紀錄,保留舊值,不要清空
                updated_row["ip_address"] = old_row.get("ip_address", "")
        else:
            updated_row["ip_address"] = old_row.get("ip_address", "")
    except Exception:
        # IP解析失敗不該讓整個同步失敗,保留舊值即可
        updated_row["ip_address"] = old_row.get("ip_address", "")

    # 只替換這一台裝置在既有快取裡的那一列,其他裝置完全不動
    existing_cache[serial] = updated_row
    utils.write_devices_status_cache(CFG["devices_status_cache"]["csv_path"], list(existing_cache.values()))

    return True, "同步完成"


def _refresh_devices_status_cache_once_gen(wait_for_response_seconds=3, force=False):
    """重建 devices-status.csv 的產生器版本,派送查詢指令的過程中會逐步yield進度更新,
    方便SSE串流即時顯示派送進度給使用者看(例如「已派送 15/96 台」)。
    背景排程不需要即時進度,用下面的refresh_devices_status_cache_once()這個薄包裝,
    直接把這個產生器整個跑完、只取最後一筆「最終結果」即可,呼叫方式完全不變。

    每次yield的內容是一個dict:
      進度更新: {"progress": True, "current": N, "total": M}
      最終結果: {"final": True, "ok": bool, "msg": str或None, "count": int}
    """
    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")

    step_started = datetime.datetime.now()
    devices, rc, _, err = utils.query_devices_from_mysql(CFG["mysql"], db_password)
    print(f"[裝置狀態排程]   查詢裝置清單完成,耗時 {(datetime.datetime.now()-step_started).total_seconds():.1f} 秒,共 {len(devices) if devices else 0} 台")
    if rc != 0:
        yield {"final": True, "ok": False, "msg": err, "count": 0}
        return
    serial_by_enrollment_id = {d["enrollment_id"]: d["serial_number"] for d in devices}

    # 先送出查詢請求(用「目前已知的」快取判斷要不要額外查定位,不用等新一輪的rows)
    existing_cache_before, _ = utils.read_devices_status_cache(CFG["devices_status_cache"]["csv_path"])
    base_url, api_user, api_key = get_nanomdm_conn()
    if base_url and api_key:
        _, di_params = build_mdm_command_params("DeviceInformation", {})
        _, aou_params = build_mdm_command_params("AvailableOSUpdates", {})

        if force:
            # 強制模式:完全跳過pending查詢,對所有裝置一律送出,不受門檻限制
            pending_queries = {}
            print("[裝置狀態排程]   強制模式,跳過pending檢查")
        else:
            # 查一次目前所有裝置有哪些(裝置,指令類型)組合還卡在active=1佇列裡沒被處理完,
            # 以及每筆pending紀錄是什麼時候建立的。
            # 派送每一種查詢指令前,先確認該裝置的這個類型是不是「最近才送出、還在等回應」,
            # 是的話跳過這次派送,不重複疊加——這是為了修正實際發生過的問題:對長時間離線的
            # 裝置每10分鐘無條件重新派送同類型查詢指令,導致enrollment_queue/commands資料表
            # 無限累積。但如果pending的那筆已經卡了超過pending_retry_threshold_minutes
            # 分鐘還沒解決,視為「這筆多半已經沒有意義了」,重新嘗試送一次新的——避免裝置一旦
            # 離線超過一次排程週期就被永久放棄追蹤,即使裝置後來已經恢復連線也不會再被查詢。
            step_started = datetime.datetime.now()
            pending_queries = utils.get_pending_status_query_types(CFG["mysql"], db_password)
            print(f"[裝置狀態排程]   查詢pending清單完成,耗時 {(datetime.datetime.now()-step_started).total_seconds():.1f} 秒,共 {len(pending_queries)} 筆")
        retry_threshold_minutes = CFG["devices_status_cache"].get("pending_retry_threshold_minutes", 180)

        def _should_send_query(eid, request_type):
            if force:
                return True  # 強制模式,一律送出
            key = (eid, request_type)
            if key not in pending_queries:
                return True  # 沒有pending紀錄,正常送出
            created_at_str = pending_queries[key]
            try:
                created_at = datetime.datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
                age_minutes = (datetime.datetime.now() - created_at).total_seconds() / 60
                return age_minutes > retry_threshold_minutes  # 卡太久了,視為過期,重新嘗試
            except (ValueError, TypeError):
                return False  # 時間格式解析失敗,保守起見維持原本「已經pending,不重送」的行為

        step_started = datetime.datetime.now()
        total = len(devices)
        for i, d in enumerate(devices):
            try:
                eid = d["enrollment_id"]
                if _should_send_query(eid, "DeviceInformation"):
                    utils.send_mdm_command(base_url, api_user, api_key, eid, "DeviceInformation", di_params)
                if _should_send_query(eid, "AvailableOSUpdates"):
                    utils.send_mdm_command(base_url, api_user, api_key, eid, "AvailableOSUpdates", aou_params)
                if _should_send_query(eid, "OSUpdateStatus"):
                    utils.send_mdm_command(base_url, api_user, api_key, eid, "OSUpdateStatus", {})
                # 只對本地記錄「目前已啟用遺失模式」的裝置額外查詢定位,
                # 沒開遺失模式的裝置送DeviceLocation一定會失敗,不用浪費請求
                old_row = existing_cache_before.get(d["serial_number"], {})
                if old_row.get("lost_mode_enabled") == "true" and _should_send_query(eid, "DeviceLocation"):
                    utils.send_mdm_command(base_url, api_user, api_key, eid, "DeviceLocation", {})
            except Exception:
                pass  # 個別裝置排入失敗不該影響整批,繼續往下一台
            yield {"progress": True, "current": i + 1, "total": total}
        print(f"[裝置狀態排程]   派送查詢指令完成,耗時 {(datetime.datetime.now()-step_started).total_seconds():.1f} 秒")

    # 稍等一下,讓在線上的裝置有機會立刻處理推播、回應查詢(非強制,只是盡量提高這次就抓到新資料的機會)
    if wait_for_response_seconds > 0:
        time.sleep(wait_for_response_seconds)

    step_started = datetime.datetime.now()
    status_rows, rc2, err2 = utils.query_all_devices_latest_status(CFG["mysql"], db_password)
    print(f"[裝置狀態排程]   查詢最新狀態完成,耗時 {(datetime.datetime.now()-step_started).total_seconds():.1f} 秒")
    if rc2 != 0:
        yield {"final": True, "ok": False, "msg": err2, "count": 0}
        return

    existing_cache, _ = utils.read_devices_status_cache(CFG["devices_status_cache"]["csv_path"])
    rows = utils.build_devices_status_rows(status_rows, serial_by_enrollment_id, existing_cache=existing_cache)

    # 從nanomdm的docker log解析每台裝置最後連線的來源IP(Apple MDM協定本身不提供這項資訊,
    # 這是從nanomdm服務自己的HTTP request log裡,用trace_id把x_forwarded_for的IP兜出來的)。
    # 這一步失敗不該影響電量/容量/系統版本這些原本就查得到的資料照常寫入。
    try:
        container_name = CFG["nanomdm_docker"]["container_name"]
        tail_lines = CFG["nanomdm_docker"]["log_tail_lines"]
        log_rc, log_text, log_err = utils.run_docker_logs(container_name, tail=tail_lines)
        if log_rc == 0:
            ip_by_enrollment_id = utils.extract_device_ips_from_nanomdm_logs(log_text)
            row_by_serial = {r["serial_number"]: r for r in rows}
            for enrollment_id, info in ip_by_enrollment_id.items():
                serial = serial_by_enrollment_id.get(enrollment_id)
                if not serial:
                    continue
                if serial not in row_by_serial:
                    row_by_serial[serial] = {"serial_number": serial}
                    rows.append(row_by_serial[serial])
                row_by_serial[serial]["ip_address"] = info["ip"]
    except Exception:
        pass  # IP位址是額外附加資訊,解析失敗不該讓整個狀態快取更新失敗

    utils.write_devices_status_cache(CFG["devices_status_cache"]["csv_path"], rows)

    yield {"final": True, "ok": True, "msg": None, "count": len(rows)}


def refresh_devices_status_cache_once(wait_for_response_seconds=3, force=False):
    """薄包裝:把上面的產生器版本整個跑完,只取最後一筆「最終結果」回傳,
    維持跟改寫前完全一樣的呼叫介面跟回傳格式(ok, msg, count)三元組,
    給背景排程等不需要即時進度的呼叫方使用,不用改動任何既有呼叫的地方。
    """
    result = {"ok": False, "msg": "沒有任何結果", "count": 0}
    for item in _refresh_devices_status_cache_once_gen(wait_for_response_seconds, force):
        if item.get("final"):
            result = item
    return result["ok"], result["msg"], result["count"]


def _devices_status_scheduler_loop():
    while True:
        started_at = datetime.datetime.now()
        try:
            print(f"[裝置狀態排程] 開始執行,時間: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
            ok, msg, count = refresh_devices_status_cache_once()
            elapsed = (datetime.datetime.now() - started_at).total_seconds()
            print(f"[裝置狀態排程] {'完成,共 ' + str(count) + ' 台裝置有資料' if ok else '失敗: ' + str(msg)}(耗時 {elapsed:.1f} 秒)")
        except Exception as e:
            elapsed = (datetime.datetime.now() - started_at).total_seconds()
            print(f"[裝置狀態排程] 發生例外: {e}(耗時 {elapsed:.1f} 秒)")
        time.sleep(CFG["devices_status_cache"]["refresh_interval_seconds"])


def start_devices_status_scheduler():
    t = threading.Thread(target=_devices_status_scheduler_loop, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# nanomdm 指令歷史清理(commands/command_results/enrollment_queue)
# ---------------------------------------------------------------------------
@app.route("/api/cleanup/settings", methods=["GET"])
@login_required
def api_cleanup_settings_get():
    return jsonify({"ok": True, "settings": CFG["nanomdm_cleanup"]})


@app.route("/api/cleanup/settings", methods=["POST"])
@login_required
def api_cleanup_settings_save():
    data = request.json or {}
    retention_days = data.get("retention_days")
    auto_enabled = data.get("auto_enabled")

    try:
        retention_days = int(retention_days)
        if retention_days < 1:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "保留天數必須是大於0的整數"}), 400

    CFG["nanomdm_cleanup"]["retention_days"] = retention_days
    CFG["nanomdm_cleanup"]["auto_enabled"] = bool(auto_enabled)
    config.save_config(CFG)

    log_activity_entry(
        "系統維護-清理設定變更", True,
        detail=f"保留天數={retention_days}, 自動排程={'開啟' if auto_enabled else '關閉'}",
    )
    return jsonify({"ok": True, "settings": CFG["nanomdm_cleanup"]})


@app.route("/api/cleanup/preview")
@login_required
def api_cleanup_preview():
    retention_days = request.args.get("retention_days", CFG["nanomdm_cleanup"]["retention_days"])
    try:
        retention_days = int(retention_days)
        if retention_days < 1:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "保留天數必須是大於0的整數"}), 400

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
    result = utils.preview_command_cleanup(CFG["mysql"], db_password, retention_days)
    if not result["ok"]:
        return jsonify({"ok": False, "message": f"預覽查詢失敗: {result.get('error')}"}), 500
    return jsonify(result)


@app.route("/api/cleanup/execute", methods=["POST"])
@login_required
def api_cleanup_execute():
    data = request.json or {}
    password = data.get("password", "")
    if not verify_current_user_password(password):
        log_activity_entry("系統維護-手動清理指令歷史", False, detail="密碼驗證失敗,操作已取消")
        return jsonify({"ok": False, "message": "密碼不正確,操作已取消"}), 403

    retention_days = data.get("retention_days", CFG["nanomdm_cleanup"]["retention_days"])
    try:
        retention_days = int(retention_days)
        if retention_days < 1:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "保留天數必須是大於0的整數"}), 400

    env = get_env_dict()
    db_password = env.get(CFG["mysql"]["db_password_env_key"], "")

    # 執行前先預覽一次,把「預期要刪的數量」記進操作紀錄,讓之後回頭查活動紀錄時
    # 能看到這次清理實際影響的範圍,不是只留下一句「執行成功」看不出規模
    preview = utils.preview_command_cleanup(CFG["mysql"], db_password, retention_days)
    expected_count = preview.get("commands_count", "未知") if preview.get("ok") else "未知"

    result = utils.execute_command_cleanup(CFG["mysql"], db_password, retention_days)
    ok = result.get("ok", False)
    log_activity_entry(
        "系統維護-手動清理指令歷史", ok,
        detail=f"保留天數={retention_days}, 預期刪除筆數={expected_count}" + (f", error={result.get('error')}" if not ok else ""),
    )

    if not ok:
        return jsonify({"ok": False, "message": f"清理失敗: {result.get('error')}"}), 500
    return jsonify({"ok": True, "message": f"清理完成,已刪除約 {expected_count} 筆指令紀錄(含關聯的回應與佇列紀錄)"})


def _nanomdm_cleanup_scheduler_loop():
    """定期檢查是否有開啟自動清理,開啟的話依照目前設定的保留天數執行清理。
    刻意每次都重新讀取CFG(不是啟動時的快照),讓使用者透過webui切換開關/保留天數後,
    不用重啟服務就能在下一次排程時間生效。
    """
    while True:
        try:
            if CFG["nanomdm_cleanup"].get("auto_enabled"):
                retention_days = CFG["nanomdm_cleanup"].get("retention_days", 60)
                env = get_env_dict()
                db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
                preview = utils.preview_command_cleanup(CFG["mysql"], db_password, retention_days)
                expected_count = preview.get("commands_count", "未知") if preview.get("ok") else "未知"
                result = utils.execute_command_cleanup(CFG["mysql"], db_password, retention_days)
                ok = result.get("ok", False)
                log_system_activity_entry(
                    "系統維護-自動清理指令歷史", ok,
                    detail=f"保留天數={retention_days}, 預期刪除筆數={expected_count}" + (f", error={result.get('error')}" if not ok else ""),
                )
        except Exception as e:
            log_system_activity_entry("系統維護-自動清理指令歷史", False, detail=str(e))
        time.sleep(CFG["nanomdm_cleanup"].get("check_interval_seconds", 86400))


def start_nanomdm_cleanup_scheduler():
    t = threading.Thread(target=_nanomdm_cleanup_scheduler_loop, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# 版本與更新
# ---------------------------------------------------------------------------
@app.route("/version")
@login_required
def version_page():
    return render_template("version.html", active="version")


def _get_github_token():
    """從.env讀取選填的GITHUB_TOKEN。有設定的話,GitHub API的請求速率限制
    會從未登入的60次/小時提升到5000次/小時(這個限制實測過真的會遇到,不是純理論)。
    """
    env = get_env_dict()
    return env.get("GITHUB_TOKEN", "") or None


@app.route("/api/version/current")
@login_required
def api_version_current():
    cfg = CFG["update"]
    current = utils_version.get_current_version(cfg["version_file"])
    return jsonify({"ok": True, "current_version": current})


@app.route("/api/version/set-current", methods=["POST"])
@login_required
def api_version_set_current():
    """手動設定目前版本。用在這個功能上線之前就已經安裝好的環境(找不到版本記錄檔),
    需要使用者自己確認一次目前實際是哪個版本,之後才能正確比對差異。
    """
    data = request.json or {}
    tag = (data.get("tag") or "").strip()
    if not tag:
        return jsonify({"ok": False, "message": "請提供版本標籤"}), 400
    utils_version.set_current_version(CFG["update"]["version_file"], tag)
    log_activity_entry("系統維護-手動設定目前版本", True, detail=f"設定為 {tag}")
    return jsonify({"ok": True, "current_version": tag})


@app.route("/api/version/tags")
@login_required
def api_version_tags():
    cfg = CFG["update"]
    token = _get_github_token()
    tags, err = utils_version.fetch_github_tags(cfg["github_owner"], cfg["github_repo"], github_token=token)
    if tags is None:
        return jsonify({"ok": False, "message": err}), 500

    releases_map = utils_version.fetch_github_releases_map(cfg["github_owner"], cfg["github_repo"], github_token=token)
    tags_sorted = sorted(tags, key=lambda t: utils_version.version_sort_key(t["name"]), reverse=True)
    for t in tags_sorted:
        release_info = releases_map.get(t["name"])
        t["release_notes"] = release_info["body"] if release_info else None
        t["published_at"] = release_info["published_at"] if release_info else None

    return jsonify({"ok": True, "tags": tags_sorted})


@app.route("/api/version/check-update")
@login_required
def api_version_check_update():
    cfg = CFG["update"]
    token = _get_github_token()
    tags, err = utils_version.fetch_github_tags(cfg["github_owner"], cfg["github_repo"], github_token=token)
    if tags is None:
        return jsonify({"ok": False, "message": err}), 500
    if not tags:
        return jsonify({"ok": False, "message": "GitHub 上目前沒有任何版本標籤"}), 404

    tags_sorted = sorted(tags, key=lambda t: utils_version.version_sort_key(t["name"]), reverse=True)
    latest = tags_sorted[0]["name"]

    current = utils_version.get_current_version(cfg["version_file"])

    releases_map = utils_version.fetch_github_releases_map(cfg["github_owner"], cfg["github_repo"], github_token=token)
    latest_notes = releases_map.get(latest, {}).get("body")

    return jsonify({
        "ok": True,
        "current_version": current,
        "latest_version": latest,
        "update_available": bool(current) and (current != latest),
        "release_notes": latest_notes,
    })


@app.route("/api/version/diff")
@login_required
def api_version_diff():
    target_tag = request.args.get("target_tag", "").strip()
    if not target_tag:
        return jsonify({"ok": False, "message": "缺少 target_tag"}), 400

    cfg = CFG["update"]
    current = utils_version.get_current_version(cfg["version_file"])
    if not current:
        return jsonify({"ok": False, "message": "目前版本未知,請先在頁面上手動設定目前版本"}), 400
    if current == target_tag:
        return jsonify({"ok": True, "current_version": current, "target_version": target_tag, "files": []})

    token = _get_github_token()
    files, err = utils_version.compare_versions(
        cfg["github_owner"], cfg["github_repo"], current, target_tag, cfg, github_token=token,
    )
    if files is None:
        return jsonify({"ok": False, "message": err}), 500

    return jsonify({"ok": True, "current_version": current, "target_version": target_tag, "files": files})


def _delayed_restart_self():
    """延遲重啟nanomdm-webui.service自己。故意在背景執行緒裡延遲幾秒才動手,
    確保這次更新請求的HTTP回應能先確實送到瀏覽器,不會因為服務被重啟中斷連線,
    讓使用者看不到「更新成功」的回應。
    """
    def _do_restart():
        time.sleep(2)
        subprocess.run(["systemctl", "restart", "nanomdm-webui.service"])
    t = threading.Thread(target=_do_restart, daemon=True)
    t.start()


@app.route("/api/version/apply", methods=["POST"])
@login_required
def api_version_apply():
    data = request.json or {}
    password = data.get("password", "")
    target_tag = (data.get("target_tag") or "").strip()

    if not verify_current_user_password(password):
        log_activity_entry("系統維護-版本更新", False, detail="密碼驗證失敗,操作已取消")
        return jsonify({"ok": False, "message": "密碼不正確,操作已取消"}), 403
    if not target_tag:
        return jsonify({"ok": False, "message": "缺少 target_tag"}), 400

    cfg = CFG["update"]
    current = utils_version.get_current_version(cfg["version_file"])
    if not current:
        return jsonify({"ok": False, "message": "目前版本未知,請先在頁面上手動設定目前版本"}), 400

    token = _get_github_token()
    files, err = utils_version.compare_versions(
        cfg["github_owner"], cfg["github_repo"], current, target_tag, cfg, github_token=token,
    )
    if files is None:
        return jsonify({"ok": False, "message": f"比對版本失敗: {err}"}), 500
    if not files:
        return jsonify({"ok": False, "message": "沒有任何符合條件的檔案需要更新,目前已經是這個版本的內容"}), 400

    backup_dir = "/opt/nanomdm-webui/.update_backups"
    results, overall_ok, this_backup_dir = utils_version.apply_version_update(
        cfg["github_owner"], cfg["github_repo"], target_tag, files, cfg, backup_dir, github_token=token,
    )

    failed_files = [r for r in results if not r["ok"]]
    if not failed_files:
        utils_version.set_current_version(cfg["version_file"], target_tag)

    changed_local_paths = [f["local_path"] for f in files]
    services_to_restart = utils_version.determine_services_to_restart(changed_local_paths, cfg)

    # nanomdm-webui.service重啟自己放到最後、用延遲執行緒處理,其他服務可以直接同步重啟
    restarted = []
    self_restart_needed = False
    for svc in services_to_restart:
        if svc == "nanomdm-webui.service":
            self_restart_needed = True
            continue
        restart_ok, restart_err = restart_service_and_log("systemd", svc, f"版本更新後重啟(更新到{target_tag})")
        restarted.append({"service": svc, "ok": restart_ok, "error": restart_err})

    log_activity_entry(
        "系統維護-版本更新", overall_ok and not failed_files,
        detail=f"從 {current} 更新到 {target_tag}, 檔案數={len(files)}, 失敗數={len(failed_files)}, "
               f"重啟服務={services_to_restart}, 備份位置={this_backup_dir}",
    )

    if self_restart_needed:
        _delayed_restart_self()

    return jsonify({
        "ok": overall_ok and not failed_files,
        "results": results,
        "services_restarted": services_to_restart,
        "self_restarting": self_restart_needed,
        "backup_dir": this_backup_dir,
        "message": (
            f"更新完成,已套用 {len(files) - len(failed_files)}/{len(files)} 個檔案。"
            + (f" webui 服務即將自動重啟,幾秒後請重新整理頁面。" if self_restart_needed else "")
        ),
    })


@app.route("/api/devices/status-cache")
@login_required
def api_devices_status_cache():
    cache, mtime = utils.read_devices_status_cache(CFG["devices_status_cache"]["csv_path"])
    return jsonify({"ok": True, "cache": cache, "last_sync": _format_last_sync(mtime) if mtime else None})


@app.route("/api/devices/sync-one", methods=["POST"])
@login_required
def api_devices_sync_one():
    data = request.json or {}
    serial = data.get("serial", "")
    enrollment_id = data.get("enrollment_id", "")
    if not serial or not enrollment_id:
        return jsonify({"ok": False, "message": "缺少序號或 enrollment_id"}), 400

    ok, message = sync_single_device_status(serial, enrollment_id, wait_for_response_seconds=5)
    if not ok:
        return jsonify({"ok": False, "message": message}), 500
    return jsonify({"ok": True, "message": message})


@app.route("/api/devices/status-sync-stream")
@login_required
def api_devices_status_sync_stream():
    def generate():
        yield f"data: {json.dumps({'message': '正在送出查詢請求...', 'done': False}, ensure_ascii=False)}\n\n"
        try:
            for item in _refresh_devices_status_cache_once_gen(wait_for_response_seconds=5, force=True):
                if item.get("progress"):
                    current, total = item["current"], item["total"]
                    yield f"data: {json.dumps({'message': f'已派送查詢指令: {current}/{total} 台', 'done': False}, ensure_ascii=False)}\n\n"
                elif item.get("final"):
                    if not item["ok"]:
                        yield f"data: {json.dumps({'error': item['msg'], 'done': True}, ensure_ascii=False)}\n\n"
                        return
                    count = item["count"]
                    mtime = os.path.getmtime(CFG["devices_status_cache"]["csv_path"])
                    yield f"data: {json.dumps({'done': True, 'count': count, 'last_sync': _format_last_sync(mtime), 'message': f'已更新快取(共 {count} 台裝置有資料)。已在線上、能立刻處理推播的裝置這次應該就有拿到最新資料;沒能及時回應的裝置,已經幫它排入新的查詢,請稍後再按一次查看'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e), 'done': True}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/devices/offboard-stream")
@login_required
def api_devices_offboard_stream():
    """裝置退場:
    1. 清除 Activation Lock bypass code
    2. 撤銷這台裝置目前所屬群組的 VPP App 授權(釋放授權額度)
    3. 在ASM解除指派(裝置留在ASM名冊,只是不再指派給任何MDM伺服器,可逆,不是「釋出」)
    4. 清除nanomdm這邊的enrollment註冊紀錄
    5. 清理這套webui自己的本地檔案(devices.csv / devices-status.csv / webhook-server.py的序號暫存)
    每個步驟各自獨立回報結果,單一步驟失敗不會擋住後面的步驟繼續執行。
    前兩步驟需要「還握有裝置管理權」才做得到,所以特意排在切斷ASM/nanomdm管理關係之前。
    """
    serial = request.args.get("serial")
    enrollment_id = request.args.get("enrollment_id") or None
    if not serial:
        return jsonify({"ok": False, "message": "缺少序號"}), 400

    def generate():
        overall_ok = True

        # 先查出這台裝置目前所屬的群組,用來決定要撤銷哪些App的VPP授權(用devices.csv記錄的群組
        # 當作「這台裝置實際裝過哪些App」的最佳依據,不是100%保證準確,但這是我們系統裡有掌握到
        # 最接近事實的資訊,避免漏撤銷或撤銷到不相關的App)
        devices_before = utils.read_devices_csv(CFG["paths"]["devices_csv"])
        current_group = devices_before.get(serial, {}).get("group", "")
        current_device_name = devices_before.get(serial, {}).get("device_name", "")

        # ---- 步驟1: 清除Activation Lock bypass code ----
        yield f"data: {json.dumps({'step': 1, 'step_name': '清除Activation Lock', 'status': 'running', 'done': False}, ensure_ascii=False)}\n\n"
        if not enrollment_id:
            yield f"data: {json.dumps({'step': 1, 'step_name': '清除Activation Lock', 'status': 'skipped', 'message': '這台裝置沒有enrollment_id,代表本來就沒有MDM註冊紀錄,略過此步驟', 'done': False}, ensure_ascii=False)}\n\n"
        else:
            try:
                base_url, api_user, api_key = get_nanomdm_conn()
                if not base_url or not api_key:
                    yield f"data: {json.dumps({'step': 1, 'step_name': '清除Activation Lock', 'status': 'skipped', 'message': '.env 內缺少 NANOMDM 設定,略過此步驟', 'done': False}, ensure_ascii=False)}\n\n"
                else:
                    status_code, cmd_result = utils.send_mdm_command(
                        base_url, api_user, api_key, enrollment_id, "ClearActivationLockBypassCode", {}
                    )
                    ok = status_code < 400
                    if not ok:
                        overall_ok = False
                    msg = "已送出清除指令(裝置連線後才會實際生效)" if ok else f"送出失敗: {cmd_result}"
                    yield f"data: {json.dumps({'step': 1, 'step_name': '清除Activation Lock', 'status': 'done' if ok else 'error', 'message': msg, 'done': False}, ensure_ascii=False)}\n\n"
            except Exception as e:
                overall_ok = False
                yield f"data: {json.dumps({'step': 1, 'step_name': '清除Activation Lock', 'status': 'error', 'message': str(e), 'done': False}, ensure_ascii=False)}\n\n"

        # ---- 步驟2: 撤銷VPP授權 ----
        yield f"data: {json.dumps({'step': 2, 'step_name': '撤銷VPP授權', 'status': 'running', 'done': False}, ensure_ascii=False)}\n\n"
        try:
            groups = utils.load_groups(CFG["paths"]["groups_json"])
            group_apps = (groups.get(current_group, {}) or {}).get("apps", []) if current_group else []
            if not group_apps:
                yield f"data: {json.dumps({'step': 2, 'step_name': '撤銷VPP授權', 'status': 'skipped', 'message': '這台裝置目前沒有分類到任何群組、或群組沒有綁定App,沒有需要撤銷的授權', 'done': False}, ensure_ascii=False)}\n\n"
            else:
                revoke_results = []
                for adam_id in group_apps:
                    try:
                        r = utils.revoke_vpp_license(CFG["cert_status"]["vpp_token_path"], serial, adam_id)
                        revoke_results.append(f"{adam_id}: {'成功' if r.get('status') in (0, '0', None) else r}")
                    except Exception as e:
                        revoke_results.append(f"{adam_id}: 失敗({e})")
                yield f"data: {json.dumps({'step': 2, 'step_name': '撤銷VPP授權', 'status': 'done', 'message': '; '.join(revoke_results), 'done': False}, ensure_ascii=False)}\n\n"
        except Exception as e:
            overall_ok = False
            yield f"data: {json.dumps({'step': 2, 'step_name': '撤銷VPP授權', 'status': 'error', 'message': str(e), 'done': False}, ensure_ascii=False)}\n\n"

        # ---- 步驟3: ASM解除指派 ----
        yield f"data: {json.dumps({'step': 3, 'step_name': 'ASM解除指派', 'status': 'running', 'done': False}, ensure_ascii=False)}\n\n"
        try:
            base_url, api_key, org_type, axm_name = get_nanoaxm_conn()
            if not base_url or not api_key:
                yield f"data: {json.dumps({'step': 3, 'step_name': 'ASM解除指派', 'status': 'skipped', 'message': '.env 內缺少 NANOAXM 設定,略過此步驟', 'done': False}, ensure_ascii=False)}\n\n"
            else:
                _, device_by_server, unassigned, _ = utils_asm.read_asm_devices_cache(
                    CFG["asm_devices_cache"]["servers_csv"], CFG["asm_devices_cache"]["devices_csv"]
                )
                asm_device_id = None
                current_server_id = None
                for server_id, rows in device_by_server.items():
                    for d in rows:
                        if d.get("serialNumber") == serial:
                            asm_device_id = d["id"]
                            current_server_id = server_id
                            break
                    if asm_device_id:
                        break

                already_unassigned = False
                if not asm_device_id:
                    for d in unassigned:
                        if d.get("serialNumber") == serial:
                            asm_device_id = d["id"]
                            already_unassigned = True
                            break

                if not asm_device_id:
                    yield f"data: {json.dumps({'step': 3, 'step_name': 'ASM解除指派', 'status': 'skipped', 'message': '在ASM快取裡找不到這台裝置(可能本來就沒有指派給任何MDM伺服器,或快取還沒同步到最新狀態),略過此步驟', 'done': False}, ensure_ascii=False)}\n\n"
                elif already_unassigned:
                    yield f"data: {json.dumps({'step': 3, 'step_name': 'ASM解除指派', 'status': 'skipped', 'message': '這台裝置目前已經沒有指派給任何MDM伺服器,不用再解除一次', 'done': False}, ensure_ascii=False)}\n\n"
                else:
                    activity = utils_asm.unassign_devices(base_url, api_key, org_type, axm_name, current_server_id, [asm_device_id])
                    activity_id = activity.get("data", {}).get("id")
                    if not activity_id:
                        overall_ok = False
                        yield f"data: {json.dumps({'step': 3, 'step_name': 'ASM解除指派', 'status': 'error', 'message': f'建立解除指派作業失敗: {activity}', 'done': False}, ensure_ascii=False)}\n\n"
                    else:
                        # poll_activity_until_done是generator,要用for迴圈逐次拿進度,不是直接呼叫拿回傳值
                        last_update = None
                        for update in utils_asm.poll_activity_until_done(base_url, api_key, org_type, axm_name, activity_id):
                            last_update = update
                        final_status_text = (last_update or {}).get("status", "未知")
                        yield f"data: {json.dumps({'step': 3, 'step_name': 'ASM解除指派', 'status': 'done', 'message': f'解除指派作業已完成,狀態: {final_status_text}', 'done': False}, ensure_ascii=False)}\n\n"
        except Exception as e:
            overall_ok = False
            yield f"data: {json.dumps({'step': 3, 'step_name': 'ASM解除指派', 'status': 'error', 'message': str(e), 'done': False}, ensure_ascii=False)}\n\n"

        # ---- 步驟2: 清除nanomdm的enrollment註冊紀錄 ----
        yield f"data: {json.dumps({'step': 4, 'step_name': 'nanomdm註冊紀錄清除', 'status': 'running', 'done': False}, ensure_ascii=False)}\n\n"
        if not enrollment_id:
            yield f"data: {json.dumps({'step': 4, 'step_name': 'nanomdm註冊紀錄清除', 'status': 'skipped', 'message': '這台裝置沒有enrollment_id,代表本來就沒有MDM註冊紀錄,略過此步驟', 'done': False}, ensure_ascii=False)}\n\n"
        else:
            try:
                env = get_env_dict()
                db_password = env.get(CFG["mysql"]["db_password_env_key"], "")
                ok, msg = utils.delete_nanomdm_enrollment(CFG["mysql"], db_password, enrollment_id)
                if ok:
                    yield f"data: {json.dumps({'step': 4, 'step_name': 'nanomdm註冊紀錄清除', 'status': 'done', 'message': '已刪除enrollment紀錄', 'done': False}, ensure_ascii=False)}\n\n"
                else:
                    overall_ok = False
                    yield f"data: {json.dumps({'step': 4, 'step_name': 'nanomdm註冊紀錄清除', 'status': 'error', 'message': msg, 'done': False}, ensure_ascii=False)}\n\n"
            except Exception as e:
                overall_ok = False
                yield f"data: {json.dumps({'step': 4, 'step_name': 'nanomdm註冊紀錄清除', 'status': 'error', 'message': str(e), 'done': False}, ensure_ascii=False)}\n\n"

        # ---- 步驟3: 清理webui本地檔案 ----
        yield f"data: {json.dumps({'step': 5, 'step_name': '清理本地檔案', 'status': 'running', 'done': False}, ensure_ascii=False)}\n\n"
        try:
            utils.delete_device_row(CFG["paths"]["devices_csv"], serial)
            utils.delete_devices_status_row(CFG["devices_status_cache"]["csv_path"], serial)
            if enrollment_id:
                utils.remove_from_udid_serial_cache(
                    CFG["paths"]["udid_serial_cache"], CFG["paths"]["udid_serial_cache_lock"], enrollment_id
                )
            yield f"data: {json.dumps({'step': 5, 'step_name': '清理本地檔案', 'status': 'done', 'message': '已移除 devices.csv / devices-status.csv 裡的紀錄(以及webhook序號暫存,如果有的話)', 'done': False}, ensure_ascii=False)}\n\n"
        except Exception as e:
            overall_ok = False
            yield f"data: {json.dumps({'step': 5, 'step_name': '清理本地檔案', 'status': 'error', 'message': str(e), 'done': False}, ensure_ascii=False)}\n\n"

        log_activity_entry("裝置退場", overall_ok, detail=f"enrollment_id={enrollment_id}", serial=serial, device_name=current_device_name, group=current_group)
        yield f"data: {json.dumps({'done': True, 'overall_ok': overall_ok}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# 系統狀態監控
# ---------------------------------------------------------------------------
@app.route("/system-status")
@login_required
def system_status_page():
    return render_template("system_status.html", active="system_status")


@app.route("/api/sysstatus/system")
@login_required
def api_sysstatus_system():
    try:
        return jsonify({"ok": True, "data": utils_sysstatus.get_system_status()})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route("/api/sysstatus/docker")
@login_required
def api_sysstatus_docker():
    containers, err = utils_sysstatus.list_docker_containers()
    if err:
        return jsonify({"ok": False, "message": err}), 500
    # 只顯示設定檔裡列出的容器,避免這台主機上其他不相關的容器也被列出來
    monitored = set(CFG["sysstatus"]["docker_containers"])
    filtered = [c for c in containers if c["name"] in monitored]
    return jsonify({"ok": True, "data": filtered})


@app.route("/api/sysstatus/docker/logs/<container_name>")
@login_required
def api_sysstatus_docker_logs(container_name):
    if container_name not in CFG["sysstatus"]["docker_containers"]:
        return jsonify({"ok": False, "message": "不在允許清單裡的容器名稱"}), 400
    logs, err = utils_sysstatus.get_docker_logs(container_name, tail=300)
    if err:
        return jsonify({"ok": False, "message": err}), 500
    return jsonify({"ok": True, "logs": logs})


@app.route("/api/sysstatus/docker/restart", methods=["POST"])
@login_required
def api_sysstatus_docker_restart():
    data = request.json or {}
    container_name = data.get("container_name", "")
    if container_name not in CFG["sysstatus"]["docker_containers"]:
        return jsonify({"ok": False, "message": "不在允許清單裡的容器名稱"}), 400
    ok, err = utils_sysstatus.restart_docker_container(container_name)
    log_activity_entry(f"重啟容器-{container_name}", ok, detail=err)
    if not ok:
        return jsonify({"ok": False, "message": err}), 500
    return jsonify({"ok": True, "message": f"已重啟容器 {container_name}"})


@app.route("/api/sysstatus/systemd")
@login_required
def api_sysstatus_systemd():
    results = []
    for svc in CFG["sysstatus"]["systemd_services"]:
        status = utils_sysstatus.get_systemd_service_status(svc["name"])
        status["port"] = svc.get("port", "")
        results.append(status)
    return jsonify({"ok": True, "data": results})


@app.route("/api/sysstatus/systemd/logs/<service_name>")
@login_required
def api_sysstatus_systemd_logs(service_name):
    allowed = {svc["name"] for svc in CFG["sysstatus"]["systemd_services"]}
    if service_name not in allowed:
        return jsonify({"ok": False, "message": "不在允許清單裡的服務名稱"}), 400
    logs, err = utils_sysstatus.get_systemd_logs(service_name, lines=300)
    if err:
        return jsonify({"ok": False, "message": err}), 500
    return jsonify({"ok": True, "logs": logs})


@app.route("/api/sysstatus/systemd/restart", methods=["POST"])
@login_required
def api_sysstatus_systemd_restart():
    data = request.json or {}
    service_name = data.get("service_name", "")
    allowed = {svc["name"] for svc in CFG["sysstatus"]["systemd_services"]}
    if service_name not in allowed:
        return jsonify({"ok": False, "message": "不在允許清單裡的服務名稱"}), 400
    ok, err = utils_sysstatus.restart_systemd_service(service_name)
    log_activity_entry(f"重啟服務-{service_name}", ok, detail=err)
    if not ok:
        return jsonify({"ok": False, "message": err}), 500
    return jsonify({"ok": True, "message": f"已重啟服務 {service_name}"})


@app.route("/api/sysstatus/mysql")
@login_required
def api_sysstatus_mysql():
    env = get_env_dict()
    db_configs = []
    missing_config_messages = []
    for cfg_key, label in [("mysql", "nanomdm"), ("nanodep_mysql", "nanodep"), ("nanoaxm_mysql", "nanoaxm")]:
        if cfg_key not in CFG:
            continue
        env_key = CFG[cfg_key]["db_password_env_key"]
        db_password = env.get(env_key, "")
        if not db_password:
            missing_config_messages.append(
                f"{label}: .env 裡沒有設定 {env_key},無法查詢這個資料庫的狀態(如果 {label} 服務本來就沒有獨立資料庫,可以忽略這則訊息)"
            )
            continue
        db_configs.append({
            "label": label,
            "docker_container": CFG[cfg_key]["docker_container"],
            "db_user": CFG[cfg_key]["db_user"],
            "db_password": db_password,
        })

    exact = request.args.get("exact") == "1"
    if exact:
        data, errors = utils_sysstatus.get_mysql_database_stats_exact(db_configs)
    else:
        data, errors = utils_sysstatus.get_mysql_database_stats(db_configs)
    return jsonify({"ok": True, "data": data, "errors": missing_config_messages + errors, "exact": exact})


@app.route("/api/sysstatus/static-files")
@login_required
def api_sysstatus_static_files():
    try:
        return jsonify({"ok": True, "data": utils_sysstatus.get_static_files_status(CFG)})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


if __name__ == "__main__":
    start_vpp_scheduler()
    start_asm_devices_scheduler()
    start_devices_status_scheduler()
    start_nanomdm_cleanup_scheduler()
    app.run(host="0.0.0.0", port=CFG.get("port", 5566), debug=False)
