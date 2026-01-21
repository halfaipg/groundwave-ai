#!/bin/bash
# groundwave-ai - Complete Restart Script
# Restarts web server, bot, and Cloudflare tunnel (if configured)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Restarting groundwave-ai..."
echo ""

# Kill existing processes
echo "[1/3] Stopping old processes..."
pkill -f cloudflared 2>/dev/null || true
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
sleep 2

# Start web server and bot
echo "[2/3] Starting web server and bot..."
cd "$SCRIPT_DIR"
source venv/bin/activate
nohup python run.py > /tmp/groundwave.log 2>&1 &

# Wait up to 40 seconds for web server (Meshtastic takes ~30s to connect)
echo "      Waiting for web server (may take up to 40s for Meshtastic)..."
for i in {1..40}; do
    if curl -s http://localhost:8000/api/status > /dev/null 2>&1; then
        echo "      Web server running (took ${i}s)"
        break
    fi
    if [ $i -eq 40 ]; then
        echo "      Web server taking longer than expected, continuing anyway..."
        echo "      Check logs: tail -100 /tmp/groundwave.log"
    fi
    sleep 1
done

# Start Cloudflare tunnel (if script exists)
if [ -f "$SCRIPT_DIR/start-tunnel.sh" ]; then
    echo "[3/3] Starting Cloudflare tunnel..."
    ./start-tunnel.sh > /tmp/tunnel.log 2>&1 &
    sleep 3

    if ps aux | grep cloudflared | grep -v grep > /dev/null; then
        echo "      Cloudflare tunnel running"
    else
        echo "      Cloudflare tunnel failed to start"
        echo "      Check logs: tail -100 /tmp/tunnel.log"
    fi
else
    echo "[3/3] No Cloudflare tunnel configured (start-tunnel.sh not found)"
fi

echo ""
echo "groundwave-ai restart complete"
echo ""
echo "   Local:  http://localhost:8000"
echo ""
echo "Monitor logs:"
echo "   tail -f /tmp/groundwave.log"
echo ""
