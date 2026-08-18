"""
設定檔載入模組
獨立於程式碼之外的設定檔:webui_config.json
"""
import json
import os
import secrets

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_config.json")

DEFAULT_CONFIG = {
    "secret_key": secrets.token_hex(32),
    "admin_username": "admin",
    "admin_password_hash": None,
    "admin_accounts": [],  # [{"username":..., "password_hash":..., "created_at":...}, ...]
    "ip_allowlist": [],    # CIDR字串清單,例如 ["192.168.1.0/24"];空清單代表不限制
    "port": 5566,
    "paths": {
        "env_file": "/opt/nanomdm-deployment/.env",
        "env_backup_dir": "/opt/nanomdm-deployment/env_backups",
        "devices_csv": "/opt/nanomdm-deployment/devices.csv",
        "groups_json": "/opt/nanomdm-deployment/groups.json",
        "cmdr_script": "/opt/nanomdm-deployment/cmdr.py",
        "dep_account_detail_script": "/opt/nanomdm-deployment/nanodep-release/tools/dep-account-detail.sh",
        "cfg_get_cert_script": "/opt/nanomdm-deployment/nanodep-release/tools/cfg-get-cert.sh",
        "cfg_decrypt_tokens_script": "/opt/nanomdm-deployment/nanodep-release/tools/cfg-decrypt-tokens.sh",
        "cfg_authcreds_script": "/opt/nanomdm-deployment/nanoaxm-tools/cfg-authcreds.sh",
        "dep_device_details_script": "/opt/nanomdm-deployment/nanodep-release/tools/dep-device-details.sh",
        "check_vpp_license_script": "/opt/nanomdm-deployment/check_vpp_license.sh",
        "vpp_cache_csv": "/opt/nanomdm-deployment/vpp_license.csv",
        "mobileconfig_dir": "/opt/nanomdm-deployment/mobileconfig",
        "dep_profiles_dir": "/opt/nanomdm-deployment/dep-profiles",
        "logo_dir": "/opt/nanomdm-webui/logo",
        "udid_serial_cache": "/opt/nanomdm-deployment/udid-serial-cache.json",
        "udid_serial_cache_lock": "/opt/nanomdm-deployment/udid-serial-cache.lock"
    },
    "nanodep": {
        "base_url_env_key": "NANODEP_BASE_URL",
        "api_key_env_key": "NANODEP_API_KEY",
        "name_env_key": "NANODEP_NAME",
        "depsyncer_restart_cmd_env_key": "NANODEP_DEPSYNCER_RESTART_CMD"
    },
    "nanoaxm": {
        "base_url_env_key": "NANOAXM_BASE_URL",
        "api_key_env_key": "NANOAXM_API_KEY",
        "name_env_key": "NANOAXM_NAME",
        "org_type": "school"
    },
    "asm_devices_cache": {
        "servers_csv": "/opt/nanomdm-deployment/all_asm_server.csv",
        "devices_csv": "/opt/nanomdm-deployment/all_asm_devices.csv",
        "refresh_interval_seconds": 1800
    },
    "mysql": {
        "docker_container": "nanomdm-mysql",
        "db_user": "nanomdm",
        "db_name": "nanomdm",
        "db_password_env_key": "NANOMDM_DB_PASSWORD"
    },
    "nanodep_mysql": {
        "docker_container": "nanomdm-mysql",
        "db_user": "nanodep",
        "db_name": "nanodep",
        "db_password_env_key": "NANODEP_DB_PASSWORD"
    },
    "nanoaxm_mysql": {
        "docker_container": "nanomdm-mysql",
        "db_user": "nanoaxm",
        "db_name": "nanoaxm",
        "db_password_env_key": "NANOAXM_DB_PASSWORD"
    },
    "cert_status": {
        "nginx_cert_path": "/etc/letsencrypt/live/YOUR_DOMAIN_HERE/fullchain.pem",
        "scep_ca_path": "/opt/nanomdm-deployment/scep-depot/ca.pem",
        "scep_depot_dir": "/opt/nanomdm-deployment/scep-depot",
        "scep_ca_backup_dir": "/opt/nanomdm-deployment/scep-depot-backups",
        "scep_docker_image": "local/scep:latest",
        "scep_entrypoint": "/usr/local/bin/scepserver-linux-amd64",
        "vpp_token_path": "/opt/nanomdm-deployment/vpp_token.vpptoken"
    },
    "nanomdm": {
        "base_url_env_key": "NANOMDM_BASE_URL",
        "api_user": "nanomdm",
        "api_key_env_key": "NANOMDM_API_KEY"
    },
    "system_logs": {
        "user_login_log": "/opt/nanomdm-deployment/logs/user_login.log",
        "user_login_retention_days": 60,
        "user_activity_log": "/opt/nanomdm-deployment/logs/user_activity.log",
        "user_activity_retention_days": 30
    },
    "devices_status_cache": {
        "csv_path": "/opt/nanomdm-deployment/devices-status.csv",
        "refresh_interval_seconds": 600
    },
    "nanomdm_cleanup": {
        "retention_days": 60,
        "auto_enabled": False,
        "check_interval_seconds": 86400
    },
    "update": {
        "github_owner": "liao-chianan",
        "github_repo": "nanomdm-woofweb",
        "repo_subfolder": "nanomdm-woofweb",
        "version_file": "/opt/nanomdm-webui/VERSION",
        "eligible_extensions": [".py", ".sh", ".css", ".js", ".html"],
        "path_map": {
            "nanomdm-webui": "/opt/nanomdm-webui",
            "nanomdm-deployment": "/opt/nanomdm-deployment"
        },
        "service_map": {
            "nanomdm-webui": "nanomdm-webui.service",
            "webhook-server.py": "webhook-automation.service",
            "enroll-server.py": "enroll-server.service"
        }
    },
    "nanomdm_docker": {
        "container_name": "nanomdm-server",
        "log_tail_lines": 5000
    },
    "branding": {
        "site_label": "NanoMDM 管理系統",
        "logo_filename": "default.png"
    },
    "sysstatus": {
        "docker_containers": [
            "nanomdm-server", "nanoaxm-server", "nanodep-server", "nanomdm-scep", "nanomdm-mysql"
        ],
        "systemd_services": [
            {"name": "webhook-automation.service", "port": "8092"},
            {"name": "enroll-server.service", "port": ""},
            {"name": "nanodep-syncer.service", "port": ""},
            {"name": "nanomdm-webui.service", "port": "5566"},
            {"name": "nginx.service", "port": "80/443"}
        ]
    }
}


class ConfigError(Exception):
    pass


def _read_env_file_simple(path):
    """最小、自成一體的.env讀取函式(不依賴utils.py,避免config.py載入時牽扯進不必要的模組)。
    只解析單純的 KEY=VALUE 格式,去掉前後空白跟包住的引號,註解與空行忽略。
    找不到檔案就回傳空字典,不報錯——這是為了讓「.env裡沒有寫這個變數」這種正常情況,
    可以優雅地退回程式內建的預設值,而不是讓整個設定載入失敗。
    """
    result = {}
    if not os.path.exists(path):
        return result
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                value = value.strip()
                if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                result[key.strip()] = value
    except Exception:
        return {}
    return result


def _apply_env_overrides(cfg):
    """讓幾個「會隨部署環境不同而改變」的路徑,可以直接在.env裡設定覆寫,
    不用去改程式碼或webui_config.json——方便佈署到別的學校時,只要編輯.env一個檔案。
    .env裡沒有設定對應變數的話,就維持webui_config.json/DEFAULT_CONFIG裡原本的值,
    不影響現有部署的行為。

    目前支援覆寫的對應關係:
      NGINX_CERT_PATH -> cfg["cert_status"]["nginx_cert_path"]
      VPP_TOKEN_PATH  -> cfg["cert_status"]["vpp_token_path"]
                         (這個環境變數名稱跟webhook-server.py用的是同一個,
                          確保兩邊讀到的是同一份VPP Token檔案路徑,不會各自寫死、各自對不起來)
    """
    env_file_path = cfg.get("paths", {}).get("env_file", "")
    env = _read_env_file_simple(env_file_path)

    if env.get("NGINX_CERT_PATH"):
        cfg["cert_status"]["nginx_cert_path"] = env["NGINX_CERT_PATH"]
    if env.get("VPP_TOKEN_PATH"):
        cfg["cert_status"]["vpp_token_path"] = env["VPP_TOKEN_PATH"]

    return cfg


def load_config():
    """讀取 webui_config.json,若不存在則報錯提示先執行 setup"""
    if not os.path.exists(CONFIG_PATH):
        raise ConfigError(
            f"找不到設定檔 {CONFIG_PATH}\n"
            f"請先執行: python3 scripts/setup_config.py 建立管理者帳號密碼與設定"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 補齊缺少的預設值(避免舊設定檔缺欄位造成錯誤)
    def merge_defaults(base, defaults):
        for k, v in defaults.items():
            if k not in base:
                base[k] = v
            elif isinstance(v, dict) and isinstance(base.get(k), dict):
                merge_defaults(base[k], v)
        return base

    cfg = merge_defaults(cfg, DEFAULT_CONFIG)
    cfg = _apply_env_overrides(cfg)

    # 舊版設定檔遷移:單一帳號(admin_username+admin_password_hash) -> 帳號清單(admin_accounts)
    # 只在 admin_accounts 是空的、但舊格式有資料時才遷移,避免覆蓋掉使用者已經自行新增的帳號清單
    if not cfg.get("admin_accounts") and cfg.get("admin_password_hash"):
        cfg["admin_accounts"] = [{
            "username": cfg.get("admin_username") or "admin",
            "password_hash": cfg["admin_password_hash"],
            "created_at": None,
        }]
        save_config(cfg)

    if not cfg.get("admin_accounts"):
        raise ConfigError(
            "設定檔內尚未設定任何管理者帳號,請先執行: python3 scripts/setup_config.py"
        )
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def reload_auth_state():
    """輕量重新讀取帳號清單與IP白名單(不做完整的load_config驗證/遷移),
    供登入檢查、IP限制檢查等每次請求都要用最新資料的地方呼叫,
    這樣新增/刪除帳號或IP規則後不需要重啟服務就能立即生效。
    """
    if not os.path.exists(CONFIG_PATH):
        return {"admin_accounts": [], "ip_allowlist": []}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return {
        "admin_accounts": cfg.get("admin_accounts", []),
        "ip_allowlist": cfg.get("ip_allowlist", []),
    }


def reload_branding():
    """輕量重新讀取品牌設定(站台名稱、LOGO檔名),讓每個頁面渲染時都能拿到最新設定,
    不用重啟服務。找不到設定檔時回傳預設值。
    """
    default = {"site_label": "NanoMDM 管理系統", "logo_filename": "default.png"}
    if not os.path.exists(CONFIG_PATH):
        return default
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    branding = cfg.get("branding", {})
    return {
        "site_label": branding.get("site_label") or default["site_label"],
        "logo_filename": branding.get("logo_filename") or default["logo_filename"],
    }


def save_branding(site_label=None, logo_filename=None):
    """更新品牌設定裡的其中一個或兩個欄位,沒傳的欄位維持原值不變。"""
    if not os.path.exists(CONFIG_PATH):
        return
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    branding = cfg.get("branding", {})
    if site_label is not None:
        branding["site_label"] = site_label
    if logo_filename is not None:
        branding["logo_filename"] = logo_filename
    cfg["branding"] = branding
    save_config(cfg)
