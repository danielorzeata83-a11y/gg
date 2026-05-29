#!/bin/bash
# One-shot setup script for Ubuntu 22.04 on AWS EC2
# Run as: bash deploy/setup.sh

set -e
echo "=== Polymarket Bot — AWS Ubuntu Setup ==="

# System deps
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv nginx certbot python3-certbot-nginx

# Create bot user
sudo useradd -m -s /bin/bash polybot 2>/dev/null || true

# App directory
sudo mkdir -p /opt/polybot
sudo chown polybot:polybot /opt/polybot

echo "Copy your bot files to /opt/polybot/ then run:"
echo "  sudo bash deploy/install_service.sh"
