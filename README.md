# OKX RSI 网格交易机器人

基于 RSI 超卖均值回归的量化交易机器人，运行在 OKX 现货市场。

## ⚡ 快速开始

### 1. 安装依赖

```bash
cd okx_trading
pip install -r requirements.txt
```

### 2. 配置 API 密钥

```bash
cp config_secrets.py.example config_secrets.py
nano config_secrets.py  # 填入你的 OKX API 密钥
chmod 600 config_secrets.py
```

**API 密钥权限要求**：只读 + 交易

### 3. 启动机器人

```bash
# 后台运行
nohup python3 robots/rsi_grid/main.py > logs/robot.log 2>&1 &

# 或使用脚本
bash scripts/robotctl.sh start
```

### 4. 查看状态

```bash
# 基本状态
ps aux | grep rsi_grid

# 深度检查（持仓、日志）
python3 scripts/status.py --deep

# 查看实时日志
tail -f logs/trading.log
```

## 📁 目录结构

| 目录/文件 | 说明 |
|-----------|------|
| `core/` | 核心模块：网格、仓位、状态管理 |
| `robots/rsi_grid/` | RSI 网格策略机器人入口 |
| `scripts/` | 运维脚本：状态检查、市场扫描 |
| `strategies/` | 交易策略实现 |
| `data/` | 数据目录：状态文件、持仓记录 |
| `logs/` | 日志目录 |
| `tests/` | 单元测试 |

### 核心文件

| 文件 | 说明 |
|------|------|
| `config.py` | 策略参数配置 |
| `config_secrets.py` | API 密钥（不提交 Git） |
| `okx_client.py` | OKX API 客户端 |
| `STRATEGY.md` | 策略设计详细文档 |

## ⚙️ 配置说明

### config.py 关键参数

```python
# RSI 参数
RSI_OVERSOLD = 30       # 超卖买入 threshold
RSI_OVERBOUGHT = 70     # 超买卖出 threshold

# 资金管理
INITIAL_BUDGET = 150    # 总预算 ($)
RSI_BATCH_USD = 50      # RSI 建仓金额 ($)
GRID_BUDGET = 50        # 网格每层金额 ($)
MAX_POSITIONS = 2       # 最大持仓数

# 网格参数
GRID_RATIO = 0.02       # 网格间距 2%
GRID_LAYERS = 8         # 网格层数

# 运行参数
SCAN_INTERVAL = 900     # 扫描间隔 15 分钟
```

### 监控币种

ETH, BTC, SOL, DOGE, XRP, ADA, SUI, BNB, LINK, PEPE, AVAX, LTC, TON, ARB, OP, NEAR

## 📈 策略概览

详见 [STRATEGY.md](./STRATEGY.md)

**核心逻辑**：
- RSI < 30 时建仓买入
- 网格高卖低买循环
- 止损 -15%，止盈 +12%

## 🛠️ 运维命令

```bash
# 启动
bash scripts/robotctl.sh start

# 停止
bash scripts/robotctl.sh stop

# 重启
bash scripts/robotctl.sh restart

# 状态
bash scripts/robotctl.sh status

# 深度检查（持仓、日志、健康度）
python3 scripts/status.py --deep

# 市场扫描（查看当前 RSI）
python3 scripts/scan_market.py --top 10

# 查看日志
tail -f logs/trading.log

# 查看错误日志
grep -i error logs/trading.log | tail -20
```

## 📊 状态文件

- `data/state.json` - 机器人状态、持仓信息
- `data/orders.json` - 订单记录

## 🔒 安全注意事项

1. **API 密钥** - 只授予现货交易和读取权限，**不要**授予提现权限
2. **预算控制** - INITIAL_BUDGET 设置你承受得起的金额
3. **监控** - 定期检查机器人状态和持仓

## 📝 更新日志

- **V3.9** (2026-03-17) - Critical bugfix: PnL 计算、冻结资金、手动平仓检测
- **V3.8** (2026-03-01) - 网格方向调整、持仓同步修复
- **V3.7** (2026-02-18) - 网格核心重构，修复 609 订单问题
