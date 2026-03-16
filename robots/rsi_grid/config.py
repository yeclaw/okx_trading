#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI + 网格机器人配置
================
策略参数配置（与 STRATEGY.md 一致）
"""

# 导入敏感配置
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config_secrets import OKX_CONFIG, EMAIL_CONFIG
except ImportError:
    OKX_CONFIG = {"api_key": "", "api_secret": "", "passphrase": "", "sandbox": False, "proxies": None}
    EMAIL_CONFIG = {"enabled": False}

# 扫描币种
SYMBOLS = [
    'ETH/USDT', 'BTC/USDT', 'SOL/USDT', 'DOGE/USDT', 'XRP/USDT',
    'ADA/USDT', 'SUI/USDT', 'BNB/USDT', 'LINK/USDT', 'PEPE/USDT',
    'AVAX/USDT', 'LTC/USDT', 'TON/USDT', 'ARB/USDT', 'NEAR/USDT',
    # OP/USDT 2026-02-19 暴跌22%从策略候选中移除
]

# 资金配置（网格预算$50，每层$6.25，8层）
TRADING_CONFIG = {
    'initial_capital': 150,
    'first_batch': 43.75,    # RSI 建仓
    'second_batch': 0,       # 禁用第二批
    'grid_budget': 50,       # 网格跟进预算（8层 × $6.25）
    'max_positions': 2,
}

# 运行参数
RUN_CONFIG = {
    'check_interval': 60,      # 持仓检查间隔（秒）
    'scan_interval': 900,       # 机会扫描间隔（秒）
    'grid_layers': 8,          # 网格层数
    'grid_spread': 0.08,      # 网格间距（±8%）
}

# 报警配置
ALERT_CONFIG = {
    'enabled': EMAIL_CONFIG.get('enabled', False),
    'smtp_host': EMAIL_CONFIG.get('smtp_server'),
    'smtp_port': EMAIL_CONFIG.get('smtp_port', 587),
    'smtp_user': EMAIL_CONFIG.get('sender_email'),
    'smtp_password': EMAIL_CONFIG.get('sender_password'),
    'email_from': EMAIL_CONFIG.get('sender_email'),
    'email_to': [EMAIL_CONFIG.get('receiver_email')],
}
