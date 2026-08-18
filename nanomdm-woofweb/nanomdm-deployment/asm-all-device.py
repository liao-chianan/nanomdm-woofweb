import subprocess
import json
import os

AXM_NAME = os.environ.get("NANOAXM_NAME", "")
BASE_URL = os.environ.get("NANOAXM_BASE_URL", "http://127.0.0.1:9005")
API_KEY = os.environ.get("NANOAXM_API_KEY", "")

all_devices = []
cursor = None

while True:
    url = f"{BASE_URL}/proxy/school/{AXM_NAME}/v1/orgDevices"
    if cursor:
        url += f"?cursor={cursor}"

    result = subprocess.run(
        ["curl", "-s", "-u", f"nanoaxm:{API_KEY}", url],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)

    devices = data.get("data", [])
    all_devices.extend(devices)
    print(f"目前累積: {len(all_devices)} 筆")

    cursor = data.get("meta", {}).get("paging", {}).get("nextCursor")
    if not cursor:
        break

print(f"\n總計: {len(all_devices)} 台裝置\n")

# 輸出序號、狀態、指派狀況摘要
for d in all_devices:
    attrs = d.get("attributes", {})
    print(attrs.get("serialNumber"), "-", attrs.get("deviceModel"), "-", attrs.get("status"))

# 存成 JSON 檔案方便後續使用
with open("/opt/nanomdm-deployment/all-org-devices.json", "w", encoding="utf-8") as f:
    json.dump(all_devices, f, ensure_ascii=False, indent=2)
