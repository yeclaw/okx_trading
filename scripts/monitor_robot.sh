#!/bin/bash
# 监控 RSI 网格机器人状态，自动重启

LOG_FILE="/home/admin/.openclaw/workspace/okx_trading/data/logs/robot_monitor.log"
PID_FILE="/home/admin/.openclaw/workspace/okx_trading/data/robot.pid"
MAIN_SCRIPT="/home/admin/.openclaw/workspace/okx_trading/robots/rsi_grid/main.py"
LOG_DIR="/home/admin/.openclaw/workspace/okx_trading/data/logs"

# 检查进程是否运行
if pgrep -f "okx_trading/robots/rsi_grid/main.py" > /dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 机器人运行正常" >> $LOG_FILE
    exit 0
fi

# 机器人已停止，尝试重启
echo "$(date '+%Y-%m-%d %H:%M:%S') - 机器人已停止，尝试重启..." >> $LOG_FILE

# 确保日志目录存在
mkdir -p $LOG_DIR

# 启动机器人
cd /home/admin/.openclaw/workspace
nohup python3 $MAIN_SCRIPT > $LOG_DIR/robot_$(date +%Y%m%d_%H%M%S).log 2>&1 &

# 等待 3 秒检查是否启动成功
sleep 3
if pgrep -f "okx_trading/robots/rsi_grid/main.py" > /dev/null; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 重启成功" >> $LOG_FILE
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 重启失败" >> $LOG_FILE
fi
