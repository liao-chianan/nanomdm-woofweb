#!/bin/bash
#
# check-cert-expiry.sh
# 一次查詢自建 MDM 系統所有憑證/Token 的到期日
# 涵蓋：nginx (Let's Encrypt)、SCEP CA、APNs Push 憑證、DEP OAuth Token、VPP Content Token
#
# 使用方式：
#   cd /opt/nanomdm-deployment
#   source .env
#   ./check-cert-expiry.sh

set -uo pipefail

# ===== 自動載入 .env，不依賴呼叫者事先 source =====
# 這樣即使在不同的終端機 session 執行，也不會因為忘記 source .env 而查詢失敗
ENV_FILE="/opt/nanomdm-deployment/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "警告：找不到 $ENV_FILE，部分查詢可能因缺少密碼/金鑰而失敗"
fi

# ===== 顏色設定 =====
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

# ===== 路徑設定：請依實際環境調整 =====
DEPLOY_DIR="/opt/nanomdm-deployment"
SCEP_CA_PATH="${DEPLOY_DIR}/scep-depot/ca.pem"
NGINX_CERT_PATH="${NGINX_CERT_PATH:-/etc/letsencrypt/live/YOUR_DOMAIN_HERE/fullchain.pem}"
VPP_TOKEN_FILE="${VPP_TOKEN_PATH:-${DEPLOY_DIR}/vpp_token.vpptoken}"
DEP_NAME="${NANODEP_NAME:-}"
AXM_NAME="${NANOAXM_NAME:-}"
AXM_BASE_URL="http://127.0.0.1:9005"
# =====================================

TODAY_EPOCH=$(date +%s)

print_row() {
    local name="$1"
    local expiry_date="$2"   # 格式須為可被 date -d 解析的字串
    local raw_info="${3:-}"

    if [ -z "$expiry_date" ] || [ "$expiry_date" == "null" ]; then
        printf "%-28s %-35s\n" "$name" "查詢失敗或無資料"
        return
    fi

    local expiry_epoch
    expiry_epoch=$(date -d "$expiry_date" +%s 2>/dev/null)
    if [ -z "$expiry_epoch" ]; then
        printf "%-28s %-35s\n" "$name" "日期格式無法解析: $expiry_date"
        return
    fi

    local days_left=$(( (expiry_epoch - TODAY_EPOCH) / 86400 ))
    local color="$GREEN"
    local warn=""

    if [ "$days_left" -lt 0 ]; then
        color="$RED"; warn=" [已過期]"
    elif [ "$days_left" -le 14 ]; then
        color="$RED"; warn=" [緊急：14天內到期]"
    elif [ "$days_left" -le 30 ]; then
        color="$YELLOW"; warn=" [注意：30天內到期]"
    fi

    printf "%-28s ${color}%-25s 剩餘 %4d 天%s${NC}\n" "$name" "$expiry_date" "$days_left" "$warn"
    [ -n "$raw_info" ] && echo "    └─ $raw_info"
}

# 用於沒有明確到期日、只能靠實際呼叫 API 驗證是否還有效的項目
# （例如 NanoAXM 的私鑰／OAuth 憑證：Apple 官方文件顯示私鑰本身不會自動到期，
#  只會因手動撤銷而失效，因此改用健康檢查方式確認目前是否仍可正常運作）
print_status() {
    local name="$1"
    local ok="$2"   # "true" 或 "false"
    local raw_info="${3:-}"

    if [ "$ok" == "true" ]; then
        printf "%-28s ${GREEN}%-25s${NC}\n" "$name" "正常（API 呼叫成功）"
    else
        printf "%-28s ${RED}%-25s${NC}\n" "$name" "異常（API 呼叫失敗，請檢查）"
    fi
    [ -n "$raw_info" ] && echo "    └─ $raw_info"
}

echo "============================================================"
echo " 自建 MDM 系統 - 憑證/Token 效期總覽"
echo " 查詢時間: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
echo ""

# ---------- 1. nginx / Let's Encrypt ----------
if [ -f "$NGINX_CERT_PATH" ]; then
    enddate=$(openssl x509 -in "$NGINX_CERT_PATH" -noout -enddate 2>/dev/null | cut -d= -f2)
    print_row "nginx (Let's Encrypt)" "$enddate"
else
    printf "%-28s %s\n" "nginx (Let's Encrypt)" "找不到憑證檔案: $NGINX_CERT_PATH"
fi

# ---------- 2. SCEP 根 CA ----------
if [ -f "$SCEP_CA_PATH" ]; then
    enddate=$(openssl x509 -in "$SCEP_CA_PATH" -noout -enddate 2>/dev/null | cut -d= -f2)
    print_row "SCEP 根 CA (自簽)" "$enddate" "此為 PKI 信任鏈根憑證，過期影響範圍最大"
else
    printf "%-28s %s\n" "SCEP 根 CA" "找不到憑證檔案: $SCEP_CA_PATH"
fi

# ---------- 3. APNs Push 憑證 ----------
# 註：NanoMDM v0.9.0 的 GET /v1/pushcert?topic=... 查詢端點目前有已知問題
# （回傳 "tls: failed to find any PEM data" 錯誤），改為直接從 MySQL 讀取
# cert_pem 欄位內容，自行用 openssl 計算到期日，繞開該端點。
# topic 值改為動態查詢，不寫死，避免未來重新申請憑證後 topic 改變導致查詢落空。
if command -v docker >/dev/null 2>&1; then
    topics=$(docker exec -i nanomdm-mysql mysql -unanomdm -p"${NANOMDM_DB_PASSWORD:-}" nanomdm \
        -N -e "SELECT topic FROM push_certs;" 2>/dev/null)

    if [ -z "$topics" ]; then
        printf "%-28s %s\n" "APNs Push 憑證" "資料庫中查無任何 push_certs 紀錄"
    else
        while IFS= read -r topic; do
            [ -z "$topic" ] && continue
            cert_pem=$(docker exec -i nanomdm-mysql mysql -unanomdm -p"${NANOMDM_DB_PASSWORD:-}" nanomdm \
                -N -e "SELECT cert_pem FROM push_certs WHERE topic='${topic}';" 2>/dev/null)
            if [ -n "$cert_pem" ]; then
                not_after=$(echo "$cert_pem" | sed 's/\\n/\n/g' | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
                print_row "APNs Push 憑證" "$not_after" "topic=${topic}（動態查詢，直接讀取資料庫）"
            fi
        done <<< "$topics"
    fi
else
    printf "%-28s %s\n" "APNs Push 憑證" "跳過（找不到 docker 指令）"
fi

# ---------- 4. DEP OAuth Token ----------
if command -v docker >/dev/null 2>&1; then
    dep_expiry=$(docker exec -i nanomdm-mysql mysql -unanodep -p"${NANODEP_DB_PASSWORD:-}" nanodep \
        -N -e "SELECT access_token_expiry FROM dep_names WHERE name='${DEP_NAME}';" 2>/dev/null)
    print_row "DEP OAuth Token" "$dep_expiry" "此值僅供參考，曾出現提前失效情況，建議搭配定期健康檢查"
else
    printf "%-28s %s\n" "DEP OAuth Token" "跳過（找不到 docker 指令）"
fi

# ---------- 5. VPP Content Token ----------
if [ -f "$VPP_TOKEN_FILE" ]; then
    vpp_json=$(base64 -d "$VPP_TOKEN_FILE" 2>/dev/null)
    vpp_expiry=$(echo "$vpp_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('expDate',''))" 2>/dev/null)
    org_name=$(echo "$vpp_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('orgName',''))" 2>/dev/null)
    print_row "VPP Content Token" "$vpp_expiry" "組織名稱: ${org_name}"
else
    printf "%-28s %s\n" "VPP Content Token" "找不到檔案: $VPP_TOKEN_FILE"
fi

# ---------- 6. NanoAXM 私鑰 / OAuth 憑證（健康檢查）----------
# 官方文件顯示此私鑰本身沒有自動到期機制，只會因手動撤銷而失效，
# 因此無法查詢「到期日」，改用實際呼叫 API 的方式驗證目前是否仍然有效。
if [ -n "${NANOAXM_API_KEY:-}" ]; then
    axm_resp=$(curl -s -o /dev/null -w "%{http_code}" -u "nanoaxm:${NANOAXM_API_KEY}" \
        "${AXM_BASE_URL}/proxy/school/${AXM_NAME}/v1/mdmServers" 2>/dev/null)
    if [ "$axm_resp" == "200" ]; then
        print_status "NanoAXM 私鑰/OAuth憑證" "true" "AXM_NAME=${AXM_NAME}（HTTP ${axm_resp}）"
    else
        print_status "NanoAXM 私鑰/OAuth憑證" "false" "HTTP 狀態碼: ${axm_resp}，請確認私鑰是否遭撤銷或 NanoAXM 服務是否正常"
    fi
else
    printf "%-28s %s\n" "NanoAXM 私鑰/OAuth憑證" "跳過（未設定 NANOAXM_API_KEY）"
fi

echo ""
echo "============================================================"
echo " 圖例：綠色=正常　黃色=30天內到期　紅色=14天內到期或已過期"
echo " NanoAXM 私鑰無到期日可查，改以實際呼叫 API 驗證是否仍有效"
echo "============================================================"
