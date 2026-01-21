#!/bin/bash
# groundwave-ai - Backup Script
# Creates a timestamped backup of the entire project including database

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR=~/backups
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_FILE="$BACKUP_DIR/groundwave-backup-$TIMESTAMP.tar.gz"

echo "Creating groundwave-ai backup..."
echo ""

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Create backup
cd "$(dirname "$SCRIPT_DIR")" && tar -czf "$BACKUP_FILE" \
  --exclude='groundwave-ai/venv' \
  --exclude='groundwave-ai/__pycache__' \
  --exclude='groundwave-ai/**/__pycache__' \
  --exclude='groundwave-ai/.pytest_cache' \
  --exclude='groundwave-ai/research' \
  --exclude='groundwave-ai/data/kiwix/kiwix-tools*' \
  --exclude='groundwave-ai/*.log' \
  "$(basename "$SCRIPT_DIR")"

if [ $? -eq 0 ]; then
    echo "Backup created successfully"
    echo ""
    echo "Location: $BACKUP_FILE"
    echo "Size: $(du -h "$BACKUP_FILE" | cut -f1)"
    echo ""
    
    # Show list of all backups
    echo "All backups:"
    ls -lh "$BACKUP_DIR"/groundwave-backup-*.tar.gz 2>/dev/null | awk '{print "  " $9 " - " $5}'
else
    echo "Backup failed"
    exit 1
fi
