#!/usr/bin/env bash
#
# install.sh — NanoMDM WoofWeb 一鍵安裝腳本
# 適用環境: Debian 12 (bookworm) x86_64,全新主機,需要 sudo 權限、固定對外IP、DNS解析
#
# 用法: sudo bash install.sh
#
set -euo pipefail

# =============================================================================
# 共用輸出函式
# =============================================================================
C_RESET='\033[0m'
C_RED='\033[0;31m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'
C_BLUE='\033[0;34m'

log_step()  { echo -e "\n${C_BLUE}==>${C_RESET} ${1}"; }
log_ok()    { echo -e "${C_GREEN}  ✓${C_RESET} ${1}"; }
log_warn()  { echo -e "${C_YELLOW}  !${C_RESET} ${1}"; }
log_err()   { echo -e "${C_RED}  ✗${C_RESET} ${1}" >&2; }
die()       { log_err "$1"; exit 1; }

# =============================================================================
# 系統需求檢查
# =============================================================================
log_step "檢查系統需求"

if [ "$(id -u)" -ne 0 ]; then
    die "請用 root 權限執行(例如: sudo bash install.sh)"
fi

if [ ! -f /etc/os-release ]; then
    die "找不到 /etc/os-release,無法確認作業系統版本"
fi
# shellcheck disable=SC1091
. /etc/os-release
if [ "${ID:-}" != "debian" ] || [ "${VERSION_ID:-}" != "12" ]; then
    log_warn "偵測到的作業系統是 ${PRETTY_NAME:-未知},本腳本是針對 Debian 12 (bookworm) 設計"
    read -rp "  是否仍要繼續? (y/N): " continue_anyway
    if [ "${continue_anyway,,}" != "y" ]; then
        die "已取消安裝"
    fi
fi

if [ "$(uname -m)" != "x86_64" ]; then
    die "偵測到的架構是 $(uname -m),本腳本只支援 x86_64(因為docker image與nanodep-syncer執行檔都是linux-amd64版本)"
fi

log_ok "作業系統與架構檢查通過: ${PRETTY_NAME:-Debian 12} / $(uname -m)"

# =============================================================================
# 1. 取得 Docker 官方 repo
# =============================================================================
log_step "設定 Docker 官方 apt repo"

apt update -qq
apt install -y -qq ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
fi
chmod a+r /etc/apt/keyrings/docker.gpg

if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
        $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        tee /etc/apt/sources.list.d/docker.list > /dev/null
fi

log_ok "Docker repo 設定完成"

# =============================================================================
# 2. 安裝必備套件
# =============================================================================
log_step "安裝必備套件(這一步可能需要幾分鐘)"

apt update -qq
apt install -y \
    build-essential strace git curl wget jq unzip pigz rsync \
    nginx certbot python3-certbot-nginx python3-dev python3-pip python3-venv \
    net-tools dnsutils ca-certificates iperf3 htop iftop iotop ioping \
    traceroute lsof usbutils pciutils bash-completion gnupg \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable --now docker > /dev/null 2>&1 || die "docker 服務啟動失敗,請執行 systemctl status docker 檢查"

log_ok "套件安裝完成,docker 服務已啟動"

# =============================================================================
# 3. 建立部署目錄
# =============================================================================
log_step "建立部署目錄"

mkdir -p /opt/nanomdm-webui
mkdir -p /opt/nanomdm-deployment
mkdir -p /opt/nanomdm-deployment/mysql-data
mkdir -p /opt/nanomdm-deployment/mysql-init
mkdir -p /opt/nanomdm-deployment/scep-depot

log_ok "目錄建立完成"

# =============================================================================
# 4. 下載並解壓縮專案
# =============================================================================
log_step "下載 NanoMDM WoofWeb 專案"

PROJECT_ZIP_URL="https://github.com/liao-chianan/nanomdm-woofweb/raw/main/nanomdm-woofweb.zip"
TMP_ZIP="/tmp/nanomdm-woofweb.zip"
TMP_EXTRACT="/tmp/nanomdm-woofweb-extract"

curl -fsSL "$PROJECT_ZIP_URL" -o "$TMP_ZIP" || die "下載失敗,請確認網路連線與網址是否正確: $PROJECT_ZIP_URL"

rm -rf "$TMP_EXTRACT"
mkdir -p "$TMP_EXTRACT"
unzip -q "$TMP_ZIP" -d "$TMP_EXTRACT" || die "解壓縮失敗,檔案可能損毀"

[ -d "$TMP_EXTRACT/nanomdm-webui" ] || die "解壓縮後找不到 nanomdm-webui 目錄,壓縮檔內容結構可能跟預期不符"
[ -d "$TMP_EXTRACT/nanomdm-deployment" ] || die "解壓縮後找不到 nanomdm-deployment 目錄,壓縮檔內容結構可能跟預期不符"

# 用rsync而不是單純cp -r,這樣如果之後重複執行這支腳本(例如中途失敗要重跑),
# 已經存在的檔案會被正確覆蓋更新,而不是保留舊版本或報錯
if command -v rsync > /dev/null 2>&1; then
    rsync -a "$TMP_EXTRACT/nanomdm-webui/" /opt/nanomdm-webui/
    rsync -a "$TMP_EXTRACT/nanomdm-deployment/" /opt/nanomdm-deployment/
else
    cp -rf "$TMP_EXTRACT/nanomdm-webui/." /opt/nanomdm-webui/
    cp -rf "$TMP_EXTRACT/nanomdm-deployment/." /opt/nanomdm-deployment/
fi

rm -rf "$TMP_ZIP" "$TMP_EXTRACT"

log_ok "專案下載並部署到 /opt/nanomdm-webui 與 /opt/nanomdm-deployment 完成"

# 明確設定已知需要執行權限的腳本,不依賴zip本身有沒有保留執行權限位元
# (zip/git在某些流程下不一定會保留unix執行權限,這是實際部署時遇到過的問題:
#  這些腳本是被我們的程式直接呼叫路徑執行,不是透過bash/sh間接執行,缺execute bit會直接
#  收到 Permission denied)
chmod +x /opt/nanomdm-deployment/nanodep-release/tools/*.sh 2>/dev/null || true
chmod +x /opt/nanomdm-deployment/nanoaxm-tools/*.sh 2>/dev/null || true
chmod +x /opt/nanomdm-deployment/check_vpp_license.sh 2>/dev/null || true
chmod +x /opt/nanomdm-deployment/check-cert-expiry.sh 2>/dev/null || true
log_ok "已設定必要腳本的執行權限"

# =============================================================================
# 5. 互動式詢問部署資訊
# =============================================================================
log_step "互動式安裝設定"
echo "接下來需要輸入幾項部署資訊,每一項都會說明用途。"
echo ""

# ---- 5.1 單位英文縮寫 ----
while true; do
    read -rp "單位英文縮寫(小寫英數字,例如 school,將作為 DEP_NAME/NANOAXM_NAME/NANODEP_NAME 的預設值): " ORG_ABBR
    if [[ "$ORG_ABBR" =~ ^[a-z0-9]+$ ]]; then
        break
    fi
    log_warn "請輸入小寫英文字母或數字組成的縮寫,不要有空白或特殊符號"
done
log_ok "單位縮寫: $ORG_ABBR"

# ---- 5.2 伺服器網域名稱 ----
echo ""
echo "伺服器網域名稱,將用於 nanomdm 設定、nginx 設定、certbot 憑證申請。"
echo "請確保這個網域的 DNS A 記錄已經指向本機的對外IP,且已經生效。"
while true; do
    read -rp "伺服器網域名稱(例如 nanomdm.example.edu.tw): " SERVER_DOMAIN

    # 基本格式檢查:至少要有一個點,只能是英數字、點、減號組成
    if ! [[ "$SERVER_DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$ ]]; then
        log_warn "網域格式看起來不正確,請重新輸入(例如 nanomdm.example.edu.tw)"
        continue
    fi

    # DNS查詢檢查:確認這個網域至少能查到一筆A記錄,不保證一定是指向本機,只是基本健檢
    echo "  正在查詢 DNS..."
    if dig +short "$SERVER_DOMAIN" A | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        resolved_ip=$(dig +short "$SERVER_DOMAIN" A | head -1)
        log_ok "DNS查詢成功,$SERVER_DOMAIN 目前指向 $resolved_ip"
        my_ip=$(curl -fsSL -4 https://ifconfig.me 2>/dev/null || curl -fsSL -4 https://icanhazip.com 2>/dev/null || echo "")
        if [ -n "$my_ip" ] && [ "$my_ip" != "$resolved_ip" ]; then
            log_warn "偵測到本機對外IP是 $my_ip,跟網域目前解析到的 $resolved_ip 不一樣。"
            log_warn "如果是剛改過DNS、還在生效中,可以先繼續;但certbot申請憑證前,DNS一定要真的生效才會成功。"
            read -rp "  是否仍要繼續? (y/N): " continue_anyway
            [ "${continue_anyway,,}" = "y" ] && break
            continue
        fi
        break
    else
        log_warn "查詢不到 $SERVER_DOMAIN 的DNS A記錄。"
        read -rp "  是否仍要繼續?(例如DNS還在生效中) (y/N): " continue_anyway
        [ "${continue_anyway,,}" = "y" ] && break
    fi
done
log_ok "伺服器網域: $SERVER_DOMAIN"

# ---- 5.3 Certbot email ----
echo ""
echo "Let's Encrypt/certbot 申請憑證時需要一個聯絡email,只會用於憑證到期等相關通知,不影響系統功能。"
while true; do
    read -rp "Certbot 通知email: " CERTBOT_EMAIL
    if [[ "$CERTBOT_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
        break
    fi
    log_warn "email格式看起來不正確,請重新輸入"
done
log_ok "Certbot email: $CERTBOT_EMAIL"

# ---- 5.4 API金鑰(nanomdm/nanodep/nanoaxm共用同一組) ----
echo ""
DEFAULT_API_KEY=$(openssl rand -hex 24)
echo "nanomdm/nanodep/nanoaxm 這三個服務都需要各自的API金鑰,通常是同一個管理者在維護,"
echo "所以這裡用同一組金鑰即可。直接按 Enter 使用自動產生的亂數金鑰(建議),或自行輸入。"
read -rp "API金鑰 [自動產生: ${DEFAULT_API_KEY:0:8}...]: " API_KEY_INPUT
API_KEY="${API_KEY_INPUT:-$DEFAULT_API_KEY}"
log_ok "API金鑰已設定"

# ---- 5.5 MySQL資料庫密碼(root與nanomdm/nanodep/nanoaxm三個服務帳號共用同一組) ----
echo ""
DEFAULT_DB_PASSWORD=$(openssl rand -hex 16)
echo "MySQL的root密碼,以及nanomdm/nanodep/nanoaxm三個資料庫帳號的密碼,同樣用同一組即可。"
echo "直接按 Enter 使用自動產生的亂數密碼(建議),或自行輸入。"
read -rp "資料庫密碼 [自動產生: ${DEFAULT_DB_PASSWORD:0:8}...]: " DB_PASSWORD_INPUT
DB_PASSWORD="${DB_PASSWORD_INPUT:-$DEFAULT_DB_PASSWORD}"
log_ok "資料庫密碼已設定"

# ---- 5.6 SCEP相關密鑰(自動產生,不詢問) ----
# 用hex而不是base64:SCEP_CHALLENGE之後會被寫進XML格式的mobileconfig檔案裡,
# hex只含0-9a-f,不用擔心任何特殊字元在XML/shell/docker-compose參數等情境下需要跳脫
SCEP_CHALLENGE=$(openssl rand -hex 20)
SCEP_CA_PASSWORD=$(openssl rand -hex 20)
log_ok "SCEP挑戰密碼、SCEP CA私鑰密碼已自動產生"

echo ""
echo "=============================================="
echo " 設定摘要"
echo "=============================================="
echo "  單位縮寫:        $ORG_ABBR"
echo "  伺服器網域:      $SERVER_DOMAIN"
echo "  Certbot email:  $CERTBOT_EMAIL"
echo "  API金鑰:         (已設定,將寫入 .env)"
echo "  資料庫密碼:      (已設定,將寫入 .env)"
echo "  SCEP相關密鑰:    (已自動產生,將寫入 .env)"
echo "=============================================="
read -rp "確認以上設定並繼續安裝? (Y/n): " confirm_settings
if [ "${confirm_settings,,}" = "n" ]; then
    die "已取消安裝"
fi

# =============================================================================
# 產生 .env
# =============================================================================
log_step "產生 .env 設定檔"

ENV_FILE="/opt/nanomdm-deployment/.env"

cat > "$ENV_FILE" << EOF
# 由 install.sh 自動產生於 $(date '+%Y-%m-%d %H:%M:%S')

# --- nanomdm ---
NANOMDM_API_KEY=${API_KEY}
NANOMDM_BASE_URL=http://127.0.0.1:9000
NANOMDM_DB_PASSWORD=${DB_PASSWORD}

# --- nanodep ---
NANODEP_API_KEY=${API_KEY}
NANODEP_BASE_URL=http://127.0.0.1:9001
NANODEP_NAME=${ORG_ABBR}
NANODEP_DB_PASSWORD=${DB_PASSWORD}
NANODEP_DEPSYNCER_RESTART_CMD=systemctl restart nanodep-syncer.service

# --- nanoaxm ---
NANOAXM_API_KEY=${API_KEY}
NANOAXM_BASE_URL=http://127.0.0.1:9005
NANOAXM_NAME=${ORG_ABBR}
NANOAXM_DB_PASSWORD=${DB_PASSWORD}

# --- MySQL ---
MYSQL_ROOT_PASSWORD=${DB_PASSWORD}

# --- SCEP ---
SCEP_CHALLENGE=${SCEP_CHALLENGE}
SCEP_CA_PASSWORD=${SCEP_CA_PASSWORD}

# --- nginx憑證路徑(標準Let's Encrypt慣例,通常不需要更動) ---
NGINX_CERT_PATH=/etc/letsencrypt/live/${SERVER_DOMAIN}/fullchain.pem

# --- VPP Token路徑(維持預設值即可,之後透過webui上傳VPP Token時會存到這裡) ---
VPP_TOKEN_PATH=/opt/nanomdm-deployment/vpp_token.vpptoken

# --- 學校識別資訊(選填,用於遺失模式指令的預設訊息,可之後在webui「系統環境參數」頁面修改) ---
SCHOOL_NAME=${ORG_ABBR}
SCHOOL_PHONE=
EOF

chmod 600 "$ENV_FILE"
log_ok ".env 已產生並設定權限為僅 root 可讀寫"

# =============================================================================
# 準備 MySQL 初始化腳本
# =============================================================================
log_step "準備 MySQL 初始化腳本"

# 複製schema(建立資料庫+資料表結構),用01前綴確保在使用者建立腳本之前執行
cp /opt/nanomdm-deployment/nanomdm_schemas_clean.sql /opt/nanomdm-deployment/mysql-init/01-schemas.sql

# 動態產生建立三個資料庫帳號的腳本(schema檔案本身不含這部分,因為密碼是互動輸入才知道的)。
# 用02前綴確保在01-schemas.sql之後執行(GRANT要在資料庫已經存在之後才能下)
cat > /opt/nanomdm-deployment/mysql-init/02-create-users.sql << EOF
CREATE USER IF NOT EXISTS 'nanomdm'@'%' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON nanomdm.* TO 'nanomdm'@'%';

CREATE USER IF NOT EXISTS 'nanodep'@'%' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON nanodep.* TO 'nanodep'@'%';

CREATE USER IF NOT EXISTS 'nanoaxm'@'%' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON nanoaxm.* TO 'nanoaxm'@'%';

FLUSH PRIVILEGES;
EOF

log_ok "MySQL 初始化腳本準備完成(01-schemas.sql / 02-create-users.sql)"

# =============================================================================
# 6. 部署 Docker 服務
# =============================================================================

# 重要防呆:docker官方MySQL image的docker-entrypoint-initdb.d初始化腳本(建立資料庫/帳號)
# 只有在資料目錄是「全新空白」時才會執行。如果這是重跑第二次(例如上次安裝中途失敗過),
# mysql-data目錄可能已經有殘留資料,MySQL會直接跳過所有初始化腳本、用舊資料開機,
# 導致這次產生的.env密碼/帳號設定完全沒有生效,後面會卡在等待逾時。
# 這裡先偵測、明確詢問使用者要不要清空重新初始化,而不是讓它安靜失敗。
if [ -d /opt/nanomdm-deployment/mysql-data ] && [ -n "$(ls -A /opt/nanomdm-deployment/mysql-data 2>/dev/null)" ]; then
    log_warn "偵測到 /opt/nanomdm-deployment/mysql-data 目錄裡已經有殘留資料(可能是先前安裝過、或這是重跑第二次)。"
    log_warn "MySQL 官方 image 只有在資料目錄全新空白時,才會執行建立資料庫/帳號的初始化腳本;"
    log_warn "如果保留這份殘留資料,這次產生的新密碼/帳號設定不會生效,安裝會卡在等待逾時。"
    read -rp "  是否要清空這個目錄、重新初始化?(會刪除裡面所有現有資料) (y/N): " wipe_mysql_data
    if [ "${wipe_mysql_data,,}" = "y" ]; then
        docker rm -f nanomdm-mysql > /dev/null 2>&1 || true
        rm -rf /opt/nanomdm-deployment/mysql-data/*
        log_ok "已清空 mysql-data,將以全新狀態初始化"
    else
        log_warn "保留現有資料。如果稍後卡在等待逾時,代表帳號密碼跟這份舊資料對不起來,需要手動處理"
    fi
fi

log_step "啟動 MySQL 容器(其他容器需要等它就緒)"

cd /opt/nanomdm-deployment
docker compose up -d --force-recreate mysql

echo "  等待 MySQL 完成初始化(首次啟動要跑 01-schemas.sql / 02-create-users.sql,可能要一點時間)..."
for i in $(seq 1 60); do
    if docker exec nanomdm-mysql mysqladmin ping -h localhost --silent > /dev/null 2>&1; then
        # ping通了還不夠,確認02-create-users.sql真的執行完成(用能不能用nanomdm帳號登入來判斷)
        if docker exec nanomdm-mysql mysql -unanomdm -p"${DB_PASSWORD}" -e "SELECT 1;" nanomdm > /dev/null 2>&1; then
            log_ok "MySQL 已就緒,資料庫與帳號初始化完成"
            break
        fi
    fi
    if [ "$i" -eq 60 ]; then
        die "等待 MySQL 就緒逾時(等了60次、每次5秒),請執行 docker logs nanomdm-mysql 檢查問題"
    fi
    sleep 5
done

# =============================================================================
# 建立 SCEP 根 CA(必須在啟動scep容器前完成,容器啟動時需要ca.pem/ca.key已經存在於depot)
# =============================================================================
log_step "建立 SCEP 根 CA(效期15年,簽發者名稱使用單位縮寫)"

# 重要防呆:ca -init不會覆蓋已經存在的ca.key/ca.pem,直接回報「file exists」失敗。
# 如果這是重跑第二次,scep-depot目錄可能已經有上次殘留的CA檔案,這裡先偵測、
# 明確詢問使用者要不要清空重新建立,而不是讓它直接失敗。
if [ -f /opt/nanomdm-deployment/scep-depot/ca.key ] || [ -f /opt/nanomdm-deployment/scep-depot/ca.pem ]; then
    log_warn "偵測到 /opt/nanomdm-deployment/scep-depot 裡已經有殘留的CA檔案(可能是先前安裝過、或這是重跑第二次)。"
    log_warn "ca -init 不會覆蓋既有檔案,會直接失敗。"
    read -rp "  是否要清空這個目錄、重新建立CA?(會刪除現有的CA,所有已經用這張CA註冊過的裝置會失去信任鏈) (y/N): " wipe_scep_depot
    if [ "${wipe_scep_depot,,}" = "y" ]; then
        rm -rf /opt/nanomdm-deployment/scep-depot/*
        log_ok "已清空 scep-depot,將重新建立全新的CA"
    else
        log_warn "保留現有CA檔案,略過重新建立。"
        log_warn "注意:如果現有ca.key的加密密碼跟這次.env裡的SCEP_CA_PASSWORD不一致,nanomdm-scep容器稍後會啟動失敗(decryption password incorrect)"
    fi
fi

if [ ! -f /opt/nanomdm-deployment/scep-depot/ca.pem ]; then
    SCEP_IMAGE="ghcr.io/liao-chianan/scep:2026-08-pinned"
    SCEP_ENTRYPOINT="/usr/local/bin/scepserver-linux-amd64"

    docker run --rm \
        -v /opt/nanomdm-deployment/scep-depot:/depot \
        --entrypoint "$SCEP_ENTRYPOINT" \
        "$SCEP_IMAGE" \
        ca -init -depot /depot \
        -organization "$ORG_ABBR" \
        -organizational_unit "IT" \
        -country "TW" \
        -years 15 \
        -key-password "$SCEP_CA_PASSWORD" \
        -common_name "$ORG_ABBR" \
        || die "SCEP根CA建立失敗"

    [ -f /opt/nanomdm-deployment/scep-depot/ca.pem ] || die "SCEP根CA建立後找不到ca.pem,請檢查上面的錯誤訊息"
    log_ok "SCEP 根 CA 建立完成"
else
    log_ok "沿用現有的 SCEP 根 CA(未重新建立)"
fi

# =============================================================================
# 啟動其餘 Docker 服務
# =============================================================================
log_step "啟動 nanomdm / nanodep / nanoaxm / scep 容器"

docker compose up -d --force-recreate

echo "  等待所有容器啟動..."
sleep 10

for svc in nanomdm-server nanodep-server nanoaxm-server nanomdm-scep nanomdm-mysql; do
    status=$(docker inspect --format='{{.State.Status}}' "$svc" 2>/dev/null || echo "not-found")
    if [ "$status" = "running" ]; then
        log_ok "$svc 運作中"
    else
        log_warn "$svc 狀態異常: $status,請之後用 docker logs $svc 檢查"
    fi
done

# =============================================================================
# 套用 enroll-template.mobileconfig 裡跟基本裝置註冊功能有關的佔位符
# =============================================================================
log_step "設定精簡註冊描述檔(enroll-template.mobileconfig)"

ENROLL_TEMPLATE="/opt/nanomdm-deployment/mobileconfig/enroll-template.mobileconfig"

if [ -f "$ENROLL_TEMPLATE" ]; then
    sed -i \
        -e "s|REPLACE_WITH_YOUR_SCEP_CHALLENGE|${SCEP_CHALLENGE}|g" \
        -e "s|YOUR_ORG_ABBREVIATION|${ORG_ABBR}|g" \
        -e "s|YOUR_DOMAIN_HERE|${SERVER_DOMAIN}|g" \
        -e "s|YOUR_SCHOOL_NAME|${ORG_ABBR}|g" \
        "$ENROLL_TEMPLATE"
    log_ok "已套用 SCEP Challenge、組織縮寫、網域"
    log_warn "Topic欄位維持佔位值,之後在webui上傳APNs Push憑證後會自動同步成正確的值"
    log_warn "描述檔裡的組織顯示名稱暫時用單位縮寫「${ORG_ABBR}」代替,可以之後在webui「群組描述檔」頁面編輯成正式全名"
else
    log_warn "找不到 $ENROLL_TEMPLATE,略過這個步驟(請確認專案zip裡有這個檔案)"
fi

# =============================================================================
# 7. nginx + certbot 憑證申請
# =============================================================================
log_step "設定 nginx 並申請 Let's Encrypt 憑證"

NGINX_SITE="/etc/nginx/sites-available/${SERVER_DOMAIN}"

# 先部署一份「只有HTTP、沒有SSL」的簡化版設定,讓certbot能透過HTTP-01驗證取得憑證。
# 完整版設定(含所有proxy_pass規則)要等憑證真的存在之後才套用,不然nginx會因為
# ssl_certificate指向的檔案不存在而無法啟動(先有雞還是先有蛋的問題)。
cat > "$NGINX_SITE" << EOF
server {
    listen 80;
    server_name ${SERVER_DOMAIN};

    location / {
        return 200 'nanomdm-webui installer: nginx placeholder,waiting for certbot';
        add_header Content-Type text/plain;
    }
}
EOF

ln -sf "$NGINX_SITE" "/etc/nginx/sites-enabled/${SERVER_DOMAIN}"
[ -f /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default

nginx -t || die "nginx 簡化版設定測試失敗,請檢查 $NGINX_SITE"
systemctl reload nginx
log_ok "nginx 簡化版設定(僅HTTP)已套用"

echo "  正在透過 certbot 申請憑證(需要 ${SERVER_DOMAIN} 的DNS已經正確指向本機)..."
certbot --nginx -d "$SERVER_DOMAIN" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect \
    || die "certbot 申請憑證失敗。常見原因:DNS還沒生效、80port被防火牆擋住、或網域拼錯。可以之後手動執行 certbot --nginx -d ${SERVER_DOMAIN} 重試"

[ -f "/etc/letsencrypt/live/${SERVER_DOMAIN}/fullchain.pem" ] || die "certbot執行完成但找不到憑證檔案,請檢查 /var/log/letsencrypt/letsencrypt.log"
log_ok "Let's Encrypt 憑證申請成功"

# certbot --nginx 剛剛已經自動把SSL相關設定插入進$NGINX_SITE,但那份還只是簡化版(location /)。
# 現在憑證已經確定存在,換上完整版設定(套用nanomdm.nginx.example範本,含所有proxy_pass規則)。
NGINX_EXAMPLE="/opt/nanomdm-deployment/nanomdm.nginx.example"
if [ -f "$NGINX_EXAMPLE" ]; then
    sed "s|<你的站台domain name>|${SERVER_DOMAIN}|g" "$NGINX_EXAMPLE" > "$NGINX_SITE"
    nginx -t || die "完整版nginx設定測試失敗,請檢查 $NGINX_SITE(簡化版設定仍在/etc/letsencrypt備份中,可以手動還原)"
    systemctl reload nginx
    log_ok "完整版 nginx 設定(含所有服務的 proxy_pass 規則)已套用"
else
    log_warn "找不到 $NGINX_EXAMPLE,nginx 目前僅有 certbot 自動產生的簡化版設定,請手動補上完整設定"
fi

# =============================================================================
# 8. 部署 systemd 服務
# =============================================================================
log_step "部署 enroll-server / webhook-automation / nanodep-syncer 服務"

# 這三支的unit檔案已經是通用版本(webhook-automation.service跟nanodep-syncer.service
# 用EnvironmentFile讀取.env,不需要在這裡額外置換任何值),直接複製即可
for svc in enroll-server.service webhook-automation.service nanodep-syncer.service; do
    src="/opt/nanomdm-deployment/${svc}"
    if [ -f "$src" ]; then
        cp "$src" "/etc/systemd/system/${svc}"
        log_ok "已複製 ${svc}"
    else
        log_warn "找不到 $src,略過 ${svc}"
    fi
done

# nanodep-release底下的編譯執行檔(bypasscode/depsyncer/deptokens/depserver)刻意沒有
# 打包進zip裡(平台綁死、不是原始碼、官方更新也不會自動跟著更新),但nanodep-syncer.service
# 需要用到depsyncer這支執行檔,這裡直接向官方GitHub Releases下載最新版。
# 用GitHub API動態抓下載連結,不寫死版本號,避免官方發新版後連結失效。
DEPSYNCER_BIN="/opt/nanomdm-deployment/nanodep-release/depsyncer-linux-amd64"
if [ ! -f "$DEPSYNCER_BIN" ]; then
    echo "  正在從 nanodep 官方 GitHub Releases 下載 depsyncer-linux-amd64..."
    depsyncer_asset_url=$(curl -fsSL https://api.github.com/repos/micromdm/nanodep/releases/latest \
        | jq -r '.assets[] | select(.name | test("linux.*amd64")) | .browser_download_url' | head -1)

    if [ -z "$depsyncer_asset_url" ] || [ "$depsyncer_asset_url" = "null" ]; then
        log_warn "無法自動找到官方release的linux-amd64下載連結,請之後手動到 https://github.com/micromdm/nanodep/releases 下載並解壓縮 depsyncer-linux-amd64 到 $DEPSYNCER_BIN"
    else
        tmp_nanodep_release="/tmp/nanodep-release-download.zip"
        tmp_nanodep_extract="/tmp/nanodep-release-extract"
        curl -fsSL "$depsyncer_asset_url" -o "$tmp_nanodep_release" \
            && rm -rf "$tmp_nanodep_extract" && mkdir -p "$tmp_nanodep_extract" \
            && unzip -q "$tmp_nanodep_release" -d "$tmp_nanodep_extract" \
            && find "$tmp_nanodep_extract" -name "depsyncer-linux-amd64" -exec cp {} "$DEPSYNCER_BIN" \; \
            && rm -rf "$tmp_nanodep_release" "$tmp_nanodep_extract"

        if [ -f "$DEPSYNCER_BIN" ]; then
            log_ok "depsyncer-linux-amd64 下載完成"
        else
            log_warn "下載或解壓縮 depsyncer-linux-amd64 失敗,請之後手動到 https://github.com/micromdm/nanodep/releases 下載"
        fi
    fi
fi

chmod +x "$DEPSYNCER_BIN" 2>/dev/null || \
    log_warn "找不到 depsyncer-linux-amd64 執行檔,nanodep-syncer.service 可能無法啟動,請確認是否需要另外下載官方release"

# =============================================================================
# 9. 部署 nanomdm-webui 服務(先建立venv、安裝套件)
# =============================================================================
log_step "建立 nanomdm-webui 的 Python 虛擬環境"

cd /opt/nanomdm-webui
python3 -m venv venv || die "建立Python虛擬環境失敗,請確認 python3-venv 套件是否正確安裝"
./venv/bin/pip install --upgrade pip -q || log_warn "pip 升級失敗,繼續嘗試安裝套件"
if [ -f requirements.txt ]; then
    ./venv/bin/pip install -r requirements.txt -q || die "安裝 requirements.txt 套件失敗"
else
    log_warn "找不到 requirements.txt,手動安裝 Flask 與 requests"
    ./venv/bin/pip install "Flask>=3.0.0" "requests>=2.31.0" -q || die "安裝 Flask/requests 失敗"
fi
log_ok "Python 虛擬環境建立完成"

svc="nanomdm-webui.service"
src="/opt/nanomdm-deployment/${svc}"
if [ -f "$src" ]; then
    cp "$src" "/etc/systemd/system/${svc}"
    log_ok "已複製 ${svc}"
else
    log_warn "找不到 $src,略過 ${svc}"
fi

# =============================================================================
# 建立 webui 的管理者帳號
# =============================================================================
log_step "設定 nanomdm-webui 管理者帳號"
echo "接下來會進入互動式設定,請輸入 webui 的登入帳號密碼。"
echo "後面路徑相關的問題,因為已經是標準目錄結構,直接按 Enter 使用預設值即可。"
cd /opt/nanomdm-webui
./venv/bin/python3 scripts/setup_config.py

[ -f /opt/nanomdm-webui/webui_config.json ] || die "webui_config.json 沒有被正確建立,nanomdm-webui.service 稍後會無法啟動。請重新執行: cd /opt/nanomdm-webui && ./venv/bin/python3 scripts/setup_config.py"
log_ok "管理者帳號設定完成"

# =============================================================================
# 10. 啟用並啟動所有服務
# =============================================================================
log_step "啟用並啟動所有服務"

systemctl daemon-reload

for svc in enroll-server.service webhook-automation.service nanodep-syncer.service nanomdm-webui.service; do
    systemctl enable "$svc" > /dev/null 2>&1 || log_warn "$svc 設定開機自動啟動失敗(不影響這次是否能啟動)"
    systemctl restart "$svc" || true
    sleep 2
    if systemctl is-active --quiet "$svc"; then
        log_ok "$svc 已啟動"
    else
        log_warn "$svc 啟動失敗,請執行 journalctl -u $svc -n 50 檢查"
    fi
done

systemctl enable nginx > /dev/null 2>&1 || log_warn "nginx 設定開機自動啟動失敗(不影響目前運作狀態)"

# =============================================================================
# 完成
# =============================================================================
echo ""
echo "=============================================================================="
echo -e " ${C_GREEN}安裝完成${C_RESET}"
echo "=============================================================================="
echo ""
echo "  webui 網址:      https://${SERVER_DOMAIN}/miniweb/"
echo "  .env 位置:       /opt/nanomdm-deployment/.env(已設定權限僅root可讀寫)"
echo ""
echo "以下項目 webui 本身已經可以運作,但還需要你登入後手動完成才會有完整的裝置管理能力:"
echo "  1. APNs Push 憑證(到 identity.apple.com/pushcert 申請後,在「憑證狀態檢視」頁面上傳)"
echo "  2. VPP Content Token(到 school.apple.com 下載後上傳)"
echo "  3. NanoAXM 私鑰/OAuth憑證(到 school.apple.com 偏好設定→API 取得後設定)"
echo "  4. DEP Token(到 school.apple.com 偏好設定→裝置管理服務 下載後上傳)"
echo ""
echo "上傳 APNs 憑證後,系統會自動把 Topic 同步進精簡註冊描述檔,屆時裝置才能真正完成註冊。"
echo ""
echo "如果任何服務狀態異常,可以用以下指令個別檢查:"
echo "  journalctl -u nanomdm-webui.service -n 50"
echo "  journalctl -u webhook-automation.service -n 50"
echo "  journalctl -u enroll-server.service -n 50"
echo "  journalctl -u nanodep-syncer.service -n 50"
echo "  docker logs nanomdm-server / nanodep-server / nanoaxm-server / nanomdm-scep / nanomdm-mysql"
echo ""
echo "=============================================================================="
