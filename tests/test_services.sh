#!/bin/bash
# Service Health Check Script for Signal West Mesh Platform

echo "=================================="
echo "Signal West - Service Health Check"
echo "=================================="
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Check if Python app is running
echo "1. Checking if meshbot app is running..."
if lsof -i :8000 > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Meshbot app is running on port 8000${NC}"
    PID=$(lsof -ti :8000)
    echo "  Process ID: $PID"
else
    echo -e "${RED}✗ Meshbot app is NOT running on port 8000${NC}"
fi
echo ""

# Test 2: Check if Cloudflare tunnel is connected
echo "2. Checking Cloudflare tunnel..."
if pgrep -f "cloudflared.*signalwest" > /dev/null; then
    echo -e "${GREEN}✓ Cloudflare tunnel is running${NC}"
    TUNNEL_PID=$(pgrep -f "cloudflared.*signalwest")
    echo "  Process ID: $TUNNEL_PID"
    echo "  Tunnel ID: e69524e7-466f-4214-8c0e-ac8a07d7331f"
else
    echo -e "${RED}✗ Cloudflare tunnel is NOT running${NC}"
fi
echo ""

# Test 3: Check local web interface
echo "3. Testing local web interface..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000)
if [ "$HTTP_CODE" == "200" ]; then
    echo -e "${GREEN}✓ Web interface responding (HTTP $HTTP_CODE)${NC}"
else
    echo -e "${RED}✗ Web interface not responding (HTTP $HTTP_CODE)${NC}"
fi
echo ""

# Test 4: Check database
echo "4. Checking database..."
if [ -f "data/meshbot.db" ]; then
    DB_SIZE=$(du -h data/meshbot.db | cut -f1)
    echo -e "${GREEN}✓ Database exists (Size: $DB_SIZE)${NC}"
    
    # Count messages in database
    if command -v sqlite3 &> /dev/null; then
        MSG_COUNT=$(sqlite3 data/meshbot.db "SELECT COUNT(*) FROM messages;" 2>/dev/null || echo "N/A")
        NODE_COUNT=$(sqlite3 data/meshbot.db "SELECT COUNT(*) FROM nodes;" 2>/dev/null || echo "N/A")
        BBS_COUNT=$(sqlite3 data/meshbot.db "SELECT COUNT(*) FROM bbs_posts;" 2>/dev/null || echo "N/A")
        echo "  Messages: $MSG_COUNT"
        echo "  Nodes: $NODE_COUNT"
        echo "  BBS Posts: $BBS_COUNT"
    fi
else
    echo -e "${RED}✗ Database not found${NC}"
fi
echo ""

# Test 5: Check Meshtastic device connection
echo "5. Checking Meshtastic device..."
SERIAL_PORT=$(grep "serial_port:" config.yaml | awk '{print $2}')
if [ -e "$SERIAL_PORT" ]; then
    echo -e "${GREEN}✓ Serial device exists at $SERIAL_PORT${NC}"
else
    echo -e "${YELLOW}⚠ Serial device not found at $SERIAL_PORT${NC}"
    echo "  (This is OK if using TCP/BLE connection)"
fi
echo ""

# Test 6: Check configuration
echo "6. Checking configuration..."
if [ -f "config.yaml" ]; then
    echo -e "${GREEN}✓ Config file exists${NC}"
    BOT_NAME=$(grep "bot_name:" config.yaml | awk '{print $2}')
    BOT_SHORT=$(grep "bot_short_name:" config.yaml | awk '{print $2}')
    echo "  Bot Name: $BOT_NAME"
    echo "  Bot Short Name: $BOT_SHORT"
else
    echo -e "${RED}✗ Config file not found${NC}"
fi
echo ""

# Test 7: Test API endpoints
echo "7. Testing API endpoints..."
API_STATUS=$(curl -s http://localhost:8000/api/status 2>&1)
if echo "$API_STATUS" | grep -q "node_count"; then
    echo -e "${GREEN}✓ API /api/status responding${NC}"
    echo "  Response: $(echo $API_STATUS | head -c 80)..."
else
    echo -e "${RED}✗ API not responding correctly${NC}"
fi
echo ""

# Test 8: Check external domain
echo "8. Testing external domain (signalwest.net)..."
DOMAIN_STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://signalwest.net 2>&1)
if [ "$DOMAIN_STATUS" == "200" ]; then
    echo -e "${GREEN}✓ signalwest.net is accessible (HTTP $DOMAIN_STATUS)${NC}"
elif [ "$DOMAIN_STATUS" == "000" ]; then
    echo -e "${YELLOW}⚠ Cannot reach signalwest.net (DNS may still be propagating)${NC}"
else
    echo -e "${YELLOW}⚠ signalwest.net returned HTTP $DOMAIN_STATUS${NC}"
fi
echo ""

# Test 9: Check Python dependencies
echo "9. Checking Python environment..."
if python3 -c "import meshtastic" 2>/dev/null; then
    echo -e "${GREEN}✓ Python meshtastic library installed${NC}"
else
    echo -e "${RED}✗ Python meshtastic library missing${NC}"
fi

if python3 -c "import fastapi" 2>/dev/null; then
    echo -e "${GREEN}✓ Python fastapi library installed${NC}"
else
    echo -e "${RED}✗ Python fastapi library missing${NC}"
fi
echo ""

# Summary
echo "=================================="
echo "Health Check Complete!"
echo "=================================="
echo ""
echo "Next steps:"
echo "  • Access locally: http://localhost:8000"
echo "  • Access remotely: https://signalwest.net"
echo "  • View logs: tail -f /var/log/meshbot.log (if configured)"
echo ""
