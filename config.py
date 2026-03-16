"""OKX 交易配置"""
from config_secrets import OKX_CONFIG, EMAIL_CONFIG

# RSI 网格参数
RSI_OVERSOLD = 30       # RSI 超卖 threshold
RSI_OVERBOUGHT = 70     # RSI 超买 threshold
RSI_PERIOD = 14         # RSI 周期

# 仓位管理
INITIAL_BUDGET = 150    # 初始 USDT 预算 ($)
RSI_BATCH_USD = 50      # RSI 建仓每批金额 ($)
GRID_BUDGET = 50        # 网格每批金额 ($)
MAX_POSITIONS = 2       # 最大持仓数

# 网格参数
GRID_RATIO = 0.02       # 网格间距 2%

# 扫描周期 (秒)
SCAN_INTERVAL = 15 * 60  # 15分钟

# 监控币种
COINS = [
    'ETH', 'BTC', 'SOL', 'DOGE', 'XRP', 'ADA', 'SUI', 'BNB',
    'LINK', 'PEPE', 'AVAX', 'LTC', 'TON', 'ARB', 'OP', 'NEAR'
]

# 资金配置
TRADING_CONFIG = {
    'initial_capital': 150,
    'first_batch': 43.75,
    'second_batch': 0,
    'grid_budget': 50,
    'max_positions': 2,
}

# 运行参数
RUN_CONFIG = {
    'check_interval': 60,
    'scan_interval': 900,
    'grid_layers': 8,
    'grid_spread': 0.08,
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
