"""
鎖定畫面資訊(Lock Screen 桌布)產生模組

做法說明:
  Apple 的 MDM 沒有「在鎖定畫面正中間顯示一段文字」的設定。唯一能控制文字擺放位置的方式,
  是由伺服器端把文字「畫成一張圖」,再透過 MDM Settings 指令的 Wallpaper 項目
  (監督模式裝置限定)設為鎖定畫面桌布。
  (另一個選項 LockScreenFootnote 的位置是 Apple 決定的,固定在畫面下方小字,不能放在正中間。)

  iPadOS 17 / iOS 16 以後,鎖定畫面與主畫面是「成對」的桌布:即使 Where=1(只設定鎖定畫面),
  系統也會把主畫面一起換成同一張圖。MDM 沒有辦法讀回裝置原本的主畫面桌布,所以沒辦法「還原」,
  只能在鎖定畫面指令之後,再補送一道 Where=2 的指令,把主畫面換成另一張指定的圖
  (純色,或上傳的學校桌布)。設定裡的 home_mode 就是控制這一步:
    color = 主畫面設為純色(預設)
    image = 主畫面設為上傳的圖片
    same  = 不另外設定,主畫面跟鎖定畫面同一張圖

  這個模組同時給兩個地方使用:
    1. webui(app.py):預覽、手動推送、名稱/群組變更後自動推送
    2. webhook-server.py:裝置剛完成註冊時,透過命令列呼叫本檔案產生圖片
       (webhook-server 用系統 python3 執行、沒有 Pillow,所以改用 webui 的 venv 來跑這支程式)
         /opt/nanomdm-webui/venv/bin/python3 /opt/nanomdm-webui/utils_lockscreen.py render <序號>
       成功時 PNG 二進位內容輸出到 stdout,exit code 0;失敗時錯誤訊息輸出到 stderr,exit code 非 0。

  設定檔獨立存放在 /opt/nanomdm-deployment/lockscreen.json(不放在 webui_config.json),
  因為 webhook-server.py 也要讀「是否自動推送」這個開關,兩邊讀同一份檔案才不會不同步。

圖片尺寸與安全範圍:
  iPad 會旋轉,桌布會依照目前方向「等比放大填滿、裁掉超出的部分」。所以圖片做成正方形,
  文字只排在中間的安全範圍內(寬度預設 62%):直向 11 吋 iPad(1668x2388,寬高比約 0.70)
  在正方形圖片上只會看到中間約 70% 的寬度,留一點邊界後取 62%,直向/橫向都不會被裁到。
"""
import csv
import glob
import io
import json
import os
import re
import sys

# 這個模組不 import config/utils,讓命令列模式(webhook-server 呼叫)可以獨立執行,
# 不會因為 webui 其他模組的相依套件或設定檔狀態而失敗。
DEFAULT_SETTINGS_PATH = "/opt/nanomdm-deployment/lockscreen.json"
DEFAULT_DEVICES_CSV = "/opt/nanomdm-deployment/devices.csv"
DEFAULT_HOME_IMAGE_PATH = "/opt/nanomdm-deployment/lockscreen-home.jpg"
HOME_MODES = ("color", "image", "same")

PLACEHOLDERS = {
    "device_name": "裝置名稱",
    "group": "群組",
    "serial": "序號",
    "org_name": "組織名稱",
    "org_address": "地址",
    "org_phone": "電話",
    "org_email": "Email",
}

DEFAULT_SETTINGS = {
    # 自動推送:裝置完成註冊時(webhook-server)、名稱或群組變更時(webui)自動推送鎖定畫面。
    # 預設關閉,避免功能一更新上去就開始改動現場裝置的桌布。
    "auto_push": False,
    # 主畫面處理方式(見檔案開頭說明):color / image / same
    "home_mode": "color",
    "home_color": "#2b2f36",
    "lines": [
        "本裝置：{device_name}    群組：{group}",
        "隸屬組織：{org_name}    聯絡方式：{org_address}    電話：{org_phone}    Email：{org_email}",
    ],
    # 組織資訊:按「從 ASM 帶入」時寫入,也可以手動修改(例如 ASM 上登記的地址格式不想直接顯示)
    "org_name": "",
    "org_address": "",
    "org_phone": "",
    "org_email": "",
    "bg_color": "#1f3a5f",
    "text_color": "#ffffff",
    "band": True,             # 文字後方加一條半透明色帶,讓文字在任何底色上都清楚
    "canvas_size": 2732,      # 正方形邊長(px),2732 = 12.9 吋 iPad Pro 長邊
    "safe_width_ratio": 0.62,
    "title_font_px": 80,      # 第一行字級
    "body_font_px": 60,       # 其餘各行字級
    "min_font_px": 40,        # 一行放不下時,先嘗試在欄位之間換行,真的還放不下才縮小,最小縮到這個字級
    "font_path": "",          # 留空 = 自動尋找系統中文字型
}

# 常見 Linux 發行版的中文字型位置。Noto Sans CJK 優先(Debian/Ubuntu: apt install fonts-noto-cjk)
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
SEGMENT_SPLIT_RE = re.compile(r"\s{2,}|\u3000+")


class LockscreenError(Exception):
    pass


# ---------------------------------------------------------------------------
# 設定檔
# ---------------------------------------------------------------------------
def load_settings(path=DEFAULT_SETTINGS_PATH):
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for k in DEFAULT_SETTINGS:
                    if k in stored:
                        settings[k] = stored[k]
                # 舊版設定檔遷移:where=3(鎖定畫面與主畫面都設定)等同 home_mode=same
                if "home_mode" not in stored and str(stored.get("where")) == "3":
                    settings["home_mode"] = "same"
        except Exception:
            pass  # 檔案壞掉時退回預設值,不讓整個頁面/註冊流程失敗
    return settings


def validate_settings(data):
    """把前端送來的設定整理成合法值,不合法時丟 LockscreenError(訊息直接顯示給使用者)"""
    s = load_settings_defaults_copy()
    s["auto_push"] = bool(data.get("auto_push"))
    home_mode = str(data.get("home_mode") or "color")
    if home_mode not in HOME_MODES:
        raise LockscreenError("主畫面設定值錯誤")
    s["home_mode"] = home_mode

    lines = data.get("lines")
    if not isinstance(lines, list):
        raise LockscreenError("顯示內容格式錯誤")
    lines = [str(x).rstrip() for x in lines]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise LockscreenError("顯示內容至少要有一行")
    if len(lines) > 8:
        raise LockscreenError("顯示內容最多 8 行")
    for line in lines:
        for key in PLACEHOLDER_RE.findall(line):
            if key not in PLACEHOLDERS:
                raise LockscreenError(f"不認得的欄位 {{{key}}},可用的欄位:" + "、".join("{" + k + "}" for k in PLACEHOLDERS))
    s["lines"] = lines

    for key in ("org_name", "org_address", "org_phone", "org_email"):
        s[key] = str(data.get(key) or "").strip()[:200]

    for key in ("bg_color", "text_color", "home_color"):
        val = str(data.get(key) or "").strip()
        if not HEX_COLOR_RE.match(val):
            raise LockscreenError(f"顏色格式錯誤:{val}(請用 #RRGGBB)")
        s[key] = val

    s["band"] = bool(data.get("band"))

    def int_in(key, lo, hi):
        try:
            v = int(data.get(key))
        except (TypeError, ValueError):
            raise LockscreenError(f"{key} 必須是整數")
        if not lo <= v <= hi:
            raise LockscreenError(f"{key} 必須介於 {lo} 到 {hi}")
        return v

    s["title_font_px"] = int_in("title_font_px", 20, 300)
    s["body_font_px"] = int_in("body_font_px", 20, 300)
    s["min_font_px"] = int_in("min_font_px", 16, 200)
    s["canvas_size"] = DEFAULT_SETTINGS["canvas_size"]
    s["safe_width_ratio"] = DEFAULT_SETTINGS["safe_width_ratio"]

    font_path = str(data.get("font_path") or "").strip()
    if font_path and not os.path.isfile(font_path):
        raise LockscreenError(f"找不到字型檔 {font_path}")
    s["font_path"] = font_path
    return s


def load_settings_defaults_copy():
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def save_settings(settings, path=DEFAULT_SETTINGS_PATH):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 相依套件/字型檢查
# ---------------------------------------------------------------------------
def _pick_tc_index(ImageFont, path):
    """.ttc 字型集合裡通常同時有日/韓/簡/繁多套字形,挑名稱含 TC 的那一套,
    這樣「群」「裝」等字會用台灣的字形。找不到就用第 0 套(字一樣能顯示,只是字形風格不同)。"""
    if not path.lower().endswith(".ttc"):
        return 0
    for idx in range(0, 12):
        try:
            name = " ".join(ImageFont.truetype(path, 20, index=idx).getname())
        except Exception:
            break
        if " TC" in name or "TC " in name or name.endswith("TC"):
            return idx
    return 0


def find_font(settings):
    """回傳 (regular_path, bold_path, index)。找不到時 regular_path 為空字串。"""
    explicit = (settings.get("font_path") or "").strip()
    if explicit:
        return explicit, explicit, None
    candidates = list(FONT_CANDIDATES)
    candidates += sorted(glob.glob("/usr/share/fonts/**/NotoSansCJK*Regular*.tt[cf]", recursive=True))
    candidates += sorted(glob.glob("/usr/share/fonts/**/NotoSans*TC*Regular*.[ot]tf", recursive=True))
    for path in candidates:
        if os.path.isfile(path):
            bold = path.replace("Regular", "Bold")
            return path, (bold if os.path.isfile(bold) else path), None
    return "", "", None


def check_dependencies(settings=None):
    settings = settings or load_settings()
    result = {"pillow": False, "pillow_version": "", "font_path": "", "font_bold_path": "", "font_name": "", "ok": False, "message": ""}
    try:
        import PIL
        from PIL import ImageFont
        result["pillow"] = True
        result["pillow_version"] = PIL.__version__
    except ImportError:
        result["message"] = "webui 的 Python 環境沒有安裝 Pillow,請執行:/opt/nanomdm-webui/venv/bin/pip install Pillow"
        return result

    regular, bold, _ = find_font(settings)
    if not regular:
        result["message"] = "找不到中文字型,請執行:sudo apt install fonts-noto-cjk(或在下方指定字型檔路徑)"
        return result
    result["font_path"] = regular
    result["font_bold_path"] = bold
    try:
        idx = _pick_tc_index(ImageFont, regular)
        result["font_name"] = " ".join(ImageFont.truetype(regular, 20, index=idx).getname())
    except Exception as e:
        result["message"] = f"字型檔無法載入:{e}"
        return result
    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# 文字組合
# ---------------------------------------------------------------------------
def build_values(settings, serial, device_name, group):
    return {
        "device_name": device_name or "",
        "group": group or "(未分類)",
        "serial": serial or "",
        "org_name": settings.get("org_name", ""),
        "org_address": settings.get("org_address", ""),
        "org_phone": settings.get("org_phone", ""),
        "org_email": settings.get("org_email", ""),
    }


def render_line_segments(template_line, values):
    """把一行範本切成「欄位段落」(以兩個以上空白或全形空白分隔),各自代入值。
    如果某一段裡用到的欄位全部是空值(例如 ASM 沒填電話),整段拿掉,不會出現「電話:」後面空白。
    回傳 [段落文字, ...];整行都被拿掉時回傳空 list。"""
    segments = [seg for seg in SEGMENT_SPLIT_RE.split(template_line.strip()) if seg.strip()]
    out = []
    for seg in segments:
        keys = PLACEHOLDER_RE.findall(seg)
        if keys and all(not str(values.get(k, "")).strip() for k in keys):
            continue
        text = PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), seg)
        out.append(text.strip())
    return out


def build_text_lines(settings, values):
    """回傳 [(段落list, 是否為標題行), ...]"""
    result = []
    for i, tpl in enumerate(settings.get("lines") or []):
        segs = render_line_segments(tpl, values)
        if segs:
            result.append((segs, i == 0))
    return result


def build_plain_text(settings, values):
    """純文字版本,給頁面上的文字預覽/紀錄用"""
    return "\n".join("    ".join(segs) for segs, _ in build_text_lines(settings, values))


# ---------------------------------------------------------------------------
# 圖片產生
# ---------------------------------------------------------------------------
def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _layout_line(draw, segments, font_loader, size, min_size, max_width, gap_text):
    """先用指定字級排成一行;放不下就在段落之間換行;單一段落還是放不下才縮小字級。
    回傳 (font, [行文字, ...])"""
    def width(text, font):
        return draw.textlength(text, font=font)

    font = font_loader(size)
    joined = gap_text.join(segments)
    if width(joined, font) <= max_width:
        return font, [joined]

    # 段落間換行(貪婪排入)
    def wrap(font):
        rows, current = [], ""
        for seg in segments:
            trial = seg if not current else current + gap_text + seg
            if width(trial, font) <= max_width or not current:
                current = trial
            else:
                rows.append(current)
                current = seg
        if current:
            rows.append(current)
        return rows

    cur_size = size
    while True:
        font = font_loader(cur_size)
        rows = wrap(font)
        if all(width(r, font) <= max_width for r in rows) or cur_size <= min_size:
            return font, rows
        cur_size = max(min_size, int(cur_size * 0.92))


def render_png(settings, serial, device_name, group):
    """產生鎖定畫面 PNG,回傳 bytes。缺少 Pillow 或字型時丟 LockscreenError。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise LockscreenError("webui 的 Python 環境沒有安裝 Pillow,請執行:/opt/nanomdm-webui/venv/bin/pip install Pillow")

    regular, bold, _ = find_font(settings)
    if not regular:
        raise LockscreenError("找不到中文字型,請執行:sudo apt install fonts-noto-cjk")
    reg_idx = _pick_tc_index(ImageFont, regular)
    bold_idx = _pick_tc_index(ImageFont, bold)

    font_cache = {}

    def loader(path, idx):
        def load(size):
            key = (path, idx, size)
            if key not in font_cache:
                font_cache[key] = ImageFont.truetype(path, size, index=idx)
            return font_cache[key]
        return load

    size = int(settings.get("canvas_size") or 2732)
    bg = _hex_to_rgb(settings.get("bg_color") or "#1f3a5f")
    fg = _hex_to_rgb(settings.get("text_color") or "#ffffff")
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)

    max_width = int(size * float(settings.get("safe_width_ratio") or 0.62))
    values = build_values(settings, serial, device_name, group)
    logical_lines = build_text_lines(settings, values)
    if not logical_lines:
        raise LockscreenError("顯示內容代入後是空白的,請檢查顯示內容設定")

    min_px = int(settings.get("min_font_px") or 40)
    # 排版:每一行(可能被拆成多列)記錄 (字型, 文字, 是否標題)
    rows = []
    for segs, is_title in logical_lines:
        base = int(settings.get("title_font_px") if is_title else settings.get("body_font_px"))
        load = loader(bold, bold_idx) if is_title else loader(regular, reg_idx)
        font, wrapped = _layout_line(draw, segs, load, base, min(min_px, base), max_width, "　 ")
        for r in wrapped:
            rows.append((font, r, is_title))

    # 計算整體高度:同一行拆出來的列用 1.45 倍行距;標題與內文之間多留一些空間
    heights = []
    for font, text, is_title in rows:
        asc, desc = font.getmetrics()
        heights.append(asc + desc)
    gaps = []
    for i in range(len(rows) - 1):
        cur_title, nxt_title = rows[i][2], rows[i + 1][2]
        gaps.append(int(heights[i] * (0.9 if cur_title != nxt_title else 0.45)))
    total_h = sum(heights) + sum(gaps)
    block_w = max(draw.textlength(text, font=font) for font, text, _ in rows)

    y = (size - total_h) // 2
    if settings.get("band"):
        pad_y = int(heights[0] * 0.9)
        band = Image.new("RGBA", (size, total_h + pad_y * 2), fg + (28,))
        img.paste(band, (0, y - pad_y), band)
        # 色帶上下兩條細線,讓資訊區塊在畫面中有明確的範圍
        line_w = max(2, size // 900)
        line_len = int(max(block_w, max_width * 0.5))
        lx = (size - line_len) // 2
        draw.rectangle([lx, y - pad_y, lx + line_len, y - pad_y + line_w], fill=fg)
        draw.rectangle([lx, y + total_h + pad_y - line_w, lx + line_len, y + total_h + pad_y], fill=fg)

    for i, (font, text, is_title) in enumerate(rows):
        w = draw.textlength(text, font=font)
        draw.text(((size - w) / 2, y), text, font=font, fill=fg)
        y += heights[i] + (gaps[i] if i < len(gaps) else 0)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# Apple 定義的 Wallpaper Where 值
WHERE_LOCK = 1
WHERE_HOME = 2
WHERE_BOTH = 3


def build_wallpaper_command_params(image_bytes, where):
    """Settings 指令的參數(交給 utils.send_mdm_command 的 params)"""
    return {"Settings": [{"Item": "Wallpaper", "Image": image_bytes, "Where": int(where)}]}


def lock_where(settings):
    """鎖定畫面指令要用的 Where 值。same 模式直接送 3(明確表示兩個畫面都用這張),
    其他模式送 1,接著再補送主畫面的指令。"""
    return WHERE_BOTH if settings.get("home_mode") == "same" else WHERE_LOCK


def render_home_image(settings, home_image_path=DEFAULT_HOME_IMAGE_PATH):
    """回傳主畫面要用的圖片 bytes;home_mode=same 時回傳 None(不需要另外送主畫面指令)。"""
    mode = settings.get("home_mode") or "color"
    if mode == "same":
        return None
    if mode == "image":
        if not os.path.isfile(home_image_path):
            raise LockscreenError("主畫面設定為「上傳的圖片」,但還沒有上傳圖片")
        with open(home_image_path, "rb") as f:
            return f.read()
    try:
        from PIL import Image
    except ImportError:
        raise LockscreenError("webui 的 Python 環境沒有安裝 Pillow,請執行:/opt/nanomdm-webui/venv/bin/pip install Pillow")
    # 純色圖不需要大尺寸,iPad 會自動放大填滿,顏色不會失真
    img = Image.new("RGB", (512, 512), _hex_to_rgb(settings.get("home_color") or "#2b2f36"))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def save_home_upload(file_bytes, dest_path=DEFAULT_HOME_IMAGE_PATH, max_side=2732):
    """驗證並轉存上傳的主畫面圖片:統一轉成 JPEG、長邊縮到 max_side 以內,
    避免原始照片太大(手機照片動輒 10MB+)讓每台裝置的指令都很肥。回傳 (寬, 高, bytes數)。"""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise LockscreenError("webui 的 Python 環境沒有安裝 Pillow,請執行:/opt/nanomdm-webui/venv/bin/pip install Pillow")
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception:
        raise LockscreenError("無法讀取這個圖片檔,請上傳 JPG 或 PNG")
    img.thumbnail((max_side, max_side))
    tmp = dest_path + ".tmp"
    img.save(tmp, format="JPEG", quality=90, optimize=True)
    os.replace(tmp, dest_path)
    return img.size[0], img.size[1], os.path.getsize(dest_path)


# ---------------------------------------------------------------------------
# 命令列模式(給 webhook-server.py 呼叫)
# ---------------------------------------------------------------------------
def _read_device(devices_csv, serial):
    if not os.path.exists(devices_csv):
        return None
    with open(devices_csv, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("serial_number") or "").strip() == serial:
                return (row.get("device_name") or "").strip(), (row.get("group") or "").strip()
    return None


def _cli(argv):
    import argparse
    parser = argparse.ArgumentParser(description="產生 iPad 鎖定畫面資訊桌布")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_render = sub.add_parser("render", help="產生指定序號的 PNG,輸出到 stdout")
    p_render.add_argument("serial")
    p_render.add_argument("--settings", default=DEFAULT_SETTINGS_PATH)
    p_render.add_argument("--devices-csv", default=DEFAULT_DEVICES_CSV)
    p_render.add_argument("--require-auto-push", action="store_true",
                          help="設定裡沒有開啟自動推送時,直接以 exit code 3 結束(webhook-server 用)")
    p_home = sub.add_parser("home", help="輸出主畫面要用的圖片到 stdout;home_mode=same 時以 exit code 5 結束")
    p_home.add_argument("--settings", default=DEFAULT_SETTINGS_PATH)
    p_home.add_argument("--home-image", default=DEFAULT_HOME_IMAGE_PATH)
    sub.add_parser("check", help="檢查 Pillow 與中文字型")
    args = parser.parse_args(argv)

    if args.cmd == "check":
        print(json.dumps(check_dependencies(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "home":
        settings = load_settings(args.settings)
        try:
            data = render_home_image(settings, args.home_image)
        except LockscreenError as e:
            sys.stderr.write(f"{e}\n")
            return 2
        if data is None:
            return 5
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
        return 0

    settings = load_settings(args.settings)
    if args.require_auto_push and not settings.get("auto_push"):
        sys.stderr.write("鎖定畫面自動推送未開啟\n")
        return 3
    found = _read_device(args.devices_csv, args.serial)
    if not found:
        sys.stderr.write(f"序號 {args.serial} 不在 devices.csv\n")
        return 4
    device_name, group = found
    try:
        png = render_png(settings, args.serial, device_name, group)
    except LockscreenError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    sys.stdout.buffer.write(png)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
