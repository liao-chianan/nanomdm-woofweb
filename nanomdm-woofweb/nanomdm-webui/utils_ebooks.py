# -*- coding: utf-8 -*-
"""電子書(PDF / EPUB)派送到 iPad 的「書籍」App。

流程:
  1. 管理者在 WebUI 上傳檔案,系統檢查格式、產生隨機檔名,存到 EBOOK_FILES_DIR(獨立資料夾)。
  2. iPad 收到 MDM 的 InstallMedia 指令後,自己到 https://<SERVER_DOMAIN>/ebooks/<隨機檔名> 下載。
  3. nginx 把 /ebooks/ 的請求轉給 WebUI 的 /ebook-download/,由 WebUI 檢查來源 IP 是否在
     「電子書下載IP範圍限制」內,通過後回 X-Accel-Redirect,由 nginx 從 internal 位置直接送出檔案。
     nginx 只提供下載(internal + 固定的 Content-Type),不會執行任何東西;IP 規則改了立即生效,
     不需要改 nginx 設定或 reload。

資料:
  EBOOKS_JSON          電子書清單(id、書名、格式、大小、上傳時間)
  webui_config.json    ebooks.max_size_mb / ebooks.ip_allowlist
  groups.json          每個群組的 "ebooks": [id, ...](webhook-server.py 註冊完成時也讀這裡)
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import zipfile

import config
import utils_auth
import utils_nginx_ssl

EBOOK_ROOT = "/opt/nanomdm-deployment/ebooks"
EBOOK_FILES_DIR = os.path.join(EBOOK_ROOT, "files")
EBOOK_TMP_DIR = os.path.join(EBOOK_ROOT, "tmp")
EBOOKS_JSON = os.path.join(EBOOK_ROOT, "ebooks.json")

PUBLIC_PREFIX = "/ebooks/"                       # iPad 下載用的網址路徑
INTERNAL_PREFIX = "/_woofweb_ebook_files/"       # nginx internal location,外部無法直接存取
DOWNLOAD_ROUTE_PREFIX = "/ebook-download/"       # WebUI 檢查 IP 的路由(nginx 從 /ebooks/ 轉過來)

DEFAULT_MAX_SIZE_MB = 200
MAX_SIZE_MB_UPPER = 2048
KINDS = {"pdf": "PDF", "epub": "EPUB"}
MIME_TYPES = {"pdf": "application/pdf", "epub": "application/epub+zip"}
ID_RE = re.compile(r"^[0-9a-f]{32}$")
TITLE_MAX = 100

NGINX_BEGIN = "# BEGIN nanomdm-webui ebooks"
NGINX_END = "# END nanomdm-webui ebooks"


class EbookError(Exception):
    pass


# ---------------------------------------------------------------------------
# 設定(webui_config.json 的 ebooks 區塊)
# ---------------------------------------------------------------------------
def _read_config():
    with open(config.CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_settings():
    try:
        section = _read_config().get("ebooks") or {}
    except (OSError, ValueError):
        section = {}
    try:
        max_mb = int(section.get("max_size_mb") or DEFAULT_MAX_SIZE_MB)
    except (TypeError, ValueError):
        max_mb = DEFAULT_MAX_SIZE_MB
    return {"max_size_mb": max_mb, "ip_allowlist": list(section.get("ip_allowlist") or [])}


def save_settings(max_size_mb, ip_rules):
    try:
        max_mb = int(max_size_mb)
    except (TypeError, ValueError):
        raise EbookError("檔案大小上限必須是整數(MB)")
    if not 1 <= max_mb <= MAX_SIZE_MB_UPPER:
        raise EbookError(f"檔案大小上限必須介於 1 到 {MAX_SIZE_MB_UPPER} MB")
    try:
        rules = [utils_auth.validate_cidr(r) for r in (ip_rules or []) if r and str(r).strip()]
    except utils_auth.AuthError as e:
        raise EbookError(str(e))
    rules = list(dict.fromkeys(rules))
    cfg = _read_config()
    cfg["ebooks"] = {"max_size_mb": max_mb, "ip_allowlist": rules}
    config.save_config(cfg)
    return {"max_size_mb": max_mb, "ip_allowlist": rules}


def is_download_allowed(ip):
    return utils_auth.is_ip_allowed(ip, get_settings()["ip_allowlist"])


# ---------------------------------------------------------------------------
# 電子書清單
# ---------------------------------------------------------------------------
def _ensure_dirs():
    os.makedirs(EBOOK_FILES_DIR, exist_ok=True)
    os.makedirs(EBOOK_TMP_DIR, mode=0o700, exist_ok=True)
    # nginx(www-data)要能讀檔案,資料夾與檔案設成其他人可讀、不可寫
    os.chmod(EBOOK_ROOT, 0o755)
    os.chmod(EBOOK_FILES_DIR, 0o755)


def list_books():
    if not os.path.exists(EBOOKS_JSON):
        return []
    try:
        with open(EBOOKS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [b for b in data.get("books", []) if ID_RE.match(b.get("id", ""))]


def _save_books(books):
    _ensure_dirs()
    tmp = EBOOKS_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"books": books}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, EBOOKS_JSON)


def get_book(book_id):
    return next((b for b in list_books() if b["id"] == book_id), None)


def file_name(book):
    return f"{book['id']}.{book['kind']}"


def file_path(book):
    return os.path.join(EBOOK_FILES_DIR, file_name(book))


def persistent_id(book):
    """InstallMedia / RemoveMedia 用來辨識同一本書的 ID,一本書固定不變"""
    return f"woofweb.ebook.{book['id']}"


def clean_title(title):
    title = re.sub(r"[\x00-\x1f\x7f]", "", str(title or "")).strip()
    if not title:
        raise EbookError("請輸入書名")
    if len(title) > TITLE_MAX:
        raise EbookError(f"書名最多 {TITLE_MAX} 個字")
    return title


# ---------------------------------------------------------------------------
# 格式檢查:看檔案內容,不看副檔名
# ---------------------------------------------------------------------------
def detect_kind(path):
    with open(path, "rb") as f:
        head = f.read(1024)
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 2048))
        tail = f.read()
    if b"%PDF-" in head:
        if b"%%EOF" not in tail:
            raise EbookError("PDF 檔案不完整(找不到檔案結尾標記),可能上傳中斷或檔案已損毀")
        return "pdf"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if not names or names[0] != "mimetype" or z.read("mimetype").strip() != b"application/epub+zip":
                    raise EbookError("這是 ZIP 檔,但不是 EPUB 電子書")
                if "META-INF/container.xml" not in names:
                    raise EbookError("EPUB 檔案缺少 META-INF/container.xml,格式不完整")
        except zipfile.BadZipFile:
            raise EbookError("EPUB 檔案已損毀,無法讀取")
        return "epub"
    raise EbookError("只接受 PDF 或 EPUB 電子書,這個檔案的內容不是這兩種格式")


def save_upload(stream, original_filename, title):
    """把上傳的檔案邊讀邊寫到暫存檔,超過大小上限立即中止;通過格式檢查後才搬進正式資料夾。"""
    title = clean_title(title)
    max_bytes = get_settings()["max_size_mb"] * 1024 * 1024
    _ensure_dirs()
    book_id = secrets.token_hex(16)
    tmp_path = os.path.join(EBOOK_TMP_DIR, book_id + ".part")
    sha = hashlib.sha256()
    size = 0
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise EbookError(f"檔案超過大小上限 {max_bytes // 1024 // 1024} MB")
                sha.update(chunk)
                out.write(chunk)
        if size == 0:
            raise EbookError("檔案是空的")
        kind = detect_kind(tmp_path)
        digest = sha.hexdigest()
        dup = next((b for b in list_books() if b.get("sha256") == digest), None)
        if dup:
            raise EbookError(f"這個檔案已經上傳過了(書名:{dup['title']})")
        book = {
            "id": book_id,
            "title": title,
            "kind": kind,
            "size": size,
            "sha256": digest,
            "original_filename": os.path.basename(str(original_filename or ""))[:200],
            "uploaded_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, file_path(book))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    books = list_books()
    books.append(book)
    _save_books(books)
    return book


def rename_book(book_id, title):
    title = clean_title(title)
    books = list_books()
    for b in books:
        if b["id"] == book_id:
            b["title"] = title
            _save_books(books)
            return b
    raise EbookError("找不到這本電子書")


def delete_book(book_id, groups):
    """還有群組綁定時拒絕刪除(要先到群組取消勾選,決定裝置上的書要不要一起移除)。"""
    bound = sorted(name for name, info in groups.items() if book_id in (info.get("ebooks") or []))
    if bound:
        raise EbookError(f"這本電子書還綁定在群組:{'、'.join(bound)}。請先到群組一覽取消勾選,再刪除。")
    books = list_books()
    book = next((b for b in books if b["id"] == book_id), None)
    if not book:
        raise EbookError("找不到這本電子書")
    path = file_path(book)
    if os.path.exists(path):
        os.remove(path)
    _save_books([b for b in books if b["id"] != book_id])
    return book


def resolve_public_file(name):
    """/ebooks/<id>.<kind> -> 電子書資料;名稱格式不對或不在清單裡回傳 None"""
    m = re.match(r"^([0-9a-f]{32})\.(pdf|epub)$", name or "")
    if not m:
        return None
    book = get_book(m.group(1))
    if not book or book["kind"] != m.group(2) or not os.path.exists(file_path(book)):
        return None
    return book


# ---------------------------------------------------------------------------
# MDM 指令參數
# ---------------------------------------------------------------------------
def public_url(book, domain):
    return f"https://{domain}{PUBLIC_PREFIX}{file_name(book)}"


def install_media_params(book, domain):
    return {
        "MediaType": "Book",
        "MediaURL": public_url(book, domain),
        "PersistentID": persistent_id(book),
        "Kind": book["kind"],
        "Version": "1",
        "Title": book["title"],
    }


def remove_media_params(book):
    return {"MediaType": "Book", "PersistentID": persistent_id(book)}


# ---------------------------------------------------------------------------
# nginx:/ebooks/ 下載路徑、internal 檔案位置、上傳路徑的大小限制
# ---------------------------------------------------------------------------
def nginx_snippet(webui_port):
    return f"""    {NGINX_BEGIN} (managed by nanomdm-webui)
    # iPad 下載電子書:先交給 WebUI 檢查來源 IP,通過後由下面的 internal 位置送出檔案
    location ^~ {PUBLIC_PREFIX} {{
        proxy_pass http://127.0.0.1:{webui_port}{DOWNLOAD_ROUTE_PREFIX};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
    # 只接受 WebUI 回傳的 X-Accel-Redirect,外部直接連這個路徑會得到 404;只提供下載,不執行任何東西
    location ^~ {INTERNAL_PREFIX} {{
        internal;
        alias {EBOOK_FILES_DIR}/;
        autoindex off;
        types {{ application/pdf pdf; application/epub+zip epub; }}
        default_type application/octet-stream;
        add_header X-Content-Type-Options nosniff always;
        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    }}
    # 上傳電子書:檔案大小上限由 WebUI 的「電子書下載IP範圍限制」設定控制,nginx 這裡不另外限制
    location = /miniweb/api/ebooks/upload {{
        client_max_body_size 0;
        proxy_pass http://127.0.0.1:{webui_port}/api/ebooks/upload;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /miniweb;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }}
    {NGINX_END}
"""


def nginx_status():
    try:
        site = utils_nginx_ssl.find_site_conf()
    except Exception as e:
        return {"installed": False, "site_path": None, "error": str(e)}
    with open(site["path"], encoding="utf-8") as f:
        text = f.read()
    return {"installed": NGINX_BEGIN in text and NGINX_END in text, "site_path": site["path"], "error": None}


def install_nginx_snippet(webui_port):
    """把電子書的 location 加進 nginx 站台設定(放在 listen 443 前面)。
    先備份,nginx -t 通過才 reload,失敗就還原。已經加過就不重複加。"""
    site = utils_nginx_ssl.find_site_conf()
    path = site["path"]
    with open(path, encoding="utf-8") as f:
        original = f.read()
    if NGINX_BEGIN in original:
        return False, "nginx 已經有電子書下載設定,不需要再加入"
    m = re.search(r"^[ \t]*listen[ \t]+(\[::\]:)?443[^;]*;", original, re.M)
    if not m:
        raise EbookError(f"在 {path} 找不到 listen 443 這一行,無法自動加入,請手動調整 nginx 設定")
    new_text = original[:m.start()] + nginx_snippet(webui_port) + "\n" + original[m.start():]
    backup = utils_nginx_ssl._backup_file(path, "nginx-site.conf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    ok, err = utils_nginx_ssl._nginx_test_and_reload()
    if not ok:
        shutil.copy2(backup, path)
        utils_nginx_ssl._nginx_test_and_reload()
        raise EbookError(f"加入後 nginx 設定測試失敗,已還原: {err}")
    _ensure_dirs()
    return True, f"已加入電子書下載設定並重新載入 nginx(原設定備份在 {backup})"
