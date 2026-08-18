#!/usr/bin/env python3
"""
第一次使用前執行這支腳本,建立 webui_config.json
用法: python3 scripts/setup_config.py
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from werkzeug.security import generate_password_hash
from config import DEFAULT_CONFIG, CONFIG_PATH, save_config
import json


def main():
    print("=== NanoMDM Web 管理介面 - 初始設定 ===")
    print(f"設定檔將寫入: {CONFIG_PATH}\n")

    if os.path.exists(CONFIG_PATH):
        ans = input("設定檔已存在,是否覆蓋? (y/N): ").strip().lower()
        if ans != "y":
            print("已取消")
            return
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = dict(DEFAULT_CONFIG)

    username = input(f"管理者帳號 [{cfg.get('admin_username', 'admin')}]: ").strip()
    if username:
        cfg["admin_username"] = username
    elif "admin_username" not in cfg:
        cfg["admin_username"] = "admin"

    while True:
        pw1 = getpass.getpass("管理者密碼: ")
        pw2 = getpass.getpass("再輸入一次確認: ")
        if pw1 != pw2:
            print("兩次輸入不一致,請再試一次\n")
            continue
        if len(pw1) < 8:
            print("密碼長度至少 8 碼,請再試一次\n")
            continue
        break

    cfg["admin_password_hash"] = generate_password_hash(pw1)

    port = input(f"服務 Port [{cfg.get('port', 5566)}]: ").strip()
    if port:
        cfg["port"] = int(port)

    print("\n以下路徑設定可直接按 Enter 使用預設值,之後也可以在 webui_config.json 裡手動修改:")
    for key in ["env_file", "devices_csv", "groups_json", "cmdr_script",
                "dep_account_detail_script", "dep_device_details_script",
                "check_vpp_license_script"]:
        current = cfg["paths"].get(key, DEFAULT_CONFIG["paths"][key])
        val = input(f"  {key} [{current}]: ").strip()
        if val:
            cfg["paths"][key] = val

    save_config(cfg)
    print(f"\n設定完成,已寫入 {CONFIG_PATH}")
    print("現在可以執行: python3 app.py")


if __name__ == "__main__":
    main()
