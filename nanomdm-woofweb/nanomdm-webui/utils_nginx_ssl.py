"""nginx HTTPS 憑證切換:Let's Encrypt / ZeroSSL / Let's Encrypt 測試環境 / 手動上傳(例如萬用字元憑證)。

做法:
  nginx 的 ssl_certificate / ssl_certificate_key 固定指向 /etc/nginx/ssl/current/ 底下的
  fullchain.pem / privkey.pem,current 是一個符號連結,指向「certbot 的 live 目錄」或「手動上傳的目錄」。
  切換憑證只要改這個連結再 reload nginx,不用每次修改 nginx 設定檔。

  第一次使用時會做一次性的轉換(migrate):
    - 備份 nginx 站台設定檔,把 ssl_certificate 兩行改成指向 current
    - certbot 續期設定的 installer 改成 None(不然 certbot 續期時會把 nginx 設定改回 live 路徑),
      並加上 renew_hook = systemctl reload nginx(續期後讓 nginx 載入新憑證)
    - nginx -t 通過才 reload,失敗就還原

  每次切換:先備份連結 -> 切換 -> nginx -t -> reload -> 實際用 TLS 連線確認 nginx 送出的就是新憑證,
  任何一步失敗就自動切回原本的憑證。用 reload 不用 restart,WebUI 本身也是透過 nginx 提供,不能中斷。
"""
import configparser
import datetime
import glob
import os
import re
import shutil
import subprocess
import tempfile
import time

SSL_ROOT = "/etc/nginx/ssl"
CURRENT_LINK = os.path.join(SSL_ROOT, "current")
CURRENT_FULLCHAIN = os.path.join(CURRENT_LINK, "fullchain.pem")
CURRENT_PRIVKEY = os.path.join(CURRENT_LINK, "privkey.pem")
MANUAL_ROOT = os.path.join(SSL_ROOT, "manual")
MANUAL_KEEP = 3
NGINX_SITES = "/etc/nginx/sites-enabled"
LE_LIVE = "/etc/letsencrypt/live"
LE_RENEWAL = "/etc/letsencrypt/renewal"
CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"
STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".nginx_ssl")

ACME_SERVERS = {
    "letsencrypt": "https://acme-v02.api.letsencrypt.org/directory",
    "staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
    "zerossl": "https://acme.zerossl.com/v2/DV90",
}
MODE_LABELS = {
    "letsencrypt": "Let's Encrypt",
    "zerossl": "ZeroSSL",
    "staging": "Let's Encrypt 測試環境",
    "manual": "手動上傳",
    "unknown": "無法判斷",
}

_SSL_CERT_RE = re.compile(r"^(\s*)ssl_certificate(\s+)(\S+?);.*$", re.M)
_SSL_KEY_RE = re.compile(r"^(\s*)ssl_certificate_key(\s+)(\S+?);.*$", re.M)


class SSLError(Exception):
    pass


# ---------------------------------------------------------------------------
# 基本工具
# ---------------------------------------------------------------------------
def _run(args, timeout=30, input_text=None):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              input=input_text, stdin=None if input_text is not None else subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, "", f"執行逾時({timeout} 秒)"
    except Exception as e:
        return -1, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def _ts():
    return time.strftime("%Y%m%d_%H%M%S")


def _nginx_test_and_reload():
    rc, out, err = _run(["nginx", "-t"])
    if rc != 0:
        return False, (err or out).strip()
    rc, out, err = _run(["systemctl", "reload", "nginx"])
    if rc != 0:
        return False, (err or out).strip() or "nginx reload 失敗"
    return True, None


def find_site_conf():
    """找出有設定 ssl_certificate 的 nginx 站台設定檔。回傳 dict 或拋出 SSLError。"""
    candidates = []
    for path in sorted(glob.glob(os.path.join(NGINX_SITES, "*"))):
        real = os.path.realpath(path)
        try:
            with open(real, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        cert = _SSL_CERT_RE.search(text)
        key = _SSL_KEY_RE.search(text)
        if cert and key:
            names = re.findall(r"^\s*server_name\s+([^;]+);", text, re.M)
            domain = names[0].split()[0] if names else ""
            candidates.append({"path": real, "domain": domain,
                               "cert_path": cert.group(3), "key_path": key.group(3)})
    if not candidates:
        raise SSLError(f"在 {NGINX_SITES} 找不到設定 ssl_certificate 的站台設定檔")
    if len(candidates) > 1:
        raise SSLError("有多個站台設定檔都設定了 ssl_certificate,無法判斷要調整哪一個: "
                       + "、".join(c["path"] for c in candidates))
    return candidates[0]


# ---------------------------------------------------------------------------
# 憑證資訊
# ---------------------------------------------------------------------------
def _split_pem(text):
    return re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----\s*", text, re.S)


def cert_info(fullchain_path):
    """讀取憑證資訊(主體、簽發者、涵蓋網域、到期日、SHA-256 指紋、是否受公開信任)。"""
    rc, out, err = _run(["openssl", "x509", "-in", fullchain_path, "-noout", "-subject", "-issuer",
                         "-enddate", "-fingerprint", "-sha256", "-ext", "subjectAltName",
                         "-nameopt", "utf8,sep_comma_plus_space"])
    if rc != 0:
        return {"ok": False, "error": (err or out).strip() or "無法讀取憑證"}
    info = {"ok": True, "sans": []}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("subject="):
            info["subject"] = line[len("subject="):].strip()
        elif line.startswith("issuer="):
            info["issuer"] = line[len("issuer="):].strip()
        elif line.startswith("notAfter="):
            end = datetime.datetime.strptime(line[len("notAfter="):].strip(), "%b %d %H:%M:%S %Y %Z")
            end = end.replace(tzinfo=datetime.timezone.utc)
            info["not_after"] = end.astimezone().strftime("%Y-%m-%d %H:%M")
            info["days_left"] = (end - datetime.datetime.now(datetime.timezone.utc)).days
        elif "Fingerprint=" in line:
            info["fingerprint"] = line.split("=", 1)[1].strip().upper()
        elif "DNS:" in line:
            info["sans"] = [p.strip()[4:] for p in line.split(",") if p.strip().startswith("DNS:")]
    info["trusted"], info["trust_error"] = verify_trusted(fullchain_path)
    return info


def verify_trusted(fullchain_path):
    """用系統信任的根憑證驗證這條憑證鏈(iPad 也是信任這些公開 CA)。"""
    try:
        with open(fullchain_path, encoding="utf-8") as f:
            certs = _split_pem(f.read())
    except OSError as e:
        return False, str(e)
    if not certs:
        return False, "檔案裡沒有憑證"
    with tempfile.TemporaryDirectory() as tmp:
        leaf = os.path.join(tmp, "leaf.pem")
        chain = os.path.join(tmp, "chain.pem")
        with open(leaf, "w") as f:
            f.write(certs[0])
        args = ["openssl", "verify", "-CAfile", CA_BUNDLE]
        if len(certs) > 1:
            with open(chain, "w") as f:
                f.write("".join(certs[1:]))
            args += ["-untrusted", chain]
        rc, out, err = _run(args + [leaf])
    if rc == 0:
        return True, None
    msg = (err or out).strip().splitlines()
    return False, msg[-1] if msg else "驗證失敗"


def covers_domain(sans, domain):
    domain = domain.lower()
    for san in sans:
        san = san.lower()
        if san == domain:
            return True
        if san.startswith("*.") and domain.count(".") >= 2 and domain.split(".", 1)[1] == san[2:]:
            return True
    return False


# ---------------------------------------------------------------------------
# 狀態
# ---------------------------------------------------------------------------
def _renewal_conf(lineage):
    return os.path.join(LE_RENEWAL, f"{lineage}.conf")


def _read_renewal(lineage):
    cp = configparser.ConfigParser(interpolation=None)
    path = _renewal_conf(lineage)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        cp.read_string("[top]\n" + f.read())
    return cp


def _mode_of_dir(cert_dir):
    """依憑證目錄判斷模式。回傳 (mode, lineage)。"""
    cert_dir = os.path.normpath(cert_dir)
    if cert_dir.startswith(LE_LIVE + "/"):
        lineage = os.path.basename(cert_dir)
        cp = _read_renewal(lineage)
        server = cp.get("renewalparams", "server", fallback="") if cp else ""
        for mode, url in ACME_SERVERS.items():
            if server.rstrip("/") == url.rstrip("/"):
                return mode, lineage
        if "zerossl" in server:
            return "zerossl", lineage
        if "staging" in server:
            return "staging", lineage
        return ("letsencrypt" if server else "unknown"), lineage
    if cert_dir.startswith(MANUAL_ROOT + "/"):
        return "manual", None
    return "unknown", None


def status():
    try:
        site = find_site_conf()
    except SSLError as e:
        return {"ok": False, "error": str(e)}
    migrated = site["cert_path"] == CURRENT_FULLCHAIN and site["key_path"] == CURRENT_PRIVKEY
    if migrated:
        active_dir = os.path.realpath(CURRENT_LINK) if os.path.islink(CURRENT_LINK) else ""
        # live 目錄裡的檔案本身也是連結,判斷模式要用 live 目錄,不能用 realpath 之後的 archive 目錄
        link_target = os.readlink(CURRENT_LINK) if os.path.islink(CURRENT_LINK) else ""
        mode_dir = link_target if os.path.isabs(link_target) else active_dir
    else:
        mode_dir = os.path.dirname(site["cert_path"])
    mode, lineage = _mode_of_dir(mode_dir)
    info = cert_info(CURRENT_FULLCHAIN if migrated else site["cert_path"])
    cp = _read_renewal(lineage) if lineage else None
    return {
        "ok": True,
        "domain": site["domain"],
        "site_conf": site["path"],
        "migrated": migrated,
        "cert_dir": mode_dir,
        "mode": mode,
        "mode_label": MODE_LABELS.get(mode, mode),
        "lineage": lineage,
        "auto_renew": bool(lineage) and mode != "manual",
        "renewal_installer": cp.get("renewalparams", "installer", fallback=None) if cp else None,
        "cert": info,
        "covers_domain": covers_domain(info.get("sans", []), site["domain"]) if info.get("ok") else False,
    }


# ---------------------------------------------------------------------------
# 一次性轉換
# ---------------------------------------------------------------------------
def _backup_file(path, label):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    dest = os.path.join(STATE_DIR, f"{_ts()}_{label}")
    shutil.copy2(path, dest)
    return dest


def _neutralize_renewal(lineage):
    """certbot 續期時不要動 nginx 設定(installer = None),續期後 reload nginx 載入新憑證。"""
    path = _renewal_conf(lineage)
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = re.sub(r"^installer\s*=.*$", "installer = None", text, flags=re.M)
    if not re.search(r"^renew_hook\s*=", new, re.M):
        new = re.sub(r"^\[renewalparams\]\s*$", "[renewalparams]\nrenew_hook = systemctl reload nginx", new,
                     count=1, flags=re.M)
    if new != text:
        _backup_file(path, f"renewal-{lineage}.conf")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(path + ".tmp", path)


def _point_link(target_dir):
    os.makedirs(SSL_ROOT, mode=0o755, exist_ok=True)
    tmp = CURRENT_LINK + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target_dir, tmp)
    os.replace(tmp, CURRENT_LINK)


def migrate():
    """把 nginx 改成使用 /etc/nginx/ssl/current。已經轉換過就直接回傳。回傳 (changed, message)。"""
    site = find_site_conf()
    if site["cert_path"] == CURRENT_FULLCHAIN and site["key_path"] == CURRENT_PRIVKEY:
        return False, "已經使用固定憑證路徑"
    cert_dir = os.path.dirname(site["cert_path"])
    if (os.path.dirname(site["key_path"]) != cert_dir
            or not os.path.isfile(os.path.join(cert_dir, "fullchain.pem"))
            or not os.path.isfile(os.path.join(cert_dir, "privkey.pem"))):
        raise SSLError(f"目前的憑證路徑({site['cert_path']}、{site['key_path']})不是 certbot 的標準格式,"
                       "無法自動轉換,請手動調整 nginx 設定")
    backup = _backup_file(site["path"], "nginx-site.conf")
    old_link = os.readlink(CURRENT_LINK) if os.path.islink(CURRENT_LINK) else None
    _point_link(cert_dir)
    with open(site["path"], encoding="utf-8") as f:
        text = f.read()
    text = _SSL_CERT_RE.sub(lambda m: f"{m.group(1)}ssl_certificate{m.group(2)}{CURRENT_FULLCHAIN}; # managed by nanomdm-webui",
                            text, count=1)
    text = _SSL_KEY_RE.sub(lambda m: f"{m.group(1)}ssl_certificate_key{m.group(2)}{CURRENT_PRIVKEY}; # managed by nanomdm-webui",
                           text, count=1)
    with open(site["path"] + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    shutil.copymode(site["path"], site["path"] + ".tmp")
    os.replace(site["path"] + ".tmp", site["path"])
    ok, err = _nginx_test_and_reload()
    if not ok:
        shutil.copy2(backup, site["path"])
        if old_link:
            _point_link(old_link)
        _nginx_test_and_reload()
        raise SSLError(f"轉換後 nginx 設定測試失敗,已還原: {err}")
    mode, lineage = _mode_of_dir(cert_dir)
    if lineage:
        _neutralize_renewal(lineage)
    return True, f"nginx 已改用 {CURRENT_FULLCHAIN}(原設定備份在 {backup})"


# ---------------------------------------------------------------------------
# 切換與確認
# ---------------------------------------------------------------------------
def served_fingerprint(domain):
    """實際用 TLS 連線到本機 443,取得 nginx 目前送出的憑證指紋。"""
    rc, out, err = _run(["openssl", "s_client", "-connect", "127.0.0.1:443", "-servername", domain],
                        timeout=15, input_text="")
    certs = _split_pem(out)
    if not certs:
        return None
    rc, out2, err2 = _run(["openssl", "x509", "-noout", "-fingerprint", "-sha256"], input_text=certs[0])
    return out2.strip().split("=", 1)[1].upper() if rc == 0 and "=" in out2 else None


def _switch_and_verify(target_dir, domain):
    """把 current 指向 target_dir,reload 後確認 nginx 送出的是新憑證;失敗就切回原本的。"""
    expected = cert_info(os.path.join(target_dir, "fullchain.pem")).get("fingerprint")
    old_link = os.readlink(CURRENT_LINK) if os.path.islink(CURRENT_LINK) else None
    _point_link(target_dir)
    ok, err = _nginx_test_and_reload()
    if ok:
        for _ in range(10):
            time.sleep(1)
            if served_fingerprint(domain) == expected:
                return True, None
        err = "nginx 送出的憑證跟預期的不一樣"
    if old_link:
        _point_link(old_link)
        _nginx_test_and_reload()
    return False, f"{err},已切回原本的憑證"


# ---------------------------------------------------------------------------
# ACME(Let's Encrypt / ZeroSSL / 測試環境)
# ---------------------------------------------------------------------------
def request_acme(mode, email="", eab_kid="", eab_hmac=""):
    """用 certbot 申請憑證並切換過去。回傳 dict(ok, message, output)。"""
    if mode not in ACME_SERVERS:
        raise SSLError("不支援的憑證模式")
    st = status()
    if not st["ok"]:
        raise SSLError(st["error"])
    domain = st["domain"]
    migrate()
    lineage = st["lineage"] or _default_lineage(domain)

    cmd = ["certbot", "certonly", "--nginx", "-d", domain, "--cert-name", lineage,
           "--non-interactive", "--agree-tos", "--force-renewal",
           "--deploy-hook", "systemctl reload nginx", "--server", ACME_SERVERS[mode]]
    cmd += ["-m", email] if email else ["--register-unsafely-without-email"]
    if mode == "zerossl":
        if not eab_kid or not eab_hmac:
            raise SSLError("ZeroSSL 需要填寫 EAB Key ID 與 EAB HMAC Key")
        cmd += ["--eab-kid", eab_kid, "--eab-hmac-key", eab_hmac]
    if mode == "staging":
        cmd.append("--break-my-certs")  # 把正式憑證換成測試憑證時 certbot 要求明確確認
    rc, out, err = _run(cmd, timeout=240)
    output = _redact((out or "") + (err or ""), [eab_kid, eab_hmac])
    if rc != 0:
        return {"ok": False, "message": "certbot 申請失敗,目前使用中的憑證沒有變動", "output": output[-3000:]}

    _neutralize_renewal(lineage)
    ok, err = _switch_and_verify(os.path.join(LE_LIVE, lineage), domain)
    if not ok:
        return {"ok": False, "message": f"憑證已申請,但切換失敗: {err}", "output": output[-3000:]}
    return {"ok": True, "message": f"已改用 {MODE_LABELS[mode]} 憑證", "output": output[-3000:]}


def _default_lineage(domain):
    """目前用的不是 certbot 憑證(例如手動上傳)時,沿用這個網域既有的 certbot 憑證名稱;
    certbot 遇到同名衝突時會建立「網域-0001」這種名稱,有的話用最新的那一個。"""
    names = [os.path.basename(p)[:-5] for p in glob.glob(os.path.join(LE_RENEWAL, f"{domain}*.conf"))]
    names = [n for n in names if n == domain or re.fullmatch(re.escape(domain) + r"-\d{4}", n)]
    return sorted(names)[-1] if names else domain


def _redact(text, secrets):
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


# ---------------------------------------------------------------------------
# 手動上傳
# ---------------------------------------------------------------------------
def validate_manual(cert_text, key_text, chain_text, domain):
    """檢查上傳的憑證。回傳 (result, staged_dir)。staged_dir 只有在全部通過時才不是 None,
    呼叫端用完要交給 apply_manual 或 discard_staged 處理。"""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    certs = _split_pem(cert_text or "") + _split_pem(chain_text or "")
    if not certs:
        add("憑證格式", False, "沒有找到 PEM 格式的憑證(-----BEGIN CERTIFICATE-----)")
        return {"ok": False, "checks": checks}, None
    add("憑證格式", True, f"共 {len(certs)} 張憑證(第 1 張是網站憑證,其餘是中繼憑證)")
    if not key_text or "PRIVATE KEY-----" not in key_text:
        add("私鑰格式", False, "沒有找到 PEM 格式的私鑰")
        return {"ok": False, "checks": checks}, None
    if "ENCRYPTED" in key_text:
        add("私鑰格式", False, "私鑰有設定密碼,nginx 無法直接使用,請先解除密碼後再上傳")
        return {"ok": False, "checks": checks}, None

    os.makedirs(MANUAL_ROOT, mode=0o700, exist_ok=True)
    staged = tempfile.mkdtemp(prefix=".upload-", dir=MANUAL_ROOT)
    fullchain = os.path.join(staged, "fullchain.pem")
    privkey = os.path.join(staged, "privkey.pem")
    with open(fullchain, "w", encoding="utf-8") as f:
        f.write("".join(c.strip() + "\n" for c in certs))
    fd = os.open(privkey, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key_text.strip() + "\n")

    rc, key_pub, err = _run(["openssl", "pkey", "-in", privkey, "-pubout"])
    add("私鑰格式", rc == 0, "" if rc == 0 else (err.strip() or "無法讀取私鑰"))
    rc2, cert_pub, err2 = _run(["openssl", "x509", "-in", fullchain, "-pubkey", "-noout"])
    add("憑證與私鑰是一對", rc == 0 and rc2 == 0 and key_pub.strip() == cert_pub.strip(),
        "" if key_pub.strip() == cert_pub.strip() else "私鑰跟網站憑證不是一對")

    info = cert_info(fullchain)
    if not info.get("ok"):
        add("讀取憑證", False, info.get("error", ""))
    else:
        add("涵蓋網域", covers_domain(info["sans"], domain),
            f"憑證涵蓋: {'、'.join(info['sans']) or '(無)'};本台網域: {domain}")
        days = info.get("days_left", -1)
        add("有效期限", days >= 0, f"到期日 {info.get('not_after')}(剩 {days} 天)"
            + (",即將到期" if 0 <= days < 30 else ""))
        add("憑證鏈受公開信任", info["trusted"],
            "iPad 可以信任這份憑證" if info["trusted"]
            else f"{info.get('trust_error')};常見原因是少了中繼憑證,請上傳完整憑證鏈(fullchain)")
    ok = all(c["ok"] for c in checks)
    result = {"ok": ok, "checks": checks, "cert": info if info.get("ok") else None}
    if not ok:
        shutil.rmtree(staged, ignore_errors=True)
        return result, None
    return result, staged


def discard_staged(staged):
    if staged and os.path.basename(staged).startswith(".upload-"):
        shutil.rmtree(staged, ignore_errors=True)


def apply_manual(cert_text, key_text, chain_text):
    st = status()
    if not st["ok"]:
        raise SSLError(st["error"])
    domain = st["domain"]
    result, staged = validate_manual(cert_text, key_text, chain_text, domain)
    if not staged:
        return {"ok": False, "message": "憑證檢查沒有通過,目前使用中的憑證沒有變動", "validation": result}
    final = os.path.join(MANUAL_ROOT, _ts())
    os.rename(staged, final)
    os.chmod(final, 0o700)
    try:
        migrate()
        ok, err = _switch_and_verify(final, domain)
    except Exception:
        shutil.rmtree(final, ignore_errors=True)
        raise
    if not ok:
        shutil.rmtree(final, ignore_errors=True)
        return {"ok": False, "message": f"切換失敗: {err}", "validation": result}
    _prune_manual(keep=final)
    return {"ok": True, "message": "已改用手動上傳的憑證", "validation": result}


def _prune_manual(keep):
    dirs = sorted(d for d in glob.glob(os.path.join(MANUAL_ROOT, "[0-9]*")) if os.path.isdir(d))
    active = os.path.realpath(CURRENT_LINK) if os.path.islink(CURRENT_LINK) else ""
    for d in dirs[:-MANUAL_KEEP]:
        if d not in (keep, active):
            shutil.rmtree(d, ignore_errors=True)
