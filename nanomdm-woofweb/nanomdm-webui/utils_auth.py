# -*- coding: utf-8 -*-
"""
帳號管理與 IP 白名單管理:
- 帳號、IP 規則都存在 webui_config.json 裡(跟 config.py 共用同一個檔案),
  但讀寫都透過這裡的函式,確保每次異動都會做安全檢查。
- 刻意避免兩種鎖死自己的情況:
  1. 帳號被刪到只剩0個,或刪掉自己目前登入中的帳號
  2. IP 白名單設定成不包含自己目前所在的IP,導致自己也被擋在外面
"""
import datetime
import ipaddress
import json
import os

from werkzeug.security import generate_password_hash, check_password_hash

import config as config_module


class AuthError(Exception):
    pass


def _load_full_config():
    with open(config_module.CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_full_config(cfg):
    config_module.save_config(cfg)


# ---------------------------------------------------------------------------
# 帳號管理
# ---------------------------------------------------------------------------
def list_accounts():
    """回傳帳號清單,不含password_hash(不該傳到前端)"""
    cfg = _load_full_config()
    return [
        {"username": a["username"], "created_at": a.get("created_at")}
        for a in cfg.get("admin_accounts", [])
    ]


def verify_login(username, password):
    state = config_module.reload_auth_state()
    for account in state["admin_accounts"]:
        if account["username"] == username and check_password_hash(account["password_hash"], password):
            return True
    return False


def add_account(username, password):
    if not username or not username.strip():
        raise AuthError("帳號名稱不能是空的")
    username = username.strip()
    if len(password) < 8:
        raise AuthError("密碼長度至少需要 8 碼")

    cfg = _load_full_config()
    accounts = cfg.get("admin_accounts", [])
    if any(a["username"] == username for a in accounts):
        raise AuthError(f"帳號「{username}」已經存在")

    accounts.append({
        "username": username,
        "password_hash": generate_password_hash(password),
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    cfg["admin_accounts"] = accounts
    _save_full_config(cfg)


def delete_account(username, current_username):
    """刪除帳號。安全防護:不能刪除自己目前登入中的帳號,也不能刪到剩0個帳號。"""
    if username == current_username:
        raise AuthError("不能刪除自己目前登入中的帳號,請先用其他帳號登入再刪除")

    cfg = _load_full_config()
    accounts = cfg.get("admin_accounts", [])
    remaining = [a for a in accounts if a["username"] != username]

    if len(remaining) == len(accounts):
        raise AuthError(f"找不到帳號「{username}」")
    if len(remaining) == 0:
        raise AuthError("不能刪除最後一個管理者帳號,系統至少需要保留一組登入帳號")

    cfg["admin_accounts"] = remaining
    _save_full_config(cfg)


def change_password(username, new_password, current_username):
    """改密碼(改自己的或改別人的都可以,只要是登入中的使用者操作)"""
    if len(new_password) < 8:
        raise AuthError("密碼長度至少需要 8 碼")

    cfg = _load_full_config()
    accounts = cfg.get("admin_accounts", [])
    found = False
    for a in accounts:
        if a["username"] == username:
            a["password_hash"] = generate_password_hash(new_password)
            found = True
            break
    if not found:
        raise AuthError(f"找不到帳號「{username}」")

    cfg["admin_accounts"] = accounts
    _save_full_config(cfg)


# ---------------------------------------------------------------------------
# IP 白名單 (CIDR)
# ---------------------------------------------------------------------------
def list_ip_rules():
    cfg = _load_full_config()
    return cfg.get("ip_allowlist", [])


def validate_cidr(cidr_str):
    """驗證CIDR格式是否合法,回傳正規化後的字串(例如去除多餘空白)。格式不對會丟ValueError。"""
    cidr_str = (cidr_str or "").strip()
    try:
        # strict=False 允許輸入單一IP(視為/32或/128)或含host bits的網段寫法
        network = ipaddress.ip_network(cidr_str, strict=False)
    except ValueError as e:
        raise AuthError(f"「{cidr_str}」不是合法的 CIDR 或 IP 格式: {e}")
    return str(network)


def is_ip_allowed(ip_str, rules):
    """檢查ip_str是否落在rules(CIDR字串清單)裡任何一個網段。rules為空時一律允許(不限制)。"""
    if not rules:
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for cidr_str in rules:
        try:
            network = ipaddress.ip_network(cidr_str, strict=False)
        except ValueError:
            continue
        if ip in network:
            return True
    return False


def save_ip_rules(new_rules, requester_ip):
    """儲存IP白名單,存檔前的安全防護:
    如果新規則不是空清單,一定要確保 requester_ip(這次發出存檔請求的人目前的IP)有被涵蓋在內,
    否則直接拒絕存檔,避免使用者手滑把自己鎖在外面進不來。
    """
    normalized = [validate_cidr(r) for r in new_rules if r and r.strip()]

    if normalized and not is_ip_allowed(requester_ip, normalized):
        raise AuthError(
            f"這組規則不包含你目前的來源 IP({requester_ip}),為了避免把自己鎖在外面,已拒絕儲存。"
            f"請確認規則有涵蓋到你目前的 IP,或先加入 {requester_ip}/32 再存檔。"
        )

    cfg = _load_full_config()
    cfg["ip_allowlist"] = normalized
    _save_full_config(cfg)
    return normalized
