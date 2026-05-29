#!/bin/bash
# Polybot AWS Ubuntu 22.04 setup — run once as ubuntu user with sudo
set -e
BOT_DIR=/opt/polybot
BOT_USER=polybot

echo "==> Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv nginx ufw htop

echo "==> Creating polybot system user and directory..."
sudo useradd -r -m -s /bin/bash $BOT_USER 2>/dev/null || true
sudo mkdir -p $BOT_DIR
sudo chown $BOT_USER:$BOT_USER $BOT_DIR

echo "==> Configuring nginx..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
sudo cp "$SCRIPT_DIR/nginx.conf" /etc/nginx/sites-available/polybot
sudo ln -sf /etc/nginx/sites-available/polybot /etc/nginx/sites-enabled/polybot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx

echo "==> Configuring firewall..."
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx HTTP'
sudo ufw --force enable

echo ""
echo "Setup complete. Next steps:"
echo "   1. Upload files:  bash deploy/upload.sh ubuntu@YOUR_EC2_IP"
echo "   2. SSH in and create .env: cp $BOT_DIR/deploy/env.example $BOT_DIR/.env && nano $BOT_DIR/.env"
echo "   3. Install services: sudo bash $BOT_DIR/deploy/install_service.sh"
echo "   4. Open: http://YOUR_EC2_IP"
