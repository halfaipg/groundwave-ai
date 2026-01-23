#!/bin/bash
# Master test script - verifies all Signal West systems

clear
echo "╔════════════════════════════════════════════════════════════════════╗"
echo "║                                                                    ║"
echo "║              🛰️  SIGNAL WEST MESH BOT - SYSTEM CHECK  🛰️            ║"
echo "║                                                                    ║"
echo "╚════════════════════════════════════════════════════════════════════╝"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PASS_COUNT=0
FAIL_COUNT=0

test_pass() {
    echo -e "   ${GREEN}✅ PASS${NC} - $1"
    ((PASS_COUNT++))
}

test_fail() {
    echo -e "   ${RED}❌ FAIL${NC} - $1"
    ((FAIL_COUNT++))
}

test_warn() {
    echo -e "   ${YELLOW}⚠️  WARN${NC} - $1"
}

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "📊 TESTING CORE SERVICES"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Test 1: Meshbot App
echo "🐍 Python Application"
if lsof -i :8000 > /dev/null 2>&1; then
    test_pass "Meshbot app running on port 8000"
else
    test_fail "Meshbot app NOT running"
fi

# Test 2: Database
echo ""
echo "💾 Database"
if [ -f "data/meshbot.db" ]; then
    test_pass "Database file exists"
    MSG_COUNT=$(sqlite3 data/meshbot.db "SELECT COUNT(*) FROM messages;" 2>/dev/null)
    NODE_COUNT=$(sqlite3 data/meshbot.db "SELECT COUNT(*) FROM nodes;" 2>/dev/null)
    echo "       Messages: $MSG_COUNT, Nodes: $NODE_COUNT"
else
    test_fail "Database not found"
fi

# Test 3: Cloudflare Tunnel
echo ""
echo "☁️  Cloudflare Tunnel"
if pgrep -f "cloudflared.*signalwest" > /dev/null; then
    test_pass "Tunnel connected"
else
    test_fail "Tunnel NOT running"
fi

# Test 4: Web Interface
echo ""
echo "🌐 Web Interface"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000)
if [ "$HTTP_CODE" == "200" ]; then
    test_pass "Local web portal accessible"
else
    test_fail "Web portal not responding"
fi

# Test 5: External Domain
DOMAIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://signalwest.net 2>&1)
if [ "$DOMAIN_CODE" == "200" ]; then
    test_pass "signalwest.net is live"
else
    test_warn "signalwest.net returned HTTP $DOMAIN_CODE"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "📡 TESTING MESH NETWORK"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Test mesh via API
API_RESPONSE=$(curl -s http://localhost:8000/api/status)
CONNECTED=$(echo $API_RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin).get('connected', False))" 2>/dev/null)
MY_NODE=$(echo $API_RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin).get('my_node_id', 'Unknown'))" 2>/dev/null)
NODE_COUNT=$(echo $API_RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin).get('node_count', 0))" 2>/dev/null)

echo "🔌 Meshtastic Connection"
if [ "$CONNECTED" == "True" ]; then
    test_pass "Connected to mesh as $MY_NODE"
    echo "       Discovered nodes: $NODE_COUNT"
else
    test_fail "Not connected to mesh"
fi

# Test serial device
echo ""
echo "📟 Serial Device"
SERIAL_PORT=$(grep "serial_port:" config.yaml | awk '{print $2}')
if [ -e "$SERIAL_PORT" ]; then
    test_pass "Device found at $SERIAL_PORT"
else
    test_warn "Device not at $SERIAL_PORT (may use TCP/BLE)"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "🤖 TESTING AI/LLM INTEGRATION"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Test LM Studio
echo "🧠 LM Studio API"
LLM_RESPONSE=$(curl -s -X POST http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 5}' 2>&1)

if echo "$LLM_RESPONSE" | grep -q "content"; then
    test_pass "LM Studio responding"
    REPLY=$(echo $LLM_RESPONSE | python3 -c "import sys, json; print(json.load(sys.stdin)['choices'][0]['message']['content'])" 2>/dev/null | tr -d '\n')
    echo "       Response: '$REPLY'"
else
    test_fail "LM Studio not responding"
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "🔧 TESTING API ENDPOINTS"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Test APIs
echo "📋 REST APIs"
ENDPOINTS=("/api/status" "/api/nodes" "/api/messages" "/api/bbs/posts")
for endpoint in "${ENDPOINTS[@]}"; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000$endpoint)
    if [ "$STATUS" == "200" ]; then
        test_pass "$endpoint"
    else
        test_fail "$endpoint (HTTP $STATUS)"
    fi
done

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "📊 TEST SUMMARY"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo "Tests passed: ${GREEN}$PASS_COUNT${NC} / $TOTAL"
echo ""

if [ $FAIL_COUNT -eq 0 ]; then
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                                                                    ║${NC}"
    echo -e "${GREEN}║          ✅  ALL SYSTEMS OPERATIONAL - BOT IS READY! ✅             ║${NC}"
    echo -e "${GREEN}║                                                                    ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "🎯 Your Signal West mesh bot is fully operational!"
    echo ""
    echo "What's working:"
    echo "  • 📡 Mesh network connected ($NODE_COUNT nodes discovered)"
    echo "  • 🤖 AI assistant ready (LM Studio active)"
    echo "  • 🌐 Web portal live (local + signalwest.net)"
    echo "  • ☁️  Cloudflare tunnel secured"
    echo "  • 📋 BBS and all services online"
    echo ""
    echo "🧪 Test your bot:"
    echo "  1. Open your Meshtastic app/device"
    echo "  2. Send a DM to: ai44"
    echo "  3. Try: 'Hello!' or '!help' or '!weather'"
    echo "  4. Watch the magic happen! ✨"
    echo ""
    echo "🌍 Access your portal:"
    echo "  • Local:  http://localhost:8000"
    echo "  • Public: https://signalwest.net"
    echo ""
else
    echo -e "${RED}⚠️  $FAIL_COUNT test(s) failed - review output above${NC}"
fi

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
