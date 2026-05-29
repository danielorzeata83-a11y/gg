# AWS Ubuntu Deployment

## 1. Launch EC2
- Ubuntu 22.04 LTS, t3.small or larger
- Security group: open port 80 (HTTP) and 22 (SSH)
- Elastic IP recommended

## 2. Upload files
```bash
bash deploy/upload.sh ubuntu@YOUR_EC2_IP
```

## 3. Create .env on server
```bash
ssh ubuntu@YOUR_EC2_IP
nano /opt/polybot/.env
```
```
POLYGON_RPC_URL=https://polygon-mainnet.g.alchemy.com/v2/YOUR_KEY
BANKROLL_USDC=1000
```

## 4. Run watchlist discovery (once)
```bash
ssh ubuntu@YOUR_EC2_IP
cd /opt/polybot
python3 discover_alpha.py --pool 100 --top 20
python3 grid_scanner.py --markets 20 --top 30
```

## 5. Install services
```bash
bash deploy/install_service.sh YOUR_EC2_IP
```

## 6. Open dashboard
http://YOUR_EC2_IP — login with admin / password you set

## Useful commands
```bash
sudo systemctl status polybot polybot-api polybot-crypto
sudo journalctl -u polybot -f          # bot logs live
sudo journalctl -u polybot-crypto -f   # crypto watcher logs
sudo systemctl restart polybot         # restart after config change
```
