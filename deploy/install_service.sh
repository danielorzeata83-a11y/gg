#!/bin/bash
# Installs the bot as systemd services + nginx reverse proxy
# Run AFTER copying files to /opt/polybot/ and creating /opt/polybot/.env

set -e
BOT_DIR=/opt/polybot

# Python venv
python3 -m venv $BOT_DIR/venv
$BOT_DIR/venv/bin/pip install -q -r $BOT_DIR/requirements.txt

# ---- systemd: bot ----
sudo tee /etc/systemd/system/polybot.service > /dev/null <<EOF
[Unit]
Description=Polymarket Copy-Trading Bot
After=network.target

[Service]
Type=simple
User=polybot
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$BOT_DIR/venv/bin/python bot.py --mode paper --bankroll \${BANKROLL_USDC}
Restart=on-failure
RestartSec=10
StandardOutput=append:$BOT_DIR/bot.log
StandardError=append:$BOT_DIR/bot.log

[Install]
WantedBy=multi-user.target
EOF

# ---- systemd: api server ----
sudo tee /etc/systemd/system/polybot-api.service > /dev/null <<EOF
[Unit]
Description=Polymarket Bot Dashboard API
After=network.target polybot.service

[Service]
Type=simple
User=polybot
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python api_server.py --port 5000
Restart=on-failure
RestartSec=5
StandardOutput=append:$BOT_DIR/api.log
StandardError=append:$BOT_DIR/api.log

[Install]
WantedBy=multi-user.target
EOF

# ---- systemd: crypto watcher ----
sudo tee /etc/systemd/system/polybot-crypto.service > /dev/null <<EOF
[Unit]
Description=Polymarket Crypto Market Watcher (15min)
After=network.target

[Service]
Type=simple
User=polybot
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/venv/bin/python crypto_watcher.py --interval 900
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

# ---- nginx ----
DOMAIN=${1:-YOUR_SERVER_IP}
sudo tee /etc/nginx/sites-available/polybot > /dev/null <<EOF
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;

        # Basic auth — protect the dashboard
        auth_basic "Polybot Dashboard";
        auth_basic_user_file /etc/nginx/.htpasswd;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/polybot /etc/nginx/sites-enabled/polybot
sudo rm -f /etc/nginx/sites-enabled/default

# Create htpasswd if missing
if [ ! -f /etc/nginx/.htpasswd ]; then
    echo "Creating dashboard password..."
    sudo apt-get install -y -qq apache2-utils
    sudo htpasswd -c /etc/nginx/.htpasswd admin
fi

sudo nginx -t && sudo systemctl reload nginx

# Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable polybot polybot-api polybot-crypto
sudo systemctl start polybot polybot-api polybot-crypto

echo ""
echo "=== Done! ==="
echo "Dashboard: http://$DOMAIN  (login: admin / password you set)"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status polybot"
echo "  sudo journalctl -u polybot -f"
echo "  sudo systemctl restart polybot"
