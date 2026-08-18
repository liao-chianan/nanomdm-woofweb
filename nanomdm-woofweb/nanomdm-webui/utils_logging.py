# -*- coding: utf-8 -*-
"""
系統紀錄:使用者登入紀錄 / 操作紀錄。
每一行寫一筆 JSON(JSON Lines格式),方便可靠地解析回結構化資料,
不用依賴容易出錯的正規表達式去拆自由格式文字。
用 Python 標準函式庫的 TimedRotatingFileHandler 做每日自動 rotate。
"""
import datetime
import glob
import json
import logging
import os
from logging.handlers import TimedRotatingFileHandler

_loggers = {}


def _get_logger(cache_key, log_path, backup_count):
    if cache_key in _loggers:
        return _loggers[cache_key]

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger = logging.getLogger(f"nanomdm_webui_system_log_{cache_key}")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # 不要往上冒泡到root logger,避免跟其他log混在一起

    if not logger.handlers:
        handler = TimedRotatingFileHandler(
            log_path, when="midnight", backupCount=backup_count, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

    _loggers[cache_key] = logger
    return logger


def log_login(log_path, retention_days, username, success, ip):
    logger = _get_logger("login", log_path, retention_days)
    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "username": username or "",
        "success": bool(success),
        "ip": ip or "",
    }
    logger.info(json.dumps(entry, ensure_ascii=False))


def log_activity(log_path, retention_days, username, command, success, ip, detail=None):
    logger = _get_logger("activity", log_path, retention_days)
    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "username": username or "",
        "command": command or "",
        "success": bool(success),
        "ip": ip or "",
    }
    if detail:
        entry["detail"] = detail
    logger.info(json.dumps(entry, ensure_ascii=False))


def read_log_entries(log_path):
    """讀取目前檔案 + 所有 rotate 出來的歷史檔案,合併成一份list(每筆是dict)。
    TimedRotatingFileHandler 的備份檔名格式是「原檔名.YYYY-MM-DD」,用 glob 抓齊。
    無法解析的行會被跳過,不會讓整個讀取失敗。
    """
    entries = []
    paths = []
    if os.path.exists(log_path):
        paths.append(log_path)
    paths.extend(sorted(glob.glob(log_path + ".*")))

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries
