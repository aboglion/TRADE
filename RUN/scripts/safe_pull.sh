#!/usr/bin/env bash
# ==============================================================================
# Safe Git Pull with Automatic Stash, Backup & Merge-Conflict Protection
# Ensures the trading bot updates cleanly and never crashes on startup due
# to local modified files or untracked changes.
# ==============================================================================

set -eo pipefail

# 1. Resolve project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$PROJECT_DIR" ]; then
    PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi
cd "$PROJECT_DIR" || exit 1

# 2. Determine target branch (fallback to 'main')
BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")}"
if [ "$BRANCH" = "HEAD" ] || [ -z "$BRANCH" ]; then
    BRANCH="main"
fi

echo "=================================================================="
echo "🔄 Safe Git Pull initiated (Branch: $BRANCH)"
echo "📍 Repository: $PROJECT_DIR"
echo "=================================================================="

STASH_CREATED=0
BACKUP_DIR=""

# 3. Check for local modifications (tracked or untracked)
PORCELAIN_STATUS="$(git status --porcelain 2>/dev/null || true)"

if [ -n "$PORCELAIN_STATUS" ]; then
    echo "💾 Local modifications detected. Preserving local changes..."
    
    # Create persistent physical file backup of all changed files
    TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
    BACKUP_DIR="${PROJECT_DIR}/backups/local_changes/${TIMESTAMP}"
    mkdir -p "$BACKUP_DIR"
    
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        # Extract filename (after status code)
        FILE="${line:3}"
        # Handle renamed files 'A -> B'
        if [[ "$FILE" == *" -> "* ]]; then
            FILE="${FILE##* -> }"
        fi
        # Remove quotes if git formatted filenames with quotes
        FILE="${FILE%\"}"
        FILE="${FILE#\"}"
        
        if [ -f "$FILE" ]; then
            DEST_DIR="$BACKUP_DIR/$(dirname "$FILE")"
            mkdir -p "$DEST_DIR"
            cp -p "$FILE" "$DEST_DIR/" 2>/dev/null || true
        fi
    done <<< "$PORCELAIN_STATUS"
    
    echo "📁 Physical backup saved to: $BACKUP_DIR"

    # Git stash push saving all changes (including untracked files)
    STASH_MSG="Auto-saved local changes before git pull $(date '+%Y-%m-%d %H:%M:%S')"
    echo "📦 Stashing local changes into Git stash..."
    if git stash push --include-untracked -m "$STASH_MSG" >/dev/null 2>&1; then
        echo "✅ Local changes stashed successfully (saved in 'git stash list')."
        STASH_CREATED=1
    else
        echo "⚠️ Git stash reported no changes or returned non-zero. Continuing with pull..."
    fi
else
    echo "✨ Working tree is clean. No local modifications to stash."
fi

# 4. Pull latest code from GitHub
echo "⬇️ Pulling latest changes from GitHub (origin/$BRANCH)..."
PULL_SUCCESS=0

if git pull origin "$BRANCH"; then
    PULL_SUCCESS=1
elif git pull; then
    PULL_SUCCESS=1
else
    echo "⚠️ Direct pull encountered an error. Attempting git fetch + fast-forward merge..."
    if git fetch origin "$BRANCH" && git merge --ff-only "origin/$BRANCH"; then
        PULL_SUCCESS=1
    fi
fi

if [ "$PULL_SUCCESS" -ne 1 ]; then
    echo "❌ Git pull failed to retrieve latest changes."
    # If we stashed, restore local changes before exiting
    if [ "$STASH_CREATED" -eq 1 ]; then
        echo "🔄 Restoring stashed local changes..."
        git stash pop >/dev/null 2>&1 || true
    fi
    exit 1
fi

echo "✅ Latest changes pulled successfully."

# 5. Restore stashed local changes if a stash was created
if [ "$STASH_CREATED" -eq 1 ]; then
    echo "🔄 Re-applying saved local changes..."
    # Attempt to pop stash cleanly
    if git stash pop; then
        echo "✅ Local modifications restored cleanly on top of new remote code."
    else
        echo "⚠️ Conflict detected when reapplying local modifications!"
        echo "🧹 Resetting working tree to remote HEAD so application can boot cleanly."
        git reset --hard HEAD >/dev/null 2>&1 || true
        echo "📦 Your local changes remain 100% safe:"
        echo "   1. Preserved in Git Stash: check with 'git stash list'"
        if [ -n "$BACKUP_DIR" ]; then
            echo "   2. Preserved in file backup: $BACKUP_DIR"
        fi
    fi
fi

echo "=================================================================="
echo "🎉 Safe Git Pull completed successfully."
echo "=================================================================="
exit 0
