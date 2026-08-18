#!/usr/bin/env python3
"""
動態 enrollment profile 伺服器：
1. 收到裝置 POST 過來的 CMS 簽署訊息
2. 用 openssl 解開，取出內含的 plist
3. 抽取 SERIAL 欄位，並加上時間戳記組成唯一 CN
4. 把 enrollment profile 樣板裡的序號佔位符換成這組唯一值
5. 回傳給裝置
"""
import http.server
import subprocess
import tempfile
import plistlib
import datetime
import os
import time

TEMPLATE_PATH = "/opt/nanomdm-deployment/mobileconfig/enroll-template.mobileconfig"
LOG_PATH = "/opt/nanomdm-deployment/enroll-debug.log"
PLACEHOLDER = "__SERIAL_PLACEHOLDER__"

with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
    TEMPLATE_CONTENT = f.read()


def log(msg):
    line = f"{datetime.datetime.now()} | {msg}\n"
    print(line, end="")
    with open(LOG_PATH, "a") as logf:
        logf.write(line)


def extract_unique_cn(raw_body: bytes) -> str:
    """用 openssl 解開 CMS 訊息，取出 plist 內的 SERIAL 欄位，組成唯一 CN"""
    with tempfile.NamedTemporaryFile(suffix=".p7", delete=False) as f:
        f.write(raw_body)
        p7_path = f.name

    plist_path = p7_path + ".plist"
    try:
        result = subprocess.run(
            ["openssl", "smime", "-verify", "-noverify", "-inform", "DER",
             "-in", p7_path, "-out", plist_path],
            capture_output=True, timeout=10
        )
        if result.returncode != 0:
            log(f"openssl 解開失敗: {result.stderr.decode(errors='replace')}")
            return "UNKNOWN"

        with open(plist_path, "rb") as f:
            parsed = plistlib.load(f)

        serial = parsed.get("SERIAL", "UNKNOWN")
        unique_cn = f"{serial}-{int(time.time())}"
        log(f"解析成功，SERIAL={serial}, UDID={parsed.get('UDID')}, "
            f"PRODUCT={parsed.get('PRODUCT')}, unique_cn={unique_cn}")
        return unique_cn
    finally:
        os.remove(p7_path)
        if os.path.exists(plist_path):
            os.remove(plist_path)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self._respond("UNKNOWN")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b""

        unique_cn = "UNKNOWN"
        content_type = self.headers.get("Content-Type", "")

        if "pkcs7" in content_type and body:
            unique_cn = extract_unique_cn(body)
        else:
            log(f"非預期的 Content-Type: {content_type}，body 長度: {len(body)}")

        self._respond(unique_cn)

    def _respond(self, unique_cn):
        content = TEMPLATE_CONTENT.replace(PLACEHOLDER, unique_cn)
        encoded = content.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/x-apple-aspen-config")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", 8091), Handler)
    log("動態 enrollment 伺服器啟動於 127.0.0.1:8091")
    server.serve_forever()
