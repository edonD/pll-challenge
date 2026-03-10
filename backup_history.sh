#!/usr/bin/env bash
# Backs up full experiment history from the running PLL challenge
# Usage: ./backup_history.sh
# Or for remote: ssh <instance> 'cd pll-challenge && ./backup_history.sh'

set -euo pipefail

BACKUP_DIR="backups/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

# 1. Back up results files
cp -v results.tsv "$BACKUP_DIR/" 2>/dev/null || echo "No results.tsv"
cp -v results.md "$BACKUP_DIR/" 2>/dev/null || echo "No results.md"
cp -v results.jsonl "$BACKUP_DIR/" 2>/dev/null || echo "No results.jsonl"

# 2. Back up all design.cir versions from git history
mkdir -p "$BACKUP_DIR/circuit_snapshots"
# Get all commits that modified design.cir
git log --oneline --follow -- design.cir | while read hash msg; do
    git show "$hash:design.cir" > "$BACKUP_DIR/circuit_snapshots/${hash}_design.cir" 2>/dev/null || true
done

# 3. Back up experiment history database (if exists)
cp -v experiment_history.json "$BACKUP_DIR/" 2>/dev/null || true

# 4. Back up run logs
mkdir -p "$BACKUP_DIR/logs"
cp -v *.log "$BACKUP_DIR/logs/" 2>/dev/null || true
cp -v metrics.txt "$BACKUP_DIR/" 2>/dev/null || true

# 5. Back up waveform data (compressed)
if ls *_waveform.txt 1>/dev/null 2>&1; then
    tar czf "$BACKUP_DIR/waveforms.tar.gz" *_waveform.txt
    echo "Waveforms compressed to $BACKUP_DIR/waveforms.tar.gz"
fi

# 6. Git log summary
git log --oneline -100 > "$BACKUP_DIR/git_log.txt"
git log --format="%H %s" --all > "$BACKUP_DIR/full_git_log.txt"

# 7. Summary
echo ""
echo "=== Backup Complete ==="
echo "Location: $BACKUP_DIR"
echo "Contents:"
ls -la "$BACKUP_DIR/"
echo ""
du -sh "$BACKUP_DIR"
