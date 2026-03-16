#!/bin/bash
# RSI 机器人启动脚本（带自动重启 + 日志）

LOG_FILE="/root/clawd/okx_trading/logs/trading.log"
PID_FILE="/root/clawd/okx_trading/data/robot.pid"
MAX_RESTARTS=10          # 最多重启次数
RESTART_WINDOW=3600      # 重启时间窗口（秒）
RESTART_DELAY=5          # 重启延迟（秒）

cd /root/clawd/okx_trading

log_msg() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

get_pids() {
    # 获取所有 rsi_grid 进程PID
    pgrep -f "rsi_grid/main.py" 2>/dev/null
}

start_robot() {
    # 检查是否已在运行
    local pids=$(get_pids)
    if [ -n "$pids" ]; then
        log_msg "机器人已在运行 (PIDs: $pids)"
        return 0
    fi
    
    log_msg "启动 RSI 网格机器人..."
    
    # 使用 nohup 启动，忽略挂起信号
    nohup python3 robots/rsi_grid/main.py >> "$LOG_FILE" 2>&1 &
    local pid=$!
    
    echo $pid > "$PID_FILE"
    log_msg "机器人已启动 (PID: $pid)"
    return 0
}

stop_robot() {
    local pids=$(get_pids)
    if [ -z "$pids" ]; then
        log_msg "机器人未运行"
        rm -f "$PID_FILE" 2>/dev/null
        return 0
    fi
    
    log_msg "停止机器人 (PIDs: $pids)..."
    
    # 发送 SIGTERM 让程序优雅退出
    for pid in $pids; do
        kill -TERM $pid 2>/dev/null
    done
    
    # 等待最多 30 秒
    local count=0
    while [ $count -lt 30 ]; do
        local remaining=$(get_pids)
        if [ -z "$remaining" ]; then
            break
        fi
        sleep 1
        ((count++))
    done
    
    # 强制杀死
    local remaining=$(get_pids)
    if [ -n "$remaining" ]; then
        log_msg "强制杀死进程 (PIDs: $remaining)"
        for pid in $remaining; do
            kill -9 $pid 2>/dev/null
        done
    fi
    
    rm -f "$PID_FILE" 2>/dev/null
    log_msg "机器人已停止"
}

monitor_loop() {
    local restart_count=0
    local first_start=$(date +%s)
    
    log_msg "========== 守护进程启动 =========="
    
    while true; do
        local pids=$(get_pids)
        
        if [ -z "$pids" ]; then
            log_msg "检测到机器人已停止"
            
            # 检查重启次数
            local now=$(date +%s)
            local window_passed=$((now - first_start))
            
            if [ $window_passed -ge $RESTART_WINDOW ]; then
                log_msg "重置重启计数（时间窗口已过）"
                restart_count=0
                first_start=$now
            fi
            
            if [ $restart_count -ge $MAX_RESTARTS ]; then
                log_msg "错误: 超过最大重启次数 ($MAX_RESTARTS)，停止自动重启"
                log_msg "请手动检查问题后重启: $0 start"
                exit 1
            fi
            
            ((restart_count++))
            log_msg "尝试自动重启 ($restart_count/$MAX_RESTARTS)..."
            
            sleep $RESTART_DELAY
            start_robot
            
            # 等待几秒让进程启动
            sleep 3
        else
            # 进程运行中，重置连续失败计数（如果需要）
            # 这里只是简单记录
            :
        fi
        
        sleep 10  # 每10秒检查一次
    done
}

case "$1" in
    start)
        start_robot
        ;;
    stop)
        stop_robot
        ;;
    restart)
        stop_robot
        sleep 2
        start_robot
        ;;
    status)
        pids=$(get_pids)
        if [ -n "$pids" ]; then
            echo "机器人运行中 (PIDs: $pids)"
        else
            echo "机器人未运行"
        fi
        ;;
    monitor)
        # 后台运行守护进程
        cd /root/clawd/okx_trading
        (
            log_msg "========== 守护进程启动 =========="
            restart_count=0
            first_start=$(date +%s)
            
            while true; do
                pids=$(get_pids)
                
                if [ -z "$pids" ]; then
                    log_msg "检测到机器人已停止"
                    
                    now=$(date +%s)
                    window_passed=$((now - first_start))
                    
                    if [ $window_passed -ge $RESTART_WINDOW ]; then
                        log_msg "重置重启计数（时间窗口已过）"
                        restart_count=0
                        first_start=$now
                    fi
                    
                    if [ $restart_count -ge $MAX_RESTARTS ]; then
                        log_msg "错误: 超过最大重启次数 ($MAX_RESTARTS)，停止自动重启"
                        log_msg "请手动检查问题后重启: $0 start"
                        exit 1
                    fi
                    
                    ((restart_count++))
                    log_msg "尝试自动重启 ($restart_count/$MAX_RESTARTS)..."
                    
                    sleep $RESTART_DELAY
                    start_robot
                    sleep 3
                fi
                
                sleep 10
            done
        ) >> "$LOG_FILE" 2>&1 &
        echo "守护进程已启动 (PID: $!)"
        ;;
    *)
        echo "用法: $0 {start|stop|status|restart|monitor}"
        exit 1
        ;;
esac
