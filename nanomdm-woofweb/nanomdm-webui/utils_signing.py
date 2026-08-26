import os
import tempfile

import utils


def signing_cert_exists(cert_path, key_path):
    """檢查簽署憑證跟私鑰是否都已經產生過"""
    return os.path.exists(cert_path) and os.path.exists(key_path)


def get_signing_cert_info(cert_path):
    """回傳簽署憑證的到期日等資訊,方便畫面上顯示。找不到憑證時回傳None。"""
    if not os.path.exists(cert_path):
        return None
    import utils_certs
    enddate = utils_certs.get_cert_enddate_from_file(cert_path)
    subject = utils_certs.get_cert_subject(cert_path)
    return {"enddate": enddate, "subject": subject}


def generate_profile_signing_cert(scep_ca_cert_path, scep_ca_key_path, out_cert_path, out_key_path,
                                   common_name="NanoMDM WoofWeb Profile Signer", days=3650, timeout=30,
                                   ca_key_password=None):
    """用既有的SCEP CA簽發一張專門給描述檔簽署用的leaf憑證,不是直接拿CA本身的憑證/私鑰
    來簽署描述檔(那樣風險太高,CA私鑰一旦外洩,影響範圍是整個裝置身分驗證體系;用一張
    獨立簽發、僅用於描述檔簽署的leaf憑證,就算這張憑證或私鑰外洩,影響範圍也只限於
    「描述檔的簽署身分可以被偽造」,不會波及裝置憑證體系本身)。

    做法:openssl genrsa產生新私鑰 → openssl req產生CSR → openssl x509 -req
    用SCEP CA的憑證+私鑰簽署這張CSR,產生leaf憑證。

    ca_key_password:選填,SCEP CA私鑰如果有密碼保護(install.sh產生的.env裡的
    SCEP_CA_PASSWORD),需要提供這個密碼openssl才能讀取私鑰,不然會卡在互動式
    要求輸入密碼(在我們這種非互動的後端環境裡會直接失敗)。用-passin env:XXX
    透過環境變數傳遞,不是-passin pass:XXX直接寫在指令列參數裡,避免密碼明文
    出現在ps aux這類程序列表裡可能被其他系統使用者看到。

    回傳 (ok, message)。
    """
    if not os.path.exists(scep_ca_cert_path):
        return False, f"找不到 SCEP CA 憑證: {scep_ca_cert_path}"
    if not os.path.exists(scep_ca_key_path):
        return False, f"找不到 SCEP CA 私鑰: {scep_ca_key_path}(簽署新憑證需要CA的私鑰,不是只有憑證本身)"

    with tempfile.TemporaryDirectory() as tmpdir:
        key_path = os.path.join(tmpdir, "signing.key")
        csr_path = os.path.join(tmpdir, "signing.csr")
        cert_path = os.path.join(tmpdir, "signing.pem")
        srl_path = os.path.join(tmpdir, "ca.srl")

        rc1, _, err1 = utils.run_cmd(["openssl", "genrsa", "-out", key_path, "2048"], timeout=timeout)
        if rc1 != 0:
            return False, f"產生私鑰失敗: {err1}"

        rc2, _, err2 = utils.run_cmd(
            ["openssl", "req", "-new", "-key", key_path, "-out", csr_path, "-subj", f"/CN={common_name}"],
            timeout=timeout,
        )
        if rc2 != 0:
            return False, f"產生憑證簽署請求(CSR)失敗: {err2}"

        x509_args = [
            "openssl", "x509", "-req", "-in", csr_path,
            "-CA", scep_ca_cert_path, "-CAkey", scep_ca_key_path,
            "-CAcreateserial", "-CAserial", srl_path,
            "-out", cert_path, "-days", str(days), "-sha256",
        ]
        run_env = None
        if ca_key_password:
            x509_args += ["-passin", "env:NANOMDM_WOOFWEB_SCEP_CA_PASS"]
            run_env = dict(os.environ)
            run_env["NANOMDM_WOOFWEB_SCEP_CA_PASS"] = ca_key_password

        rc3, _, err3 = utils.run_cmd(x509_args, timeout=timeout, env=run_env)
        if rc3 != 0:
            return False, f"用 SCEP CA 簽署憑證失敗: {err3}"

        try:
            with open(key_path, "rb") as f:
                key_bytes = f.read()
            with open(cert_path, "rb") as f:
                cert_bytes = f.read()
        except OSError as e:
            return False, f"讀取產生的憑證/私鑰失敗: {e}"

        os.makedirs(os.path.dirname(out_key_path), exist_ok=True)
        os.makedirs(os.path.dirname(out_cert_path), exist_ok=True)

        # 私鑰檔案權限收緊成只有擁有者能讀寫,不要讓其他系統使用者也能讀到
        tmp_key_out = out_key_path + ".tmp"
        with open(tmp_key_out, "wb") as f:
            f.write(key_bytes)
        os.chmod(tmp_key_out, 0o600)
        os.replace(tmp_key_out, out_key_path)

        tmp_cert_out = out_cert_path + ".tmp"
        with open(tmp_cert_out, "wb") as f:
            f.write(cert_bytes)
        os.replace(tmp_cert_out, out_cert_path)

    return True, "簽署憑證已產生,有效期 10 年"


def sign_plist_bytes(plist_bytes, signing_cert_path, signing_key_path, ca_cert_path=None, timeout=30):
    """用openssl smime把plist內容簽署成PKCS#7格式(DER編碼),裝置安裝時會顯示「已驗證」,
    前提是裝置本身信任簽署這張憑證的CA(這裡用的是我們自己的SCEP CA,裝置透過DEP註冊時
    就已經信任這個CA了)。

    重要:openssl smime -outform der的輸出是二進位資料(不是純文字的plist了),
    不能用文字模式的subprocess處理(text=True會因為編碼轉換corrupt掉二進位內容),
    這裡改用暫存檔案做輸入輸出,確保二進位資料完整不失真。

    回傳 (signed_bytes或None, error訊息或None)。
    """
    if not (os.path.exists(signing_cert_path) and os.path.exists(signing_key_path)):
        return None, "簽署憑證或私鑰不存在,請先到憑證管理頁面產生簽署憑證"

    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, "profile.mobileconfig")
        out_path = os.path.join(tmpdir, "profile-signed.mobileconfig")

        try:
            with open(in_path, "wb") as f:
                f.write(plist_bytes)
        except OSError as e:
            return None, f"寫入暫存檔案失敗: {e}"

        args = [
            "openssl", "smime", "-sign",
            "-signer", signing_cert_path,
            "-inkey", signing_key_path,
        ]
        if ca_cert_path and os.path.exists(ca_cert_path):
            # 把CA憑證一起包進簽署鏈裡,讓裝置驗證時能找到完整的信任鏈,
            # 不是只有簽署者憑證本身
            args += ["-certfile", ca_cert_path]
        args += [
            "-nodetach", "-outform", "der",
            "-in", in_path, "-out", out_path,
        ]

        rc, out, err = utils.run_cmd(args, timeout=timeout)
        if rc != 0:
            return None, f"簽署失敗: {err or out}"

        try:
            with open(out_path, "rb") as f:
                signed_bytes = f.read()
        except OSError as e:
            return None, f"讀取簽署結果失敗: {e}"

    if not signed_bytes:
        return None, "簽署後的檔案是空的,openssl可能沒有正確產生輸出"

    return signed_bytes, None


def extract_plist_from_signed_bytes(signed_bytes, timeout=15):
    """從PKCS#7簽署過的內容(DER編碼)裡取出原始的plist內容。用openssl smime -verify
    做這件事(這個指令本來的用途就是「驗證簽署+取出原始內容」一次做完,不是只驗證不取內容)。

    重要:這裡的目的是「讓管理者能夠重新開啟、編輯自己系統產生的描述檔」,不是嚴格的
    安全驗證關卡,所以用-noverify跳過憑證信任鏈驗證,只做PKCS#7格式的內容取出——
    就算簽署憑證已經過期、或CA鏈驗證有問題,還是要能正常取出內容繼續編輯,不應該因為
    驗證失敗就完全無法讀取自己系統產生的檔案。

    回傳 (plist_bytes或None, error或None)。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, "signed.der")
        out_path = os.path.join(tmpdir, "extracted.plist")
        try:
            with open(in_path, "wb") as f:
                f.write(signed_bytes)
        except OSError as e:
            return None, f"寫入暫存檔案失敗: {e}"

        args = [
            "openssl", "smime", "-verify", "-noverify",
            "-inform", "der", "-in", in_path, "-out", out_path,
        ]
        rc, out, err = utils.run_cmd(args, timeout=timeout)
        if rc != 0:
            return None, err or out or "openssl smime -verify 執行失敗"

        try:
            with open(out_path, "rb") as f:
                plist_bytes = f.read()
        except OSError as e:
            return None, f"讀取取出結果失敗: {e}"

    if not plist_bytes:
        return None, "取出的內容是空的"

    return plist_bytes, None


def is_signed_mobileconfig_bytes(raw_bytes):
    """單純判斷這份.mobileconfig的原始bytes是不是簽署過的格式,不用實際解開簽署層
    (只是要判斷「是不是」的話,檢查開頭就夠了,不需要花時間真的呼叫openssl解開內容)。
    純文字/二進位plist格式(沒有簽署)開頭會是<?xml或bplist,簽署過的PKCS#7格式不會。
    """
    stripped = raw_bytes.lstrip()
    return not (stripped.startswith(b"<?xml") or stripped.startswith(b"bplist"))


def parse_mobileconfig_bytes(raw_bytes):
    """給定一份.mobileconfig檔案的原始bytes(可能是純文字/二進位plist,
    也可能是簽署過的PKCS#7格式),自動偵測格式並回傳解析後的plist dict。
    給所有需要讀取.mobileconfig檔案內容的地方共用,集中處理「這份檔案有沒有
    被簽署過」這件事,不用每個讀取的地方各自判斷、容易漏改。

    找不到對應格式或解析失敗時,拋出ValueError,呼叫端維持原本except Exception
    的錯誤處理方式即可,不需要另外特別處理。
    """
    import plistlib

    stripped = raw_bytes.lstrip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"bplist"):
        # 純文字plist或二進位plist格式,不是簽署過的,直接解析
        return plistlib.loads(raw_bytes)

    # 不是plist格式,嘗試當作簽署過的PKCS#7內容,先解開簽署層拿到裡面的原始plist
    plist_bytes, err = extract_plist_from_signed_bytes(raw_bytes)
    if plist_bytes is None:
        raise ValueError(f"無法解析檔案內容(既不是有效的plist格式,也無法當作簽署過的內容解開): {err}")
    return plistlib.loads(plist_bytes)

