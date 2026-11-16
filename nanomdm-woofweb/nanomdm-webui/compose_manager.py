#!/usr/bin/env python3
"""Docker Compose 設定管理:比對並重新套用專案管理的 compose 設定檔(docker-compose.woofweb.yml)。

設計:
  - 使用者自己的 docker-compose.yml(網域、映像檔、自訂參數)完全不動。
  - 專案要統一管理的設定(例如日誌大小上限)放在 docker-compose.woofweb.yml,透過「版本與更新」更新。
  - .env 加上 COMPOSE_FILE=docker-compose.yml:docker-compose.woofweb.yml,讓 docker compose 不管是
    這支程式執行、還是管理者手動執行 `docker compose up -d`,都會兩個檔案一起套用。

比對方式:用 `docker compose config --hash` 算出「套用後」每個服務的設定雜湊值,跟目前執行中容器上的
com.docker.compose.config-hash 標籤比對,不同的就是 `docker compose up -d` 會重建的服務。

套用流程(apply):
  比對 -> 備份 .env 與 compose 檔 -> (第一次)在 .env 加上 COMPOSE_FILE -> docker compose up -d
  -> 健康檢查(容器都在執行、MySQL healthy、nanomdm /version 有回應)
  -> 失敗時自動還原備份並再套用一次。

這支程式可以單獨執行(只用 Python 標準函式庫),WebUI 也是透過 systemd-run 在背景執行它,
這樣套用過程不會因為 WebUI 自己更新、重啟而中斷:
  compose_manager.py plan [--overlay 路徑]   比對,輸出 JSON
  compose_manager.py apply [--user 帳號] [--reason 說明] [--allow-image-change]
  compose_manager.py status                   最後一次套用的結果
"""
import argparse
import datetime
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DEPLOY_DIR = "/opt/nanomdm-deployment"
BASE_COMPOSE = "docker-compose.yml"
MANAGED_COMPOSE = "docker-compose.woofweb.yml"
STATE_DIR = os.path.join(HERE, ".compose_apply")
STATUS_FILE = os.path.join(STATE_DIR, "status.json")
LOCK_FILE = os.path.join(STATE_DIR, "apply.lock")
BACKUP_ROOT = os.path.join(STATE_DIR, "backups")
# 最後一次確認可以正常運作的 docker-compose.woofweb.yml。套用失敗時還原的是這份,不是套用前的檔案:
# 透過「版本與更新」更新時,新版管理設定檔在套用前就已經寫進部署目錄,套用前的檔案就是有問題的那份
LAST_GOOD_OVERLAY = os.path.join(STATE_DIR, "last-good-" + MANAGED_COMPOSE)
BACKUP_KEEP = 10
HEALTH_TIMEOUT_SECONDS = 180
UP_TIMEOUT_SECONDS = 900


class ComposeError(Exception):
    pass


# ---------------------------------------------------------------------------
# 基本工具
# ---------------------------------------------------------------------------
def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run(args, cwd=None, env_overrides=None, timeout=60):
    env = dict(os.environ)
    env.pop("COMPOSE_FILE", None)  # 不讓呼叫端環境變數影響結果,一律以 .env 或明確指定的值為準
    if env_overrides:
        env.update(env_overrides)
    try:
        proc = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, "", f"執行逾時({timeout} 秒): {' '.join(args[:4])}"
    except Exception as e:
        return -1, "", str(e)
    return proc.returncode, proc.stdout, proc.stderr


def read_env_file(path):
    """讀取 .env,回傳 (lines, dict)。只做簡單的 KEY=VALUE 解析(跟 docker compose 的 .env 規則相容的範圍)。"""
    values, lines = {}, []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return lines, values


def _compose_file_list(value):
    return [p for p in (value or "").split(":") if p]


def desired_compose_file_value(current_value):
    """在 .env 現有的 COMPOSE_FILE 後面加上專案管理的設定檔;沒有設定過的話用預設的兩個檔案。"""
    files = _compose_file_list(current_value) or [BASE_COMPOSE]
    if MANAGED_COMPOSE not in [os.path.basename(p) for p in files]:
        files.append(MANAGED_COMPOSE)
    return ":".join(files)


def _write_env_compose_file(env_path, value):
    """在 .env 設定 COMPOSE_FILE(已有就取代那一行,沒有就加在最後面),保留原本的權限與擁有者。"""
    lines, _ = read_env_file(env_path)
    new_line = f"COMPOSE_FILE={value}"
    replaced = False
    for i, line in enumerate(lines):
        key = line.strip().partition("=")[0].strip()
        if key in ("COMPOSE_FILE", "export COMPOSE_FILE"):
            lines[i] = new_line
            replaced = True
    if not replaced:
        lines += ["", "# docker compose 同時套用使用者的 docker-compose.yml 與專案管理的 docker-compose.woofweb.yml",
                  "# (由 WebUI「版本與更新」的 Docker Compose 設定加入,手動執行 docker compose 時也會套用)", new_line]
    st = os.stat(env_path) if os.path.exists(env_path) else None
    tmp = env_path + ".compose-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if st:
        os.chmod(tmp, st.st_mode & 0o7777)
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, env_path)


# ---------------------------------------------------------------------------
# 比對
# ---------------------------------------------------------------------------
def _compose_config(deploy_dir, compose_file_value, extra_args):
    rc, out, err = _run(["docker", "compose"] + extra_args, cwd=deploy_dir,
                        env_overrides={"COMPOSE_FILE": compose_file_value}, timeout=60)
    if rc != 0:
        raise ComposeError((err or out or "docker compose config 失敗").strip())
    return out


def _target_hashes(deploy_dir, compose_file_value):
    out = _compose_config(deploy_dir, compose_file_value, ["config", "--hash=*"])
    hashes = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            hashes[parts[0]] = parts[1]
    return hashes


def _target_config(deploy_dir, compose_file_value):
    return json.loads(_compose_config(deploy_dir, compose_file_value, ["config", "--format", "json"]))


def _running_containers(project):
    rc, out, err = _run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"])
    if rc != 0:
        raise ComposeError((err or "docker ps 失敗").strip())
    ids = out.split()
    if not ids:
        return {}
    rc, out, err = _run(["docker", "inspect"] + ids)
    if rc != 0:
        raise ComposeError((err or "docker inspect 失敗").strip())
    result = {}
    for c in json.loads(out):
        labels = (c.get("Config") or {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if not service:
            continue
        state = c.get("State") or {}
        result[service] = {
            "name": (c.get("Name") or "").lstrip("/"),
            "hash": labels.get("com.docker.compose.config-hash", ""),
            "image": (c.get("Config") or {}).get("Image", ""),
            "status": state.get("Status", ""),
            "health": (state.get("Health") or {}).get("Status"),
        }
    return result


def _logging_text(service_cfg):
    logging_cfg = service_cfg.get("logging") or {}
    opts = logging_cfg.get("options") or {}
    if not opts.get("max-size"):
        return "未設定上限"
    return f"上限 {opts.get('max-size')} x {opts.get('max-file', '1')} 個檔案"


def plan(deploy_dir=DEFAULT_DEPLOY_DIR, overlay_path=None):
    """比對目前執行中的容器與「套用後」的設定。overlay_path 用來預覽還沒下載到本機的新版管理設定檔。
    回傳 dict;無法比對時 ok=False 並附上 error。"""
    base_path = os.path.join(deploy_dir, BASE_COMPOSE)
    managed_path = overlay_path or os.path.join(deploy_dir, MANAGED_COMPOSE)
    env_path = os.path.join(deploy_dir, ".env")
    result = {"ok": False, "deploy_dir": deploy_dir, "managed_file": MANAGED_COMPOSE,
              "managed_file_exists": os.path.isfile(os.path.join(deploy_dir, MANAGED_COMPOSE)),
              "checked_at": _now()}
    try:
        if not os.path.isfile(base_path):
            raise ComposeError(f"找不到 {base_path}")
        if not os.path.isfile(managed_path):
            raise ComposeError(f"找不到專案管理的設定檔 {managed_path},請先透過「版本與更新」更新到包含這個檔案的版本")

        _, env_values = read_env_file(env_path)
        current_value = env_values.get("COMPOSE_FILE", "")
        enabled = MANAGED_COMPOSE in [os.path.basename(p) for p in _compose_file_list(current_value)]
        target_value = desired_compose_file_value(current_value)
        if overlay_path:
            target_value = ":".join(
                overlay_path if os.path.basename(p) == MANAGED_COMPOSE else p
                for p in _compose_file_list(target_value))

        target_cfg = _target_config(deploy_dir, target_value)
        project = target_cfg.get("name") or os.path.basename(deploy_dir)
        target_hashes = _target_hashes(deploy_dir, target_value)
        running = _running_containers(project)
        if not running:
            raise ComposeError(f"找不到 compose 專案「{project}」執行中的容器,請確認 docker-compose.yml 所在目錄與專案名稱")

        services = []
        for name in sorted(target_hashes):
            svc_cfg = (target_cfg.get("services") or {}).get(name, {})
            cur = running.get(name)
            target_image = svc_cfg.get("image", "")
            entry = {
                "service": name,
                "container": svc_cfg.get("container_name") or (cur or {}).get("name", ""),
                "action": "create" if cur is None else ("recreate" if cur["hash"] != target_hashes[name] else "none"),
                "current_image": (cur or {}).get("image", ""),
                "target_image": target_image,
                "image_change": bool(cur and target_image and cur["image"] != target_image),
                "logging": _logging_text(svc_cfg),
                "status": (cur or {}).get("status", ""),
            }
            services.append(entry)

        missing_env = []
        example_path = os.path.join(deploy_dir, "env.example")
        if os.path.isfile(example_path):
            _, example_values = read_env_file(example_path)
            missing_env = sorted(k for k in example_values if k not in env_values and k != "COMPOSE_FILE")

        result.update({
            "ok": True,
            "project": project,
            "enabled": enabled,
            "env_compose_file": current_value,
            "target_compose_file": desired_compose_file_value(current_value),
            "services": services,
            "recreate_services": [s["service"] for s in services if s["action"] != "none"],
            "image_changes": [s for s in services if s["image_change"]],
            "missing_env_keys": missing_env,
            "needs_apply": (not enabled) or any(s["action"] != "none" for s in services),
        })
    except ComposeError as e:
        result["error"] = str(e)
    except Exception as e:
        result["error"] = f"比對失敗: {e}"
    return result


# ---------------------------------------------------------------------------
# 套用
# ---------------------------------------------------------------------------
class _Status:
    """套用過程即時寫進 status.json,讓 WebUI 輪詢顯示進度(WebUI 重啟後也讀得到)。"""

    def __init__(self, user, reason):
        self.data = {"state": "running", "started_at": _now(), "finished_at": None,
                     "user": user, "reason": reason, "steps": [], "message": ""}
        self.save()

    def step(self, text, ok=True):
        self.data["steps"].append({"time": _now(), "text": text, "ok": ok})
        self.save()

    def finish(self, state, message):
        self.data.update({"state": state, "finished_at": _now(), "message": message})
        self.save()

    def save(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATUS_FILE)


def read_status():
    try:
        with open(STATUS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _backup(deploy_dir):
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_ROOT, ts)
    os.makedirs(dest, exist_ok=True)
    saved = []
    for name in (".env", BASE_COMPOSE, MANAGED_COMPOSE):
        src = os.path.join(deploy_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, name))
            saved.append(name)
    os.chmod(dest, 0o700)  # .env 有密碼
    backups = sorted(d for d in os.listdir(BACKUP_ROOT) if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
    for old in backups[:-BACKUP_KEEP]:
        shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
    return dest, saved


def _save_last_good(deploy_dir):
    src = os.path.join(deploy_dir, MANAGED_COMPOSE)
    if os.path.isfile(src):
        shutil.copy2(src, LAST_GOOD_OVERLAY)


def _restore(deploy_dir, backup_dir):
    """還原成套用前可以運作的狀態。回傳 (compose_file_override, note)。
    - .env 還原成套用前的內容(第一次套用時,這會拿掉剛加上的 COMPOSE_FILE)
    - 管理設定檔還原成最後一次確認可以運作的版本;沒有這份紀錄時,改成只用 docker-compose.yml 啟動
    """
    env_backup = os.path.join(backup_dir, ".env")
    if os.path.isfile(env_backup):
        shutil.copy2(env_backup, os.path.join(deploy_dir, ".env"))
    _, env_values = read_env_file(os.path.join(deploy_dir, ".env"))
    files = _compose_file_list(env_values.get("COMPOSE_FILE", ""))
    if MANAGED_COMPOSE not in [os.path.basename(f) for f in files]:
        return None, "已還原 .env(不使用專案管理設定檔)"
    if os.path.isfile(LAST_GOOD_OVERLAY):
        shutil.copy2(LAST_GOOD_OVERLAY, os.path.join(deploy_dir, MANAGED_COMPOSE))
        return None, f"已還原 .env,{MANAGED_COMPOSE} 已還原成最後一次正常運作的版本"
    base_only = ":".join(f for f in files if os.path.basename(f) != MANAGED_COMPOSE) or BASE_COMPOSE
    return base_only, (f"沒有 {MANAGED_COMPOSE} 正常運作過的紀錄,改成只用 {base_only} 啟動;"
                       f"之後手動執行 docker compose 仍會讀取有問題的 {MANAGED_COMPOSE},請先修正")


def _compose_up(deploy_dir, compose_file_value=None):
    rc, out, err = _run(["docker", "compose", "up", "-d"], cwd=deploy_dir, timeout=UP_TIMEOUT_SECONDS,
                        env_overrides={"COMPOSE_FILE": compose_file_value} if compose_file_value else None)
    output = "\n".join(x for x in (out.strip(), err.strip()) if x)
    return rc == 0, output


def _health_check(deploy_dir, status, compose_file_value=None):
    """等到所有服務的容器都套用了最新設定、正在執行(有 healthcheck 的要 healthy),再確認 nanomdm 有回應。"""
    _, env_values = read_env_file(os.path.join(deploy_dir, ".env"))
    target_value = compose_file_value or env_values.get("COMPOSE_FILE") or None
    deadline = time.time() + HEALTH_TIMEOUT_SECONDS
    last_problem = ""
    while time.time() < deadline:
        try:
            cfg = _target_config(deploy_dir, target_value) if target_value else _target_config(deploy_dir, BASE_COMPOSE)
            hashes = _target_hashes(deploy_dir, target_value or BASE_COMPOSE)
            running = _running_containers(cfg.get("name"))
            problems = []
            for name, h in hashes.items():
                cur = running.get(name)
                if cur is None:
                    problems.append(f"{name} 沒有容器")
                elif cur["hash"] != h:
                    problems.append(f"{name} 尚未套用新設定")
                elif cur["status"] != "running":
                    problems.append(f"{name} 狀態 {cur['status']}")
                elif cur["health"] not in (None, "healthy"):
                    problems.append(f"{name} 健康檢查 {cur['health']}")
            if not problems:
                break
            last_problem = "、".join(problems)
        except ComposeError as e:
            last_problem = str(e)
        time.sleep(3)
    else:
        return False, f"等待 {HEALTH_TIMEOUT_SECONDS} 秒後仍有問題: {last_problem}"

    base_url = (env_values.get("NANOMDM_BASE_URL") or "http://127.0.0.1:9000").rstrip("/")
    last_err = ""
    for _ in range(20):
        try:
            with urllib.request.urlopen(base_url + "/version", timeout=5) as resp:
                if resp.status == 200:
                    status.step(f"nanomdm 服務有回應({base_url}/version)")
                    return True, ""
                last_err = f"HTTP {resp.status}"
        except Exception as e:
            last_err = str(e)
        time.sleep(3)
    return False, f"nanomdm 服務沒有回應: {last_err}"


def apply(deploy_dir=DEFAULT_DEPLOY_DIR, user="", reason="", allow_image_change=False):
    os.makedirs(STATE_DIR, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return {"state": "failed", "message": "已經有另一個套用作業正在執行"}

    status = _Status(user, reason)
    try:
        p = plan(deploy_dir)
        if not p["ok"]:
            status.step(f"比對失敗: {p.get('error')}", ok=False)
            status.finish("failed", "比對失敗,沒有做任何變更")
            return status.data
        status.data["plan"] = p
        if p["missing_env_keys"]:
            # 只提醒不阻擋:env.example 裡有些是選用的變數,缺少不一定代表有問題
            status.step(f"提醒: .env 沒有 env.example 裡的這些變數(選用的可以忽略): {'、'.join(p['missing_env_keys'])}")
        if p["image_changes"] and not allow_image_change:
            changes = "、".join(f"{s['service']}({s['current_image']} -> {s['target_image']})" for s in p["image_changes"])
            status.step(f"docker-compose.yml 有尚未套用的映像檔變更: {changes}", ok=False)
            status.finish("failed", "偵測到映像檔變更,為了安全不會自動套用,請到「版本與更新」確認後手動套用")
            return status.data
        if not p["needs_apply"]:
            _save_last_good(deploy_dir)
            status.step("目前的容器已經是最新設定,不需要重新套用")
            status.finish("succeeded", "不需要重新套用")
            return status.data

        recreate = "、".join(p["recreate_services"]) or "無"
        status.step(f"比對完成,將重建: {recreate}")
        backup_dir, saved = _backup(deploy_dir)
        status.data["backup_dir"] = backup_dir
        status.step(f"已備份 {'、'.join(saved)} 到 {backup_dir}")

        if not p["enabled"]:
            _write_env_compose_file(os.path.join(deploy_dir, ".env"), p["target_compose_file"])
            status.step(f".env 已加入 COMPOSE_FILE={p['target_compose_file']}")

        status.step("執行 docker compose up -d ...")
        ok, output = _compose_up(deploy_dir)
        if ok:
            status.step("docker compose up -d 完成" + (f":\n{output[-1500:]}" if output else ""))
            ok, problem = _health_check(deploy_dir, status)
            if ok:
                _save_last_good(deploy_dir)
                status.step("健康檢查通過")
                status.finish("succeeded", f"套用完成,已重建: {recreate}")
                return status.data
            status.step(f"健康檢查失敗: {problem}", ok=False)
        else:
            status.step(f"docker compose up -d 失敗:\n{output[-1500:]}", ok=False)

        # 失敗:還原 .env 與管理設定檔,再套用一次
        status.step("還原並重新套用 ...")
        override, note = _restore(deploy_dir, backup_dir)
        status.step(note)
        ok2, output2 = _compose_up(deploy_dir, override)
        ok3, problem3 = _health_check(deploy_dir, status, override) if ok2 else (False, output2[-1500:])
        if ok3:
            status.step("已還原成可以正常運作的設定,服務正常")
            status.finish("rolled_back", f"套用失敗,已自動還原({note})")
        else:
            status.step(f"還原後仍有問題: {problem3}", ok=False)
            status.finish("rollback_failed",
                          f"套用失敗,自動還原後服務仍不正常,請到「系統狀態」檢查容器。備份位置: {backup_dir}")
        return status.data
    except Exception as e:
        status.step(f"發生未預期的錯誤: {e}", ok=False)
        status.finish("failed", f"發生未預期的錯誤: {e}")
        return status.data
    finally:
        _log_activity(status.data)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _log_activity(data):
    """寫進 WebUI 的操作紀錄(系統紀錄頁看得到)。失敗不影響套用結果。"""
    try:
        sys.path.insert(0, HERE)
        import config
        import utils_logging
        cfg = config.load_config()
        ok = data.get("state") == "succeeded"
        detail = data.get("message", "")
        if data.get("reason"):
            detail = f"{data['reason']}: {detail}"
        if data.get("backup_dir"):
            detail += f", 備份位置={data['backup_dir']}"
        utils_logging.log_activity(
            cfg["system_logs"]["user_activity_log"], cfg["system_logs"]["user_activity_retention_days"],
            data.get("user") or "系統(自動排程)", "系統維護-套用 Docker Compose 設定", ok, None, detail=detail)
    except Exception as e:
        print(f"寫入操作紀錄失敗: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deploy-dir", default=DEFAULT_DEPLOY_DIR)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--overlay", help="預覽用的新版 docker-compose.woofweb.yml 路徑")
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--user", default="")
    p_apply.add_argument("--reason", default="")
    p_apply.add_argument("--allow-image-change", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()

    if args.cmd == "plan":
        out = plan(args.deploy_dir, args.overlay)
    elif args.cmd == "apply":
        out = apply(args.deploy_dir, args.user, args.reason, args.allow_image_change)
    else:
        out = read_status() or {}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if args.cmd == "apply" and out.get("state") != "succeeded":
        sys.exit(1)


if __name__ == "__main__":
    main()
