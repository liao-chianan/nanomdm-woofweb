#!/bin/bash

# 自動載入 .env，不依賴呼叫者事先 source
ENV_FILE="/opt/nanomdm-deployment/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

VPP_TOKEN_FILE="${VPP_TOKEN_PATH:-/opt/nanomdm-deployment/vpp_token.vpptoken}"

if [ ! -f "$VPP_TOKEN_FILE" ]; then
    echo "❌ 找不到 $VPP_TOKEN_FILE 檔案！"
    exit 1
fi

STOKEN=$(cat "$VPP_TOKEN_FILE")

echo "1. 正在查詢 Location 下的 App 清單 (getVPPAssetsSrv)..."
ASSETS_JSON=$(curl -s -X POST https://vpp.itunes.apple.com/mdm/getVPPAssetsSrv \
  -H "Content-Type: application/json" \
  -d "{\"sToken\": \"$STOKEN\"}")

# 提取所有 Adam ID 到陣列中
ADAM_IDS_ARRAY=($(echo "$ASSETS_JSON" | jq -r '.assets[]?.adamIdStr // empty'))

if [ ${#ADAM_IDS_ARRAY[@]} -eq 0 ]; then
    echo "⚠️ 找不到任何 App 授權。"
    exit 0
fi

# 🎯 優化一：批量呼叫 iTunes API (一次性取得所有 App 中文名稱與 Bundle ID)
echo "2. 批量撈取 App 中文名稱與 Bundle ID..."
COMMA_ADAM_IDS=$(IFS=,; echo "${ADAM_IDS_ARRAY[*]}")
LOOKUP_JSON=$(curl -s "https://itunes.apple.com/lookup?id=${COMMA_ADAM_IDS}&country=tw&lang=zh_tw")

# 將 Lookup 結果轉為 JSON key-value 對照表
LOOKUP_MAP=$(echo "$LOOKUP_JSON" | jq -c '
  [.results[]? | {key: (.trackId | tostring), value: {bundleId: .bundleId, name: .trackName}}]
  | from_entries
')

echo -e "\n3. 並行查詢 VPP 授權數量中..."
echo "=========================================================================================================="
printf "%-12s | %-32s | %-28s | %-8s | %-8s\n" "Adam ID" "Identifier (Bundle ID)" "軟體中文名稱" "總數量" "剩餘量"
echo "=========================================================================================================="

# 建立臨時資料夾存放各 App 的處理結果，確保輸出順序不亂
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# 定義單一 App 的 VPP 查詢函數
fetch_license() {
    local ADAM_ID="$1"
    local TOTAL=0
    local ASSIGNED=0
    local BATCH_TOKEN=""

    while : ; do
        if [ -z "$BATCH_TOKEN" ]; then
            PAYLOAD=$(jq -n --arg stoken "$STOKEN" --arg adamId "$ADAM_ID" '{sToken: $stoken, adamId: $adamId}')
        else
            PAYLOAD=$(jq -n --arg stoken "$STOKEN" --arg batchToken "$BATCH_TOKEN" '{sToken: $stoken, batchToken: $batchToken}')
        fi

        LICENSES_JSON=$(curl -s -X POST https://vpp.itunes.apple.com/mdm/getVPPLicensesSrv \
          -H "Content-Type: application/json" \
          -d "$PAYLOAD")

        PAGE_TOTAL=$(echo "$LICENSES_JSON" | jq '.licenses // [] | length')
        PAGE_ASSIGNED=$(echo "$LICENSES_JSON" | jq '[.licenses[]? | select(.status == "Associated")] | length')

        TOTAL=$((TOTAL + PAGE_TOTAL))
        ASSIGNED=$((ASSIGNED + PAGE_ASSIGNED))

        BATCH_TOKEN=$(echo "$LICENSES_JSON" | jq -r '.batchToken // empty')

        if [ -z "$BATCH_TOKEN" ]; then
            break
        fi
    done

    local AVAILABLE=$((TOTAL - ASSIGNED))

    # 從 LOOKUP_MAP 對照表中讀取 App 名稱與 Bundle ID
    local BUNDLE_ID=$(echo "$LOOKUP_MAP" | jq -r --arg id "$ADAM_ID" '.[$id].bundleId // "未知"')
    local NAME=$(echo "$LOOKUP_MAP" | jq -r --arg id "$ADAM_ID" '.[$id].name // "未知"')

    # 將格式化結果寫入臨時檔案
    printf "%-12s | %-32s | %-28s | %-8s | %-8s\n" "$ADAM_ID" "$BUNDLE_ID" "$NAME" "$TOTAL" "$AVAILABLE" > "$TMP_DIR/$ADAM_ID"
}

# 🎯 優化二：多線程並行查詢 (最多同時發起 10 個連線)
MAX_JOBS=10

for ADAM_ID in "${ADAM_IDS_ARRAY[@]}"; do
    fetch_license "$ADAM_ID" &

    # 控制背景任務數量，避免將 API 塞爆
    while [ $(jobs -r | wc -l) -ge $MAX_JOBS ]; do
        sleep 0.05
    done
done

# 等待所有背景連線完成
wait

# 依照原始清單順序印出最終結果
for ADAM_ID in "${ADAM_IDS_ARRAY[@]}"; do
    if [ -f "$TMP_DIR/$ADAM_ID" ]; then
        cat "$TMP_DIR/$ADAM_ID"
    fi
done
