#!/bin/bash
# === 部署腳本 ===

# 本機專案目錄
LOCAL_DIR="/Users/andyyang/PycharmProjects/queuepad-display"
# 樹莓派目標目錄
REMOTE_DIR="/home/pi/queuepad-display"
# 樹莓派登入資訊
PI_HOST="pi@queuepad-pi.local"
PI_PASS="yellowgirl"
PI_SERVICE="queuepad.service"

echo "🚀 正在上傳專案到 Raspberry Pi..."
sshpass -p "$PI_PASS" scp -r "$LOCAL_DIR/"* $PI_HOST:$REMOTE_DIR/

if [ $? -eq 0 ]; then
    echo "✅ 檔案上傳完成，正在重啟服務..."
    sshpass -p "$PI_PASS" ssh -o StrictHostKeyChecking=no $PI_HOST "sudo systemctl restart $PI_SERVICE && sudo systemctl status $PI_SERVICE --no-pager -l"
    echo "🎉 部署完成！"
else
    echo "❌ 上傳失敗，請檢查路徑或網路連線。"
fi
