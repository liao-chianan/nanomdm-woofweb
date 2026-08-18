#!/bin/bash

NANOMDM_API_KEY="xORdjJQYf8DEwdOh85JSvE5aVodLBRNaaFKxj9ysIRI="
DEVICE_UDID="00008120-000C559A2E080032"
NANOMDM_URL="http://127.0.0.1:9000"

# 定義要刪除的內建 App Bundle ID 清單
APP_IDS=(
  "com.apple.Music"        # 音樂
  "com.apple.tv"           # TV
  "com.apple.stocks"       # 股市
  "com.apple.Home"         # 家庭
  "com.apple.Health"       # 健康
  "com.apple.tips"         # 提示
  "com.apple.Photo-Booth"  # Photo Booth
  "com.apple.arcade"       # Apple Arcade (遊戲)
)

echo "開始批次發送刪除指令..."

for BUNDLE_ID in "${APP_IDS[@]}"; do
  UUID=$(python3 -c "import uuid; print(uuid.uuid4())")
  
  # 動態產生 plist 並直接透過管道 (pipe) 發送到 NanoMDM
  cat << EOF | curl -s -T - -u "nanomdm:${NANOMDM_API_KEY}" "${NANOMDM_URL}/v1/enqueue/${DEVICE_UDID}"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
        <key>Command</key>
        <dict>
                <key>RequestType</key>
                <string>RemoveApplication</string>
                <key>Identifier</key>
                <string>${BUNDLE_ID}</string>
        </dict>
        <key>CommandUUID</key>
        <string>${UUID}</string>
</dict>
</plist>
EOF

  echo "已排入刪除佇列: ${BUNDLE_ID} -- ${UUID}"
done

# 最後發送一次 APNs 推播通知裝置喚醒並執行佇列
curl -u "nanomdm:${NANOMDM_API_KEY}" -X POST "${NANOMDM_URL}/v1/push/${DEVICE_UDID}"

echo -e "\n全部刪除指令已發送完成！"
