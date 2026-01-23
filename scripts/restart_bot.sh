#!/bin/bash
cd /Users/j/meshbot
pkill -f "python.*run.py" 2>/dev/null
sleep 3
source venv/bin/activate
nohup python run.py > /tmp/meshbot.log 2>&1 &
echo "$(date): Bot restarted" >> /tmp/meshbot_restarts.log
