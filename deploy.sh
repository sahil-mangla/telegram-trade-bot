#!/bin/bash
# deploy.sh - Deploy Trade Bot to DigitalOcean Droplet

# Terminate on error
set -e

DROPLET_IP="68.183.95.99"
SSH_USER="root"
SSH_KEY="~/.ssh/digitalocean_tradebot"
REMOTE_DIR="/root/trade_bot"

echo "==========================================="
echo "🚀 Starting Deployment to DigitalOcean Droplet"
echo "🌐 Destination IP: $DROPLET_IP"
echo "==========================================="

# 1. Test SSH Connection
echo "🔑 Testing SSH Connection..."
ssh -i $SSH_KEY -o StrictHostKeyChecking=no $SSH_USER@$DROPLET_IP "echo '✅ SSH connection verified!'"

# 2. Sync files to the droplet using rsync
echo "🔄 Syncing files to remote droplet..."
# Ensure the remote directory exists
ssh -i $SSH_KEY $SSH_USER@$DROPLET_IP "mkdir -p $REMOTE_DIR"

# Rsync options:
# -a: archive mode (preserves permissions, times, symlinks)
# -v: verbose
# -z: compress during transfer
# --delete: delete extraneous files from destination dirs (to keep remote clean)
rsync -avz -e "ssh -i $SSH_KEY" \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude '.git/' \
  --exclude 'logs/' \
  --exclude 'trades.db' \
  --exclude '.DS_Store' \
  ./ $SSH_USER@$DROPLET_IP:$REMOTE_DIR/

echo "✅ Sync complete!"

# 3. Setup remote environment and systemd service
echo "⚙️  Configuring dependencies and services on the remote droplet..."
ssh -i $SSH_KEY $SSH_USER@$DROPLET_IP "/bin/bash -s" << 'EOF'
  set -e
  
  echo "📦 Updating apt and installing python3-venv..."
  apt-get update -y
  apt-get install -y python3 python3-pip python3-venv rsync

  # Go to the trade_bot directory
  cd /root/trade_bot

  # Create virtual environment if it doesn't exist
  if [ ! -d "venv" ]; then
    echo "🐍 Creating virtual environment..."
    python3 -m venv venv
  fi

  # Install requirements
  echo "📥 Installing python packages inside virtual environment..."
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt

  # Ensure logs directory exists
  mkdir -p logs

  # Install Systemd service
  echo "📌 Installing systemd service..."
  cp tradebot.service /etc/systemd/system/tradebot.service
  
  # Reload daemon and restart service
  echo "🔄 Restarting systemd service..."
  systemctl daemon-reload
  systemctl enable tradebot
  systemctl restart tradebot

  echo "🟢 Checking service status..."
  systemctl status tradebot --no-pager | head -n 20
EOF

echo "==========================================="
echo "🎉 Deployment completed successfully!"
echo "==========================================="
