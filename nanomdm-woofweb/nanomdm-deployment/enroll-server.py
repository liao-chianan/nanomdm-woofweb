#!/usr/bin/env python3
"""
動態 enrollment profile 伺服器：
1. 收到裝置 POST 過來的 CMS 簽署訊息
2. 用 openssl 解開，取出內含的 plist
3. 抽取 SERIAL 欄位，並加上時間戳記組成唯一 CN
4. 把 enrollment profile 樣板裡的序號佔位符換成這組唯一值
5. (如果簽署憑證存在)用SCEP CA簽發的專用憑證簽署這份逐裝置產生的內容,
   讓裝置安裝時顯示為已驗證
6. 回傳給裝置
"""
import http.server
import subprocess
import tempfile
import plistlib
import datetime
import os
import sys
import time

# 跨目錄import,重用webui那邊的簽署邏輯(utils_signing.py),不在這裡重複寫一份
# 一模一樣的簽署程式碼,避免兩邊各自維護、日後改一邊忘了改另一邊。
sys.path.insert(0, "/opt/nanomdm-webui")
try:
    import utils_signing
    _SIGNING_AVAILABLE = True
except ImportError:
    _SIGNING_AVAILABLE = False

TEMPLATE_PATH = "/opt/nanomdm-deployment/mobileconfig/enroll-template.mobileconfig"
LOG_PATH = "/opt/nanomdm-deployment/enroll-debug.log"
PLACEHOLDER = "__SERIAL_PLACEHOLDER__"

# 簽署憑證/私鑰/CA路徑,可用環境變數覆寫,預設值對齊webui的config.py裡
# profile_signing/cert_status這兩個區塊的預設路徑約定,兩邊指的是同一組檔案。
SIGNING_CERT_PATH = os.environ.get(
    "PROFILE_SIGNING_CERT_PATH", "/opt/nanomdm-deployment/scep-depot/profile-signing-cert.pem"
)
SIGNING_KEY_PATH = os.environ.get(
    "PROFILE_SIGNING_KEY_PATH", "/opt/nanomdm-deployment/scep-depot/profile-signing-key.pem"
)
SCEP_CA_PATH = os.environ.get("SCEP_CA_PATH", "/opt/nanomdm-deployment/scep-depot/ca.pem")

def _load_template_content():
    """讀取enrollment模板內容。這份檔案本質上是一份「含有字面佔位符的原始文字樣板」,
    絕對不能是簽署過的格式——一旦被簽署,這裡完全沒辦法對它做文字替換,也沒辦法用
    文字模式讀取。

    但這份檔案同時也在webui描述檔編輯器的管理範圍內(只是被保護不能被刪除),
    如果不小心透過一般編輯器存檔、又剛好啟用了簽署功能,就可能被意外簽署,
    導致這裡讀取失敗、整個服務起不來。

    這裡加上自動修復機制:如果偵測到不是合法UTF-8文字(代表被意外簽署過),
    嘗試用簽署功能自帶的解開邏輯,取出底層原始plist、重新序列化回文字格式——
    plistlib重新序列化字串內容時,原本的佔位符文字會被完整保留,所以修復後的
    內容依然可以正常使用。修復成功會寫回檔案,避免下次啟動又要重新修復一次,
    也方便之後在webui的描述檔清單上看到這份檔案正確顯示「未簽署」。
    """
    with open(TEMPLATE_PATH, "rb") as f:
        raw_bytes = f.read()

    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        pass

    log("警告: enroll-template.mobileconfig 目前是簽署過的格式,這份檔案本質上不能被簽署"
        "(裡面的序號佔位符沒辦法對簽署過的內容做文字替換)。嘗試自動修復...")

    if not _SIGNING_AVAILABLE:
        raise RuntimeError(
            "enroll-template.mobileconfig 無法以文字模式讀取,且簽署功能模組載入失敗,"
            "無法自動修復。請手動確認這份檔案的內容,或從備份還原。"
        )

    plist_bytes, err = utils_signing.extract_plist_from_signed_bytes(raw_bytes)
    if plist_bytes is None:
        raise RuntimeError(
            f"enroll-template.mobileconfig 無法以文字模式讀取,自動修復也失敗: {err}。"
            "請手動確認這份檔案的內容,或從備份還原。"
        )

    recovered_content = plistlib.loads(plist_bytes)
    recovered_bytes = plistlib.dumps(recovered_content)
    recovered_text = recovered_bytes.decode("utf-8")

    if PLACEHOLDER not in recovered_text:
        raise RuntimeError(
            "enroll-template.mobileconfig 自動修復後,找不到預期的序號佔位符"
            f"({PLACEHOLDER}),修復結果可能不可靠,請手動確認。"
        )

    # 修復成功,寫回檔案(純文字,不簽署),避免下次啟動又要重新修復一次
    tmp_path = TEMPLATE_PATH + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(recovered_bytes)
    os.replace(tmp_path, TEMPLATE_PATH)
    log("enroll-template.mobileconfig 已自動修復成未簽署的文字格式,並寫回原檔案。"
        "請之後避免透過描述檔編輯器對這份檔案存檔(這份檔案不支援簽署)。")

    return recovered_text


def log(msg):
    line = f"{datetime.datetime.now()} | {msg}\n"
    print(line, end="")
    with open(LOG_PATH, "a") as logf:
        logf.write(line)


TEMPLATE_CONTENT = _load_template_content()


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

        # 替換完成、產生這台裝置專屬的內容之後,才簽署——順序不能顛倒,
        # 如果簽署過後才做文字替換,會直接破壞簽名(PKCS-7簽署過的內容,
        # 任何一個位元組被更動,簽名就會失效),而且每台裝置的unique_cn都不同,
        # 沒辦法只在啟動時簽署一次,必須每次請求都重新簽署這次專屬的內容。
        final_bytes = encoded
        if _SIGNING_AVAILABLE and utils_signing.signing_cert_exists(SIGNING_CERT_PATH, SIGNING_KEY_PATH):
            signed_bytes, sign_err = utils_signing.sign_plist_bytes(
                encoded, SIGNING_CERT_PATH, SIGNING_KEY_PATH, ca_cert_path=SCEP_CA_PATH,
            )
            if signed_bytes is not None:
                final_bytes = signed_bytes
            else:
                log(f"簽署失敗,改回傳未簽署版本(不影響裝置註冊): {sign_err}")

        self.send_response(200)
        self.send_header("Content-Type", "application/x-apple-aspen-config")
        self.send_header("Content-Length", str(len(final_bytes)))
        self.end_headers()
        self.wfile.write(final_bytes)


if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", 8091), Handler)
    log("動態 enrollment 伺服器啟動於 127.0.0.1:8091")
    server.serve_forever()
