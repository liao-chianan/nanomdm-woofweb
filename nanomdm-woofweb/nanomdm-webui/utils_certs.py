# -*- coding: utf-8 -*-
"""
憑證狀態檢視:對應使用者提供的 check-cert-expiry.sh 邏輯,逐項改寫成 Python。
涵蓋:nginx (Let's Encrypt)、SCEP 根 CA、APNs Push 憑證、DEP OAuth Token、
     VPP Content Token、NanoAXM 私鑰/OAuth(健康檢查,無到期日概念)。
"""
import base64
import datetime
import json
import os
import plistlib

import requests

import utils

WARNING_DAYS = 30
CRITICAL_DAYS = 14


def _status_from_days_left(days_left):
    if days_left is None:
        return "unknown"
    if days_left < 0:
        return "expired"
    if days_left <= CRITICAL_DAYS:
        return "critical"
    if days_left <= WARNING_DAYS:
        return "warning"
    return "ok"


def _make_result(name, location, description, renewal_method, check_type,
                  expiry_date=None, days_left=None, status=None, detail=None, error=None,
                  renewal_warning=None):
    return {
        "name": name,
        "location": location,
        "description": description,
        "renewal_method": renewal_method,
        "renewal_warning": renewal_warning,  # 需要用紅字特別標出的提醒文字,跟一般說明分開呈現
        "check_type": check_type,  # "expiry" | "health"
        "expiry_date": expiry_date,
        "days_left": days_left,
        "status": status or ("error" if error else "unknown"),
        "detail": detail,
        "error": error,
    }


def parse_openssl_enddate(raw):
    """把 openssl x509 -noout -enddate 的輸出(例如 'notAfter=Aug  1 12:00:00 2026 GMT')
    解析成 (expiry_date_str, days_left)。解析失敗回傳 (None, None)。
    """
    if not raw:
        return None, None
    raw = raw.strip()
    if raw.startswith("notAfter="):
        raw = raw[len("notAfter="):]
    raw = raw.replace(" GMT", "").strip()
    try:
        dt = datetime.datetime.strptime(raw, "%b %d %H:%M:%S %Y").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None, None
    now = datetime.datetime.now(datetime.timezone.utc)
    days_left = (dt - now).days
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC"), days_left


def get_cert_enddate_from_file(cert_path, timeout=10):
    if not os.path.exists(cert_path):
        return None, None, f"找不到憑證檔案: {cert_path}"
    rc, out, err = utils.run_cmd(["openssl", "x509", "-in", cert_path, "-noout", "-enddate"], timeout=timeout)
    if rc != 0:
        return None, None, f"openssl 解析失敗: {err or out}"
    expiry_date, days_left = parse_openssl_enddate(out)
    if expiry_date is None:
        return None, None, f"日期格式無法解析: {out.strip()}"
    return expiry_date, days_left, None


def get_cert_enddate_from_pem_text(pem_text, timeout=10):
    if not pem_text:
        return None, None, "沒有憑證內容"
    rc, out, err = utils.run_cmd_with_stdin(["openssl", "x509", "-noout", "-enddate"], pem_text, timeout=timeout)
    if rc != 0:
        return None, None, f"openssl 解析失敗: {err or out}"
    expiry_date, days_left = parse_openssl_enddate(out)
    if expiry_date is None:
        return None, None, f"日期格式無法解析: {out.strip()}"
    return expiry_date, days_left, None


# ---------------------------------------------------------------------------
# 各項憑證檢查
# ---------------------------------------------------------------------------
def check_nginx_cert(cert_path):
    name = "nginx (Let's Encrypt)"
    description = "提供 nanomdm/nginx reverse proxy 的 HTTPS 加密連線,裝置與管理介面都是透過這份憑證建立 TLS 連線。"
    renewal = "通常由 certbot 設定自動續期(systemd timer 或 cron job 執行 certbot renew),可用「certbot certificates」檢查續期排程是否正常運作;若自動續期失效,手動執行「certbot renew」或「certbot certonly」重新申請。"
    renewal_warning = "Let's Encrypt 憑證會自動更新,非必要不需要進行手動更新。"

    expiry_date, days_left, error = get_cert_enddate_from_file(cert_path)
    status = _status_from_days_left(days_left) if error is None else "error"
    return _make_result(name, cert_path, description, renewal, "expiry",
                         expiry_date, days_left, status, error=error, renewal_warning=renewal_warning)


def check_scep_ca(cert_path):
    name = "SCEP 根 CA (自簽)"
    description = "PKI 信任鏈的根憑證,裝置註冊時透過 SCEP 取得的身份憑證都是由這份根 CA 簽發。這是影響範圍最大的憑證——一旦過期或需要更換,所有已註冊裝置的身份憑證信任鏈都會受影響。"
    renewal = "自簽 CA 效期通常設定得很長(數年到數十年),多半不需要頻繁更新。若真的需要更換,需要重新產生 CA、重新設定 SCEP 服務,且已註冊裝置的舊憑證可能需要透過重新註冊才能取得新 CA 簽發的憑證,建議更換前詳細規劃,避免大量裝置同時失聯。"

    expiry_date, days_left, error = get_cert_enddate_from_file(cert_path)
    status = _status_from_days_left(days_left) if error is None else "error"
    return _make_result(name, cert_path, description, renewal, "expiry",
                         expiry_date, days_left, status, error=error)


def get_enroll_template_current_topic(mobileconfig_dir):
    """讀取精簡註冊描述檔(enroll-template.mobileconfig,enroll-server.py實際讀取使用的檔案)
    裡MDM Payload目前設定的Topic值,用來判斷「哪一組APNs憑證是目前新裝置註冊時實際會用到的」。
    讀取或解析失敗都回傳None,不讓這個輔助功能的失敗影響到主要的憑證狀態顯示。
    """
    path = os.path.join(mobileconfig_dir, "enroll-template.mobileconfig")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            parsed = plistlib.load(f)
        for payload in parsed.get("PayloadContent", []):
            if payload.get("PayloadType") == "com.apple.mdm":
                return payload.get("Topic") or None
    except Exception:
        return None
    return None


def check_apns_certs(mysql_cfg, db_password, mobileconfig_dir=None):
    """APNs Push 憑證存在 nanomdm 的 MySQL push_certs 表(cert_pem 欄位),
    直接讀出來用 openssl 計算到期日,不透過 nanomdm 的 /v1/pushcert API
    (該端點在 nanomdm v0.9.0 有已知問題)。
    可能有多組(多個topic),所以回傳 list。
    """
    name = "APNs Push 憑證"
    description = "Apple Push Notification service 憑證,MDM 伺服器要透過 APNs 推播通知裝置「有新指令待處理」時必須使用。這組憑證過期後,伺服器無法再喚醒裝置執行任何 MDM 指令(裝置檢查、安裝App、遠端抹除等全部會停擺,即使描述檔本身仍然有效)。"
    renewal = "到 Apple Push Certificates Portal (identity.apple.com/pushcert) 用「當初申請這張憑證的同一個 Apple ID且同一組Subject DN」續簽,下載新的憑證後透過 nanomdm 的憑證匯入工具重新上傳。務必使用同一組Subject DN,用不同帳號續簽會產生新的 Topic,導致所有已註冊裝置需要重新註冊。"
    renewal_warning = "每年在到期前一定得要到 https://identity.apple.com/pushcert/ 進行renew,否則將會讓所有裝置需要重新註冊。"

    docker_container = mysql_cfg["docker_container"]
    db_user = mysql_cfg["db_user"]
    db_name = mysql_cfg["db_name"]

    rc, out, err = utils.run_cmd([
        "docker", "exec", "-i", docker_container, "mysql",
        f"-u{db_user}", f"-p{db_password}", "-N", db_name,
        "-e", "SELECT topic FROM push_certs;",
    ], timeout=15)

    if rc != 0:
        return [_make_result(name, "MySQL: push_certs 表", description, renewal, "expiry",
                              error=f"查詢資料庫失敗: {err or out}", renewal_warning=renewal_warning)]

    topics = [t.strip() for t in out.strip().splitlines() if t.strip()]
    if not topics:
        return [_make_result(name, "MySQL: push_certs 表", description, renewal, "expiry",
                              error="資料庫中查無任何 push_certs 紀錄", renewal_warning=renewal_warning)]

    # 查出目前精簡註冊描述檔(enroll-server.py實際讀取使用的那份)裡設定的是哪個topic,
    # 這代表「目前新裝置註冊時實際會用到的憑證」,用來在畫面上標註「當前預設憑證」。
    current_default_topic = get_enroll_template_current_topic(mobileconfig_dir) if mobileconfig_dir else None

    # 多筆topic並存時,才需要額外查詢每個topic目前有多少裝置在使用,
    # 讓使用者在畫面上能一眼看出「這組憑證還有沒有裝置在靠它推播」,
    # 對於後續要不要刪除這筆舊憑證是重要的判斷依據。只有一筆的話沒有比較意義,不用多查。
    device_counts = {}
    if len(topics) > 1:
        for topic in topics:
            safe_topic = topic.replace("'", "''")
            rc_count, count_out, _ = utils.run_cmd([
                "docker", "exec", "-i", docker_container, "mysql",
                f"-u{db_user}", f"-p{db_password}", "-N", db_name,
                "-e", f"SELECT COUNT(*) FROM enrollments WHERE topic='{safe_topic}';",
            ], timeout=15)
            device_counts[topic] = count_out.strip() if rc_count == 0 else "?"

    results = []
    for topic in topics:
        safe_topic = topic.replace("'", "''")
        rc2, cert_pem, err2 = utils.run_cmd([
            "docker", "exec", "-i", docker_container, "mysql",
            f"-u{db_user}", f"-p{db_password}", "-N", db_name,
            "-e", f"SELECT cert_pem FROM push_certs WHERE topic='{safe_topic}';",
        ], timeout=15)
        if rc2 != 0 or not cert_pem.strip():
            results.append(_make_result(name, f"MySQL: push_certs (topic={topic})", description, renewal,
                                         "expiry", error=f"讀取憑證內容失敗: {err2 or '無資料'}", renewal_warning=renewal_warning))
            continue

        pem_text = cert_pem.replace("\\n", "\n")
        expiry_date, days_left, error = get_cert_enddate_from_pem_text(pem_text)
        status = _status_from_days_left(days_left) if error is None else "error"
        result = _make_result(
            name, f"MySQL: push_certs (topic={topic})", description, renewal, "expiry",
            expiry_date, days_left, status, detail=f"topic={topic}", error=error, renewal_warning=renewal_warning,
        )
        result["topic"] = topic
        result["topic_count"] = len(topics)
        result["is_current_default"] = bool(current_default_topic) and topic == current_default_topic
        if topic in device_counts:
            result["device_count"] = device_counts[topic]
        results.append(result)
    return results


def check_dep_oauth_token(nanodep_mysql_cfg, db_password, dep_name):
    name = "DEP OAuth Token"
    description = "nanodep 跟 Apple 官方 DEP(classic ADE)API 溝通用的伺服器對伺服器授權 Token,查詢/指派/更新裝置的 ADE 相關操作都要靠這組 Token。"
    renewal = "到 https://school.apple.com/#/main/preferences/myprofile (偏好設定 → 裝置管理服務),選擇指定的伺服器:「下載權杖」可以下載 .p7m 檔案上傳給自己的伺服器更新 Token;「編輯 → 上傳公用密鑰」則是更新公鑰用(僅更換伺服器時才需要)。"
    renewal_warning = "每年需要下載權杖更新1次p7m的token即可,除非更換伺服器才需要重新上傳公用密鑰再去下載p7m的token來更新。"

    docker_container = nanodep_mysql_cfg["docker_container"]
    db_user = nanodep_mysql_cfg["db_user"]
    db_name = nanodep_mysql_cfg["db_name"]
    safe_name = dep_name.replace("'", "''")

    rc, out, err = utils.run_cmd([
        "docker", "exec", "-i", docker_container, "mysql",
        f"-u{db_user}", f"-p{db_password}", "-N", db_name,
        "-e", f"SELECT access_token_expiry FROM dep_names WHERE name='{safe_name}';",
    ], timeout=15)

    if rc != 0:
        return _make_result(name, "MySQL: dep_names 表", description, renewal, "expiry",
                             error=f"查詢資料庫失敗: {err or out}", renewal_warning=renewal_warning)

    raw = out.strip()
    if not raw or raw.upper() == "NULL":
        return _make_result(name, "MySQL: dep_names 表", description, renewal, "expiry",
                             error="查無資料或此欄位為 NULL", renewal_warning=renewal_warning)

    try:
        dt = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        days_left = (dt - now).days
        expiry_date = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return _make_result(name, "MySQL: dep_names 表", description, renewal, "expiry",
                             error=f"日期格式無法解析: {raw}", renewal_warning=renewal_warning)

    status = _status_from_days_left(days_left)
    return _make_result(name, "MySQL: dep_names 表", description, renewal, "expiry",
                         expiry_date, days_left, status, renewal_warning=renewal_warning)


def check_vpp_token(token_path):
    name = "VPP Content Token"
    description = "驗證 VPP(Apps and Books)授權管理 API 呼叫用的憑證,查詢/指派/收回 App 授權都需要這個 Token。"
    renewal = "到 https://school.apple.com/#/main/preferences/paymentsandbilling/appsandbooks (偏好設定 → 付款與帳單 → Apps 與 Books),下載對應的 VPP Token 檔案,取代目前這個 .vpptoken 檔案即可(不需要更動程式碼)。"
    renewal_warning = "每年需要更新1次。"

    if not os.path.exists(token_path):
        return _make_result(name, token_path, description, renewal, "expiry",
                             error=f"找不到檔案: {token_path}", renewal_warning=renewal_warning)

    try:
        with open(token_path, "rb") as f:
            raw = f.read()
        decoded = base64.b64decode(raw)
        data = json.loads(decoded)
    except Exception as e:
        return _make_result(name, token_path, description, renewal, "expiry",
                             error=f"檔案解析失敗: {e}", renewal_warning=renewal_warning)

    exp_date_str = data.get("expDate", "")
    org_name = data.get("orgName", "")
    if not exp_date_str:
        return _make_result(name, token_path, description, renewal, "expiry",
                             error="Token 內容裡沒有 expDate 欄位", detail=f"組織名稱: {org_name}", renewal_warning=renewal_warning)

    try:
        dt = datetime.datetime.strptime(exp_date_str, "%Y-%m-%dT%H:%M:%S%z")
        now = datetime.datetime.now(datetime.timezone.utc)
        days_left = (dt - now).days
        expiry_date = dt.strftime("%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return _make_result(name, token_path, description, renewal, "expiry",
                             error=f"日期格式無法解析: {exp_date_str}", detail=f"組織名稱: {org_name}", renewal_warning=renewal_warning)

    status = _status_from_days_left(days_left)
    return _make_result(name, token_path, description, renewal, "expiry",
                         expiry_date, days_left, status, detail=f"組織名稱: {org_name}", renewal_warning=renewal_warning)


def check_nanoaxm_health(base_url, api_key, org_type, axm_name):
    """NanoAXM 的私鑰/OAuth 憑證依 Apple 官方文件不會自動到期,只會因手動撤銷而失效,
    因此沒有「到期日」可查,改成實際呼叫一次 API 驗證目前是否還能正常運作。
    """
    name = "NanoAXM 私鑰/OAuth憑證"
    description = "nanoAXM 呼叫新版 Apple Business/School Manager API 用的私鑰與 OAuth 憑證。這組憑證沒有自動到期機制,只會因為在 ABM/ASM 後台手動撤銷金鑰才會失效。"
    renewal = "此項無到期日可查,只能靠健康檢查確認目前是否仍有效。到 https://school.apple.com/#/main/preferences/apiaccounts (偏好設定 → API),可以查看/建立 Client ID(用戶端ID)與 Key ID(密鑰ID),需要更換金鑰時在這裡重新產生,並更新 NanoAXM 的設定。"
    renewal_warning = "不需要更新,僅在更換API時使用。"

    if not base_url or not api_key or not axm_name:
        return _make_result(name, "NanoAXM API 健康檢查", description, renewal, "health",
                             error=".env 內缺少 NANOAXM 相關設定,無法檢查", renewal_warning=renewal_warning)

    url = f"{base_url.rstrip('/')}/proxy/{org_type}/{axm_name}/v1/mdmServers"
    try:
        resp = requests.get(url, auth=("nanoaxm", api_key), timeout=15)
    except requests.RequestException as e:
        return _make_result(name, "NanoAXM API 健康檢查", description, renewal, "health",
                             status="error", error=f"連線失敗: {e}", renewal_warning=renewal_warning)

    if resp.status_code == 200:
        return _make_result(name, "NanoAXM API 健康檢查", description, renewal, "health",
                             status="ok", detail=f"HTTP {resp.status_code}", renewal_warning=renewal_warning)
    return _make_result(name, "NanoAXM API 健康檢查", description, renewal, "health",
                         status="error", detail=f"HTTP {resp.status_code}",
                         error=f"API 呼叫失敗,請確認私鑰是否遭撤銷或 NanoAXM 服務是否正常: {resp.text[:200]}",
                         renewal_warning=renewal_warning)


def run_all_checks(cfg, env):
    """跑過所有憑證檢查,任何一項失敗都不會擋住其他項目,回傳結果列表。
    順序刻意安排成「每年需要手動更新、影響重大的放前面」:
    APNs -> DEP -> VPP -> SCEP -> NanoAXM -> nginx。
    這個順序同時決定了前端「憑證狀態檢視」總覽表格跟「憑證說明與更新方法」區塊的顯示順序,
    因為兩者都是照這個results陣列的順序逐一渲染,只要這裡排好,兩邊就會一致。
    """
    results = []

    db_password = env.get(cfg["mysql"]["db_password_env_key"], "")
    results.extend(check_apns_certs(cfg["mysql"], db_password, mobileconfig_dir=cfg["paths"]["mobileconfig_dir"]))

    nanodep_db_password = env.get(cfg["nanodep_mysql"]["db_password_env_key"], "")
    dep_name = env.get(cfg["nanodep"]["name_env_key"], "")
    if dep_name:
        results.append(check_dep_oauth_token(cfg["nanodep_mysql"], nanodep_db_password, dep_name))
    else:
        results.append(_make_result("DEP OAuth Token", "MySQL: dep_names 表", "", "", "expiry",
                                     error=".env 內缺少 NANODEP_NAME,無法查詢"))

    results.append(check_vpp_token(cfg["cert_status"]["vpp_token_path"]))

    results.append(check_scep_ca(cfg["cert_status"]["scep_ca_path"]))

    axm_base_url = env.get(cfg["nanoaxm"]["base_url_env_key"], "")
    axm_api_key = env.get(cfg["nanoaxm"]["api_key_env_key"], "")
    axm_name = env.get(cfg["nanoaxm"]["name_env_key"], "")
    org_type = cfg["nanoaxm"]["org_type"]
    results.append(check_nanoaxm_health(axm_base_url, axm_api_key, org_type, axm_name))

    results.append(check_nginx_cert(cfg["cert_status"]["nginx_cert_path"]))

    return results


def get_cert_subject(cert_path, timeout=10):
    """讀取憑證的Subject(組織/OU/國別),用來在重新產生SCEP CA前,
    讓使用者看到目前CA的組織資訊做參考,不同openssl版本輸出格式不完全一樣,
    這裡用寬鬆的方式解析,解析不出來的欄位就留空,不會因此整個失敗。
    """
    if not os.path.exists(cert_path):
        return {"organization": "", "organizational_unit": "", "country": "", "common_name": "", "error": f"找不到憑證檔案: {cert_path}"}

    rc, out, err = utils.run_cmd(["openssl", "x509", "-in", cert_path, "-noout", "-subject", "-nameopt", "multiline"], timeout=timeout)
    if rc != 0:
        return {"organization": "", "organizational_unit": "", "country": "", "common_name": "", "error": f"openssl 解析失敗: {err or out}"}

    result = {"organization": "", "organizational_unit": "", "country": "", "common_name": "", "error": None}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("organizationName"):
            result["organization"] = line.split("=", 1)[1].strip() if "=" in line else ""
        elif line.startswith("organizationalUnitName"):
            result["organizational_unit"] = line.split("=", 1)[1].strip() if "=" in line else ""
        elif line.startswith("countryName"):
            result["country"] = line.split("=", 1)[1].strip() if "=" in line else ""
        elif line.startswith("commonName"):
            result["common_name"] = line.split("=", 1)[1].strip() if "=" in line else ""
    return result


def extract_topic_from_cert_pem(cert_pem_text, timeout=10):
    """從APNs推播憑證的PEM內容裡,提取Subject裡的UID欄位(即topic,
    格式類似com.apple.mgmt.External.xxxxxxxx)。用在上傳新憑證前,
    不用真的上傳到nanomdm就能預先知道這張新憑證的topic是什麼,
    才能在畫面上提早警告使用者「這會不會跟現有的topic不一樣」。
    解析失敗回傳 (None, 錯誤訊息)。
    """
    rc, out, err = utils.run_cmd_with_stdin(
        ["openssl", "x509", "-noout", "-subject", "-nameopt", "multiline"],
        cert_pem_text, timeout=timeout,
    )
    if rc != 0:
        return None, f"無法解析憑證內容: {err or out}"
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("userId"):
            topic = line.split("=", 1)[1].strip() if "=" in line else ""
            if topic:
                return topic, None
    return None, "憑證裡找不到UID欄位,可能不是有效的APNs推播憑證"
