#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋势跟踪机器人配置
================
策略参数配置
"""

# 扫描币种列表
SYMBOLS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT',
    'XRP/USDT', 'ADA/USDT', 'DOGE/USDT', 'MATIC/USDT',
    'LTC/USDT', 'LINK/USDT', 'AVAX/USDT', 'UNI/USDT',
    'ATOM/USDT', 'ARB/USDT', 'OP/USDT', 'NEAR/USDT',
]

# 策略参数
STRATEGY_CONFIG = {
    # MA 参数
    "ma_short": 20,
    "ma_long": 50,
    
    # ADX 参数
    "adx_period": 14,
    "adx_threshold": 25,
    
    # 止损止盈
    "stop_loss_pct": 0.10,  # -10%
    "take_profit_pct": 0.20,  # +20%
    
    # 仓位管理
    "max_position_pct": 0.20,  # 单币最大 20%
    
    # 检查间隔（秒）
    "check_interval": 300,  # 5 分钟
}

# 运行模式
RUN_CONFIG = {
    "check_interval": 300,  # 扫描间隔
    "log_file": "logs/trend.log",
}
