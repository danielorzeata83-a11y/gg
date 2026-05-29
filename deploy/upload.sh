#!/bin/bash
# Usage: bash deploy/upload.sh ubuntu@YOUR_EC2_IP
set -e
HOST=${1:?Usage: bash deploy/upload.sh ubuntu@YOUR_EC2_IP}
BOT_DIR=/opt/polybot

echo "==> Preparing remote directory..."
ssh "$HOST" "sudo mkdir -p $BOT_DIR && sudo chown \$(whoami):\$(whoami) $BOT_DIR"

echo "==> Syncing files to $HOST:$BOT_DIR/ ..."
rsync -avz --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.jsonl' \
  --exclude='.env' \
  --exclude='venv/' \
  --exclude='alpha_wallets*.json' \
  . "$HOST:$BOT_DIR/"

echo ""
echo "Upload complete."
echo ""
echo "Next steps:"
echo "  1. ssh $HOST"
echo "  2. cd $BOT_DIR"
echo "  3. cp deploy/env.example .env && nano .env    # fill in your keys"
echo "  4. sudo bash deploy/install_service.sh"
echo "  5. Open http://\$(ssh $HOST curl -s ifconfig.me 2>/dev/null)"
