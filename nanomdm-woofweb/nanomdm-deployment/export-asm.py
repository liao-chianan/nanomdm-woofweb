#!/usr/bin/env python3
"""
從 NanoAXM 抓取 ASM 上所有 MDM Server 與裝置資訊，整理成 CSV 檔案。
1. all_asm_server.csv - 所有 MDM Server 清單
2. all_asm_devices.csv - 所有裝置清單

只使用 Python 標準函式庫（subprocess 呼叫 curl），不需要額外安裝套件。
"""
import subprocess
import json
import csv
import os

# ===== 設定區：請依實際環境調整 =====
# ===== 設定區：請依實際環境調整 =====
AXM_NAME = os.environ.get("NANOAXM_NAME", "")
BASE_URL = os.environ.get("NANOAXM_BASE_URL", "http://127.0.0.1:9005")
API_KEY = os.environ.get("NANOAXM_API_KEY", "")
OUTPUT_DIR = "/opt/nanomdm-deployment"
# =====================================

SERVER_FIELDS = ["id", "servername", "createdDateTime", "updatedDateTime", "lastConnectedIp", "status"]
DEVICE_FIELDS = [
    "id", "serialNumber", "deviceModel", "partNumber", "wifiMacAddress",
    "deviceCapacity", "color", "orderDateTime", "addedToOrgDateTime",
    "updatedDateTime", "status"
]


def call_api(url: str) -> dict:
    result = subprocess.run(
        ["curl", "-s", "-u", f"nanoaxm:{API_KEY}", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl 執行失敗: {result.stderr}")
    return json.loads(result.stdout)


def fetch_all_pages(base_url: str) -> list:
    """處理分頁邏輯，抓取所有頁面的 data 陣列"""
    all_items = []
    cursor = None

    while True:
        url = base_url if not cursor else f"{base_url}&cursor={cursor}" \
            if "?" in base_url else f"{base_url}?cursor={cursor}"

        data = call_api(url)
        items = data.get("data", [])
        all_items.extend(items)
        print(f"  目前累積: {len(all_items)} 筆")

        cursor = data.get("meta", {}).get("paging", {}).get("nextCursor")
        if not cursor:
            break

    return all_items


def fetch_servers() -> list:
    print("抓取所有 MDM Server...")
    url = f"{BASE_URL}/proxy/school/{AXM_NAME}/v1/mdmServers"
    return fetch_all_pages(url)


def fetch_devices() -> list:
    print("抓取所有裝置...")
    url = f"{BASE_URL}/proxy/school/{AXM_NAME}/v1/orgDevices"
    return fetch_all_pages(url)


def write_servers_csv(servers: list, path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(SERVER_FIELDS)
        for s in servers:
            attrs = s.get("attributes", {})
            writer.writerow([
                s.get("id", ""),
                attrs.get("serverName", ""),
                attrs.get("createdDateTime", ""),
                attrs.get("updatedDateTime", ""),
                attrs.get("lastConnectedIp", ""),
                attrs.get("status", ""),
            ])
    print(f"已寫入 {len(servers)} 筆伺服器資料至 {path}")


def write_devices_csv(devices: list, path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(DEVICE_FIELDS)
        for d in devices:
            attrs = d.get("attributes", {})
            writer.writerow([
                d.get("id", ""),
                attrs.get("serialNumber", ""),
                attrs.get("deviceModel", ""),
                attrs.get("partNumber", ""),
                attrs.get("wifiMacAddress", ""),
                attrs.get("deviceCapacity", ""),
                attrs.get("color", ""),
                attrs.get("orderDateTime", ""),
                attrs.get("addedToOrgDateTime", ""),
                attrs.get("updatedDateTime", ""),
                attrs.get("status", ""),
            ])
    print(f"已寫入 {len(devices)} 筆裝置資料至 {path}")


def main():
    if not API_KEY:
        print("錯誤：找不到 NANOAXM_API_KEY 環境變數，請先 source .env")
        return
    if not AXM_NAME:
        print("錯誤：找不到 NANOAXM_NAME 環境變數，請先 source .env")
        return

    servers = fetch_servers()
    write_servers_csv(servers, os.path.join(OUTPUT_DIR, "all_asm_server.csv"))

    devices = fetch_devices()
    write_devices_csv(devices, os.path.join(OUTPUT_DIR, "all_asm_devices.csv"))

    print("\n完成！")


if __name__ == "__main__":
    main()
