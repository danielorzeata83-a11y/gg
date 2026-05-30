#!/bin/bash
# Polybot service installer — run on the AWS server after uploading files
set -e
BOT_DIR=/opt/polybot
BOT_USER=polybot
VENV=$BOT_DIR/venv

echo "==> Setting up Python venv..."
sudo -u $BOT_USER python3 -m venv $VENV
sudo -u $BOT_USER $VENV/bin/pip install -q --upgrade pip
sudo -u $BOT_USER $VENV/bin/pip install -q -r $BOT_DIR/requirements.txt

echo "==> Creating systemd services..."

sudo tee /etc/systemd/system/polybot.service > /dev/null <<EOF
[Unit]
Description=Polybot – on-chain watcher + paper trading bot
After=network.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$VENV/bin/python bot.py --rpc \${POLYGON_RPC_URL} --mode \${BOT_MODE}
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/polybot-api.service > /dev/null <<EOF
[Unit]
Description=Polybot – Flask dashboard API
After=network.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$VENV/bin/python api_server.py --port 8080
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/polybot-crypto.service > /dev/null <<EOF
[Unit]
Description=Polybot – crypto market watcher (15 min)
After=network.target

[Service]
Type=simple
User=$BOT_USER
WorkingDirectory=$BOT_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=$VENV/bin/python crypto_watcher.py --interval 900
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/polybot-discover.service > /dev/null <<'EOF'
[Unit]
Description=Polybot – daily alpha wallet discovery
After=network.target

[Service]
Type=simple
User=polybot
WorkingDirectory=/opt/polybot
EnvironmentFile=/opt/polybot/.env
ExecStart=/opt/polybot/venv/bin/python discover_alpha.py --pool 100 --top 20
Restart=always
RestartSec=86400
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

echo "==> Enabling and starting services..."
sudo systemctl daemon-reload
sudo systemctl enable polybot polybot-api polybot-crypto polybot-discover
sudo systemctl start polybot-api polybot-crypto polybot-discover

echo ""
echo "Services installed. Dashboard at http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_EC2_IP')"
echo ""
echo "Useful commands:"
echo "  sudo systemctl status polybot-api"
echo "  sudo journalctl -u polybot-api -f"
echo "  sudo systemctl start polybot   # start the main trading bot when ready"
