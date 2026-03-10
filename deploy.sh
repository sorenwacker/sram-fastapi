#!/bin/bash
set -e

SERVER="sram-demo01.ewi"
REMOTE_DIR="/app/sram-fastapi"

echo "Syncing files to $SERVER..."
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' --exclude '.git' \
    -e ssh . app@$SERVER:$REMOTE_DIR/

echo "Installing dependencies..."
ssh app@$SERVER "cd $REMOTE_DIR && /app/.local/bin/uv sync --no-dev"

echo "Installing systemd service..."
ssh $SERVER "sudo cp $REMOTE_DIR/sram-demo.service /etc/systemd/system/ && sudo systemctl daemon-reload"

echo "Restarting service..."
ssh $SERVER "sudo systemctl enable sram-demo && sudo systemctl restart sram-demo"

echo "Checking status..."
ssh $SERVER "sudo systemctl status sram-demo --no-pager"

echo "Deployment complete"
