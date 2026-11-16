"""APNs 推播憑證的私鑰與 CSR:在伺服器上產生,讓使用者不必自己執行 openssl。

流程:
1. generate() 產生 push.key(私鑰,只存在伺服器上)與 push.csr
2. 使用者把 push.csr 拿到 mdmcert.download 簽署,再到 Apple Push Certificates Portal 換成 .pem
3. 上傳 .pem 時 find_key_for_cert() 用公鑰比對找到配對的私鑰,上傳成功後 mark_used()

私鑰保存規則:
- 存放在 WebUI 目錄下的 .apns_csr/<時間>/,資料夾 700、私鑰 600,不放進 scep-depot
- 已使用的私鑰保留到同一個 Topic 下一次續約成功才刪除(nanomdm 資料庫裡也有一份)
- 還沒使用的(等待上傳憑證)最多保留 3 份,超過 30 天自動清除
"""
import datetime
import json
import os
import re
import shutil
import subprocess

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".apns_csr")
PENDING_KEEP = 3
PENDING_MAX_DAYS = 30
CSR_CN = "MDM APNs Push"

_ID_RE = re.compile(r"^\d{8}_\d{6}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _run(args, input_text=None, timeout=30):
    try:
        proc = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return 1, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def _entry_dir(entry_id):
    if not entry_id or not _ID_RE.match(entry_id):
        return None
    path = os.path.join(STATE_DIR, entry_id)
    return path if os.path.isdir(path) else None


def _read_meta(path):
    try:
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_meta(path, meta):
    tmp = os.path.join(path, "meta.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(path, "meta.json"))


def _pubkey_from_key(key_pem):
    rc, out, _ = _run(["openssl", "pkey", "-pubout"], key_pem)
    return out.strip() if rc == 0 else None


def pubkey_from_cert(cert_pem):
    rc, out, _ = _run(["openssl", "x509", "-noout", "-pubkey"], cert_pem)
    return out.strip() if rc == 0 else None


def _entries():
    """回傳 [(entry_id, path, meta)],新的在前。"""
    if not os.path.isdir(STATE_DIR):
        return []
    result = []
    for name in sorted(os.listdir(STATE_DIR), reverse=True):
        path = os.path.join(STATE_DIR, name)
        if not _ID_RE.match(name) or not os.path.isdir(path):
            continue
        meta = _read_meta(path)
        if meta is None or not os.path.isfile(os.path.join(path, "push.key")):
            continue
        result.append((name, path, meta))
    return result


def prune():
    """清除過期或多餘的「等待上傳憑證」私鑰。已使用的私鑰不在這裡清除。"""
    removed = []
    now = datetime.datetime.now()
    pending = [e for e in _entries() if e[2].get("status") == "pending"]
    for idx, (entry_id, path, meta) in enumerate(pending):
        try:
            created = datetime.datetime.strptime(entry_id, "%Y%m%d_%H%M%S")
        except ValueError:
            created = now
        if idx >= PENDING_KEEP or (now - created).days > PENDING_MAX_DAYS:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(entry_id)
    return removed


def list_entries():
    prune()
    items = []
    for entry_id, _, meta in _entries():
        items.append({
            "id": entry_id,
            "created_at": meta.get("created_at", ""),
            "email": meta.get("email", ""),
            "country": meta.get("country", ""),
            "status": meta.get("status", "pending"),
            "topic": meta.get("topic", ""),
            "not_after": meta.get("not_after", ""),
            "used_at": meta.get("used_at", ""),
        })
    return items


def generate(country, email):
    """產生私鑰與 CSR。回傳 (entry, error)。"""
    country = (country or "").strip().upper()
    email = (email or "").strip()
    if not re.match(r"^[A-Z]{2}$", country):
        return None, "國碼必須是 2 個英文字母,例如 TW"
    if not _EMAIL_RE.match(email):
        return None, "email 格式不正確"

    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    entry_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(STATE_DIR, entry_id)
    if os.path.exists(path):
        return None, "剛剛才產生過一份,請稍候一秒再試"
    os.makedirs(path, mode=0o700)

    key_path = os.path.join(path, "push.key")
    csr_path = os.path.join(path, "push.csr")
    old_umask = os.umask(0o077)
    try:
        rc, out, err = _run([
            "openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
            "-keyout", key_path, "-out", csr_path,
            "-subj", f"/C={country}/CN={CSR_CN}/emailAddress={email}",
        ])
    finally:
        os.umask(old_umask)
    if rc != 0 or not os.path.isfile(key_path) or not os.path.isfile(csr_path):
        shutil.rmtree(path, ignore_errors=True)
        return None, f"產生 CSR 失敗: {(err or out).strip()}"
    os.chmod(key_path, 0o600)
    os.chmod(csr_path, 0o644)

    with open(key_path, encoding="utf-8") as f:
        pubkey = _pubkey_from_key(f.read())
    meta = {
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "country": country, "email": email, "status": "pending", "pubkey": pubkey,
    }
    _write_meta(path, meta)
    prune()
    with open(csr_path, encoding="utf-8") as f:
        csr_pem = f.read()
    return {"id": entry_id, "csr_pem": csr_pem, "created_at": meta["created_at"]}, None


def get_file(entry_id, which):
    """回傳 (檔案路徑, error)。which 是 'csr' 或 'key'。"""
    path = _entry_dir(entry_id)
    if not path:
        return None, "找不到這份 CSR"
    file_path = os.path.join(path, "push.csr" if which == "csr" else "push.key")
    if not os.path.isfile(file_path):
        return None, "檔案不存在"
    return file_path, None


def find_key_for_cert(cert_pem):
    """用憑證的公鑰找出配對的私鑰。回傳 (entry_id, key_pem),找不到回傳 (None, None)。"""
    cert_pub = pubkey_from_cert(cert_pem)
    if not cert_pub:
        return None, None
    for entry_id, path, meta in _entries():
        if meta.get("pubkey") and meta["pubkey"] == cert_pub:
            with open(os.path.join(path, "push.key"), encoding="utf-8") as f:
                return entry_id, f.read()
    return None, None


def key_matches_cert(key_pem, cert_pem):
    key_pub = _pubkey_from_key(key_pem)
    return bool(key_pub) and key_pub == pubkey_from_cert(cert_pem)


def mark_used(entry_id, topic, not_after):
    """上傳成功後標記為使用中,並刪除同一個 Topic 先前使用的私鑰(已被這次續約取代)。"""
    path = _entry_dir(entry_id)
    if not path:
        return []
    meta = _read_meta(path) or {}
    meta.update({
        "status": "used", "topic": topic, "not_after": not_after,
        "used_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _write_meta(path, meta)
    removed = []
    for other_id, other_path, other_meta in _entries():
        if other_id != entry_id and other_meta.get("status") == "used" and other_meta.get("topic") == topic:
            shutil.rmtree(other_path, ignore_errors=True)
            removed.append(other_id)
    return removed


def delete_pending(entry_id):
    """刪除還沒使用的私鑰。使用中的私鑰不能從這裡刪除。"""
    path = _entry_dir(entry_id)
    if not path:
        return False, "找不到這份 CSR"
    meta = _read_meta(path) or {}
    if meta.get("status") == "used":
        return False, "這把私鑰正在使用中,不能刪除"
    shutil.rmtree(path, ignore_errors=True)
    return True, None
