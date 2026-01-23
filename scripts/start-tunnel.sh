#!/bin/bash
# Start Cloudflare Tunnel for signalwest.net

echo "Starting Cloudflare Tunnel for signalwest.net..."
echo "This will route signalwest.net and www.signalwest.net to localhost:8000"
echo ""

cloudflared tunnel --config /Users/j/.cloudflared/signalwest-config.yml run signalwest
