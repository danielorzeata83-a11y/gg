#!/bin/bash
# Upload bot files to EC2. Usage: bash deploy/upload.sh ubuntu@YOUR_EC2_IP
HOST=${1:?Usage: bash deploy/upload.sh ubuntu@YOUR_EC2_IP}
FILES=(
    bot.py config.py ledger.py decision.py signal_log.py
    executor_paper.py executor_live.py api_server.py
    watch_onchain.py discover_alpha.py grid_scanner.py crypto_watcher.py
    requirements.txt dashboard.html alpha_report.html
)
echo "Uploading to $HOST:/opt/polybot/ ..."
ssh $HOST "sudo mkdir -p /opt/polybot && sudo chown \$USER:\$USER /opt/polybot"
scp "${FILES[@]}" $HOST:/opt/polybot/
echo "Done. Now on the server:"
echo "  1. Create /opt/polybot/.env with your keys"
echo "  2. bash deploy/install_service.sh YOUR_DOMAIN_OR_IP"
