# Polymarket Bot — AWS Deployment Guide

## Prerequisites
- AWS account with EC2 access
- SSH key pair (.pem file)
- Local machine: rsync, ssh, python3

## 1. Launch EC2 Instance
- AMI: Ubuntu 22.04 LTS (free tier eligible)
- Instance type: t3.small (2 vCPU, 2 GB RAM) or larger
- Storage: 20 GB SSD
- Security group inbound rules:
  - SSH (22) — your IP only
  - HTTP (80) — 0.0.0.0/0

## 2. Initial Server Setup
```bash
# From your local machine, run setup.sh remotely:
ssh -i your-key.pem ubuntu@YOUR_EC2_IP "bash -s" < deploy/setup.sh
```

## 3. Upload Bot Files
```bash
bash deploy/upload.sh ubuntu@YOUR_EC2_IP
```

## 4. Configure Environment
```bash
ssh ubuntu@YOUR_EC2_IP
cd /opt/polybot
cp deploy/env.example .env
nano .env
```
Fill in all values (see env.example for descriptions).
Generate secret key: `python3 -c "import secrets; print(secrets.token_hex(32))"`

## 5. Run First Discovery
```bash
cd /opt/polybot
venv/bin/python discover_alpha.py --pool 100 --top 20
```
This takes ~5 minutes. Creates alpha_wallets.json and alpha_wallets_report.json.

## 6. Install Services
```bash
sudo bash /opt/polybot/deploy/install_service.sh
```
This creates 4 systemd services:
- **polybot-api**: Dashboard web server (port 8080, always running)
- **polybot-crypto**: Crypto market watcher (every 15 min)
- **polybot-discover**: Daily alpha wallet refresh (restarts every 24h)
- **polybot**: Main trading bot (start manually when ready)

## 7. Access Dashboard
Open `http://YOUR_EC2_IP` in your browser.
Login with DASHBOARD_USER/DASHBOARD_PASS from your .env.

## 8. Enable HTTPS (optional, requires a domain)
```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.com
```
Point your domain's A record to the EC2 IP first.

## 9. Start the Trading Bot
When ready to start paper trading:
```bash
sudo systemctl start polybot
sudo journalctl -u polybot -f
```

## Useful Commands
```bash
# Check service status
sudo systemctl status polybot-api

# View live logs
sudo journalctl -u polybot-api -f
sudo journalctl -u polybot -f

# Restart a service
sudo systemctl restart polybot-api

# Stop everything
sudo systemctl stop polybot polybot-api polybot-crypto polybot-discover
```

## Updating the Bot
```bash
# From local machine:
bash deploy/upload.sh ubuntu@YOUR_EC2_IP
# Then on server:
sudo systemctl restart polybot-api polybot
```

## Troubleshooting

**Dashboard not loading**: Check `sudo systemctl status polybot-api` and `sudo journalctl -u polybot-api -n 30`

**Port 8080 not responding**: Verify Flask is running: `curl http://localhost:8080/login`

**Wrong credentials**: Check DASHBOARD_USER and DASHBOARD_PASS in /opt/polybot/.env, restart polybot-api

**Discovery shows no wallets**: Run `venv/bin/python discover_alpha.py` manually and check for API errors

**UFW blocking connections**: `sudo ufw status` — ensure port 80 is open

## Security Notes
- Never commit .env to git (it's in .gitignore)
- Rotate FLASK_SECRET_KEY after first deploy (this invalidates all existing sessions)
- Use Elastic IP to keep a stable IP address
- Consider restricting port 22 to your IP only in the security group
- DASHBOARD_PASS should be at least 16 characters
