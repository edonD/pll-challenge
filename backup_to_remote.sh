#!/usr/bin/env bash
# Pull experiment history from AWS EC2
# Usage: ./backup_to_remote.sh <ec2-host> [ssh-key]
set -euo pipefail

HOST="${1:?Usage: $0 <ec2-host> [ssh-key]}"
KEY="${2:-}"

SSH_OPTS=""
if [ -n "$KEY" ]; then
    SSH_OPTS="-i $KEY"
fi

REMOTE_DIR="pll-challenge"
LOCAL_BACKUP="backups/remote_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOCAL_BACKUP"

echo "Pulling experiment data from $HOST..."

# Pull key files
for f in results.tsv results.md results.jsonl experiment_history.json metrics.txt; do
    scp $SSH_OPTS "$HOST:$REMOTE_DIR/$f" "$LOCAL_BACKUP/" 2>/dev/null && echo "  Got $f" || echo "  Skip $f (not found)"
done

# Pull all circuit snapshots via git
scp $SSH_OPTS "$HOST:$REMOTE_DIR/design.cir" "$LOCAL_BACKUP/" 2>/dev/null || true

# Pull logs
scp $SSH_OPTS "$HOST:$REMOTE_DIR/*.log" "$LOCAL_BACKUP/" 2>/dev/null || true

echo ""
echo "Backup saved to: $LOCAL_BACKUP"
ls -la "$LOCAL_BACKUP/"
