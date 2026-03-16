#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主程序 (最终实盘修正版 - V3.0)
架构升级:
1. 虚拟网格 -> 实盘限价挂单网格
2. 添加 sync_orders, check_order_status, update_grid_after_fill
3. 平仓时强制撤销所有网格挂单
"""

import os
import sys
import json
import time
import logging
import signal
import traceback
from datetime import datetime
from typing import Dict, List
import pandas as pd

# ==================== 信号处理 ====================
def signal_handler(signum, frame):
    """捕获退出信号，记录详细日志"""
    sig_name = signal.Signals(signum).name
    logger = logging.getLogger(__name__)
    logger.warning(f"收到退出信号 {sig_name}，正在优雅关闭...")
    logger.warning(f"堆栈跟踪:\n{''.join(traceback.format_stack(frame))}")
    
    # 设置全局停止标志
    global GRACEFUL_STOP
    GRACEFUL_STOP = True

# 注册信号处理器
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGHUP, signal_handler)
signal.signal(signal.SIGQUIT, signal_handler)

GRACEFUL_STOP = False

# 添加父目录到路径
BASE_DIR = '/home/admin/.openclaw/workspace/okx_trading'
sys.path.insert(0, BASE_DIR)

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,  # 改为 DEBUG 以支持细粒度控制
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/home/admin/.openclaw/workspace/okx_trading/logs/trading.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 为反查操作创建单独的 logger（默认级别为 DEBUG，减少重复日志）
order_check_logger = logging.getLogger('order_check')
order_check_logger.setLevel(logging.DEBUG)

# 使用统一的 OKX 客户端
from okx_client import get_client, OKXConfig
from core.position import PositionManager
from core.grid import GridManager
from strategies.rsi_contrarian import RSIContrarianStrategy
# [修复] 使用本地 config.py 的 SYMBOLS，而不是全局 config 的 RSI_SYMBOLS
from robots.rsi_grid.config import SYMBOLS as RSI_SYMBOLS
from config import OKX_CONFIG, EMAIL_CONFIG, TRADING_CONFIG, RUN_CONFIG, ALERT_CONFIG

# 新增：数据记录器（只写日志，与计算解耦）
from data_logger import DataLogger

# 导入统一状态管理器
from core.state_manager import StateManager

# 导入报警管理器
from core.alert_manager import AlertManager


# ==================== 熔断机制 ====================
class CircuitBreaker:
    """API 熔断器 - 防止 API 连续失败被封"""
    
    def __init__(self, failure_threshold=5, timeout=300):
        self.failure_threshold = failure_threshold  # 连续失败次数阈值
        self.timeout = timeout                      # 熔断时长（秒）
        self.failure_count = 0                     # 连续失败计数
        self.last_failure_time = None               # 最后失败时间
        self.state = 'CLOSED'                      # CLOSED | OPEN | HALF_OPEN
    
    def record_success(self):
        """记录成功，重置计数器"""
        self.failure_count = 0
        self.state = 'CLOSED'
    
    def record_failure(self):
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = 'OPEN'
            logger.warning(f"[熔断] API 连续失败 {self.failure_count} 次，触发熔断")
    
    def can_proceed(self) -> bool:
        """检查是否可以继续调用"""
        if self.state == 'CLOSED':
            return True
        
        if self.state == 'OPEN':
            if time.time() - self.last_failure_time >= self.timeout:
                self.state = 'HALF_OPEN'
                logger.info(f"[熔断] 进入半开状态，尝试恢复")
                return True
            return False
        
        return True  # HALF_OPEN 状态允许尝试
    
    def get_status(self) -> Dict:
        """获取熔断状态"""
        return {
            'state': self.state,
            'failure_count': self.failure_count,
            'last_failure': self.last_failure_time
        }


# 邮件通知
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


class EmailNotifier:
    """邮件通知器 [修复] 改为异步发送，避免阻塞主循环"""

    def __init__(self):
        self.enabled = EMAIL_CONFIG.get('enabled', True)
        self.smtp_server = EMAIL_CONFIG.get('smtp_server', 'smtp.gmail.com')
        self.smtp_port = EMAIL_CONFIG.get('smtp_port', 587)
        self.sender = EMAIL_CONFIG.get('sender_email', '')
        self.password = EMAIL_CONFIG.get('sender_password', '')
        self.receiver = EMAIL_CONFIG.get('receiver_email', '')

    def send(self, subject, body, critical=False):
        """异步发送邮件，不阻塞主程序
        Args:
            critical: 如果为 True，同步发送（用于崩溃报警，确保发出）
        """
        if not self.enabled:
            return
        if critical:
            # 严重错误同步发送，确保发出
            self._send_thread(subject, body)
        else:
            import threading
            threading.Thread(target=self._send_thread, args=(subject, body), daemon=True).start()

    def _send_thread(self, subject, body):
        """实际发送邮件的线程函数"""
        try:
            msg = MIMEMultipart()
            msg['Subject'] = f"[QuantBot] {subject}"
            msg['From'] = self.sender
            msg['To'] = self.receiver
            msg.attach(MIMEText(str(body), 'plain', 'utf-8'))
            # 设置10秒超时，防止线程挂死
            server = smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=10)
            server.starttls()
            server.login(self.sender, self.password)
            server.sendmail(self.sender, self.receiver, msg.as_string())
            server.quit()
            logger.info(f"邮件已发送: {subject}")
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")


class Exchange:
    """交易所封装 (修复精度问题)"""

    def __init__(self):
        self.okx = get_client()
        self.proxies = {}
        self.markets = {}  # 存储市场信息
        self._load_markets_data()  # 初始化时加载

    def _load_markets_data(self):
        """加载市场精度数据 (阻塞式重试)"""
        retry_count = 0
        max_retries = 10  # 最大重试次数
        while retry_count < max_retries:
            try:
                self.markets = self.load_markets()
                if self.markets:
                    logger.info(f"市场精度数据已加载: {len(self.markets)} 个交易对")
                    return
            except Exception as e:
                logger.error(f"加载市场数据失败: {e}")
                retry_count += 1
                logger.warning(f"第 {retry_count}/{max_retries} 次重试加载市场数据...")
                time.sleep(5)
        
        # 超过最大重试次数
        logger.error(f"达到最大重试次数 {max_retries}，未能加载市场数据")
        raise RuntimeError(f"无法加载市场数据，已重试 {max_retries} 次")

    def truncate(self, value: float, precision: int) -> float:
        """不四舍五入，直接截断 (修复科学计数法自动进位问题)"""
        try:
            from decimal import Decimal, ROUND_DOWN
            # 将 float 转为 string 再转 Decimal，避免 float 精度丢失
            d = Decimal(str(value))
            # 构造量化精度，例如 precision=4 -> '0.0001'
            quantizer = Decimal("1e-{}".format(precision)) if precision > 0 else Decimal("1")
            # 强制向下取整
            truncated = d.quantize(quantizer, rounding=ROUND_DOWN)
            return float(truncated)
        except Exception as e:
            logger.error(f"精度截断错误: {e}, value={value}, prec={precision}")
            return float(value)

    def get_precision(self, symbol: str):
        """获取精度配置"""
        if symbol not in self.markets:
            return 1, 6  # 默认 amount=1位(尽量保守), price=6位
        m = self.markets[symbol]['precision']
        return m.get('amount', 1), m.get('price', 6)

    def fetch_ticker(self, symbol: str) -> Dict:
        return self.okx.fetch_ticker(symbol)

    def fetch_open_orders(self, symbol: str) -> List[Dict]:
        """获取当前挂单"""
        return self.okx.fetch_open_orders(symbol)

    def fetch_balance(self) -> Dict:
        return self.okx.fetch_balance()

    def fetch_positions(self) -> List:
        return self.okx.fetch_positions()

    def create_market_buy_order(self, symbol: str, usdt_amount: float, clOrdId: str = None) -> Dict:
        # 市价买单按 USDT 金额下单，保留两位小数
        safe_amount = self.truncate(usdt_amount, 2)
        return self.okx.create_order(symbol, 'buy', safe_amount, 'market', clOrdId=clOrdId)

    def fetch_order_by_client_id(self, symbol: str, client_oid: str) -> Dict:
        """通过自定义 clOrdId 查询订单状态（用于补救幽灵单）- [修复] 添加 status 转换"""
        try:
            instId = symbol.replace('/', '-')
            endpoint = f"/api/v5/trade/order?instId={instId}&clOrdId={client_oid}"
            result = self.okx._request('GET', endpoint)
            
            # 减少反查日志：只在首次查询时记录 DEBUG 级别，避免重复刷屏
            logger.debug(f"[反查] {symbol} {client_oid} => {result.get('code', 'N/A')}")
            
            # 同样需要转换 state -> status
            if result.get('code') == '0' and result.get('data'):
                order_data = result['data'][0] if result['data'] else {}
                
                state = order_data.get('state', '')
                status_map = {
                    '0': 'pending',
                    '1': 'open',
                    '2': 'filled',
                    '3': 'open',
                    '4': 'canceled',
                    '6': 'rejected',
                }
                order_data['status'] = status_map.get(state, 'unknown')
                return order_data
            
            return result
        except Exception as e:
            logger.error(f"核对订单失败: {e}")
            return {}

    def create_market_sell_order(self, symbol: str, coin_amount: float) -> Dict:
        # 市价卖单按币的数量，必须处理精度
        prec_amt, _ = self.get_precision(symbol)
        safe_amount = self.truncate(coin_amount, prec_amt)
        # 防止数量为0
        if safe_amount <= 0:
            logger.warning(f"下单数量过小: {coin_amount} -> {safe_amount}")
            return {}
        return self.okx.create_order(symbol, 'sell', safe_amount, 'market')

    def create_limit_order(self, symbol: str, side: str, price: float, coin_amount: float, client_oid: str = None) -> Dict:
        """[修复] 添加 client_oid 参数防止重复下单
        使用确定性 ID: grid_{symbol}_{price}_{timestamp_hour}
        """
        prec_amt, prec_price = self.get_precision(symbol)
        safe_amount = self.truncate(coin_amount, prec_amt)
        safe_price = self.truncate(price, prec_price)
        return self.okx.create_order(symbol, side, safe_amount, order_type='limit', price=safe_price, clOrdId=client_oid)

    def fetch_order(self, order_id: str, symbol: str) -> Dict:
        """获取订单状态 - [修复] 将 OKX 的 state 转换为 ccxt 风格的 status"""
        result = self.okx.get_order(symbol, order_id)
        
        # OKX 返回格式: {'code': '0', 'data': [{'state': '2', 'accFillSz': '0.1', ...}]}
        if result.get('code') == '0' and result.get('data'):
            order_data = result['data'][0] if result['data'] else {}
            
            # 将 OKX state 转换为 ccxt 风格的 status
            state = order_data.get('state', '')
            status_map = {
                '0': 'pending',      # 待确认
                '1': 'open',         # 挂单中
                '2': 'filled',       # 完全成交
                '3': 'open',         # 部分成交（仍算 open）
                '4': 'canceled',     # 已取消
                '6': 'rejected',     # 已拒绝
            }
            order_data['status'] = status_map.get(state, 'unknown')
            
            # 确保返回的 dict 包含必要的字段
            return order_data
        
        return result

    def fetch_order_history(self, symbol: str, limit: int = 50) -> Dict:
        """获取订单历史 - 用于网格恢复时查询已成交/已撤销订单
        
        OKX API: GET /api/v5/trade/history-orders
        """
        try:
            result = self.okx.fetch_order_history(symbol, limit)
            return result
        except AttributeError:
            logger.warning("okx 客户端不支持 fetch_order_history")
            return {'code': '1', 'data': [], 'msg': 'Method not supported'}

    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        return self.okx.cancel_order(symbol, order_id)

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> List:
        return self.okx.fetch_ohlcv(symbol, timeframe, limit)

    def load_markets(self) -> Dict:
        instruments = self.okx.get_instruments('SPOT')
        markets = {}
        for inst in instruments:
            try:
                symbol = inst.get('instId', '').replace('-', '/')
                if not symbol:
                    continue
                min_sz = float(inst.get('minSz', 0) or 0)
                tick_sz = inst.get('tickSz', '1')
                lot_sz = inst.get('lotSz', inst.get('minSz', '1'))
                markets[symbol] = {
                    'precision': {
                        'amount': self._count_decimals(lot_sz),
                        'price': self._count_decimals(tick_sz)
                    },
                    'limits': {
                        'amount': {'min': min_sz},
                        'cost': {'min': min_sz * float(inst.get('ctVal', 1) or 1)}
                    }
                }
            except (KeyError, ValueError, TypeError) as e:
                logger.warning(f"解析市场数据失败: {inst.get('instId', 'unknown')} - {e}")
                continue
        return markets

    def _count_decimals(self, number) -> int:
        # 直接处理字符串，避免浮点数精度丢失
        s = str(number)
        if '.' not in s:
            return 0
        decimals = len(s.split('.')[1])
        return decimals


class QuantTradingSystem:
    """量化交易系统"""

    def __init__(self, capital=150):
        # 初始资金设定
        self.initial_capital = TRADING_CONFIG['initial_capital']
        self.capital = capital

        # 资金配置（从配置文件读取）
        self.FIRST_BATCH = TRADING_CONFIG['first_batch']
        self.SECOND_BATCH = TRADING_CONFIG['second_batch']
        self.GRID_BUDGET = TRADING_CONFIG['grid_budget']
        self.MAX_CONCURRENT_POSITIONS = TRADING_CONFIG['max_positions']

        # 目录配置
        self.data_dir = '/home/admin/.openclaw/workspace/okx_trading/data'
        self.logs_dir = f'{self.data_dir}/logs'
        os.makedirs(self.logs_dir, exist_ok=True)

        # 熔断器
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            timeout=300  # 5 分钟
        )

        # 报警管理器
        self.alert = AlertManager(ALERT_CONFIG)

        self.exchange = Exchange()

        # [P1] 初始化统一状态管理器
        self.state_mgr = StateManager(data_dir=self.data_dir)

        # 初始化管理器（使用 StateManager）
        self.position_mgr = PositionManager(
            data_dir=self.data_dir,
            state_mgr=self.state_mgr
        )
        self.grid_manager = GridManager(
            budget=TRADING_CONFIG['grid_budget'],
            data_dir=self.data_dir,
            state_mgr=self.state_mgr
        )
        self.grid_manager.init_exchange(self.exchange)

        # RSI 策略币种（从配置文件读取）
        self.symbols_rsi = RSI_SYMBOLS

        # [已简化] 移除 ML 相关模块依赖
        self.email = EmailNotifier()
        
        # [新增] 数据记录器（只写日志，与计算逻辑解耦）
        self.logger = DataLogger(log_dir='data/logs')

        self.strategies = {'rsi_contrarian': RSIContrarianStrategy()}

        # 运行参数（从配置文件读取）
        self.check_interval = RUN_CONFIG['check_interval']
        self.scan_interval = RUN_CONFIG['scan_interval']
        self.last_scan_time = None
        self.is_running = False

        # 资金配置（从配置文件读取）
        self.FIRST_BATCH = TRADING_CONFIG['first_batch']
        self.SECOND_BATCH = TRADING_CONFIG['second_batch']
        self.MAX_CONCURRENT_POSITIONS = TRADING_CONFIG['max_positions']

        # [Limit Fix] 启动时同步资金
        self._sync_capital()

    def _sync_capital(self):
        """[关键修复] 同步资金：直接读取交易所可用余额
        网格挂单会冻结资金，必须以交易所返回的 free 为准
        增加重试机制：API不可达时指数退避重试3-5次
        """
        # 连续失败计数（类变量）
        if not hasattr(self.__class__, '_capital_sync_failures'):
            self.__class__._capital_sync_failures = 0
        
        max_retries = 5
        base_delay = 1  # 初始延迟 1 秒
        
        for attempt in range(max_retries):
            try:
                balance = self.exchange.fetch_balance()
                if balance and 'data' in balance and len(balance['data']) > 0:
                    # OKX V5 接口返回结构: {'code': '0', 'data': [{'details': [...]}]}
                    data = balance['data']
                    usdt_found = False
                    if isinstance(data, list) and len(data) > 0:
                        details = data[0].get('details', [])
                        for item in details:
                            if item.get('ccy') == 'USDT':
                                self.capital = float(item.get('availBal', 0) or 0)
                                usdt_found = True
                                # 重试成功时重置计数
                                if self.__class__._capital_sync_failures > 0:
                                    logger.info(f"资金同步恢复成功 (连续失败 {self.__class__._capital_sync_failures} 次后)")
                                    self.__class__._capital_sync_failures = 0
                                logger.info(f"资金同步完成: 交易所可用余额 ${self.capital:.2f}")
                                return
                    
                    # 备选：直接查找 USDT 余额
                    if not usdt_found:
                        for item in data:
                            if item.get('ccy') == 'USDT':
                                self.capital = float(item.get('availBal', 0) or 0)
                                usdt_found = True
                                if self.__class__._capital_sync_failures > 0:
                                    logger.info(f"资金同步恢复成功 (连续失败 {self.__class__._capital_sync_failures} 次后)")
                                    self.__class__._capital_sync_failures = 0
                                logger.info(f"资金同步完成: 交易所可用余额 ${self.capital:.2f}")
                                return
                    
                    # 如果找不到 USDT 余额，强制设为 0
                    if not usdt_found:
                        self.capital = 0
                        logger.warning("未找到 USDT 余额，强制设为 0（防止死循环下单）")
                else:
                    # 无法获取余额时重试
                    raise Exception("余额响应格式异常")
                    
            except Exception as e:
                # 计算指数退避延迟
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # 1s, 2s, 4s, 8s...
                    logger.warning(f"资金同步第 {attempt + 1} 次失败: {e}, {delay:.1f}s 后重试...")
                    time.sleep(delay)
                else:
                    # 所有重试都失败
                    self.__class__._capital_sync_failures += 1
                    fail_count = self.__class__._capital_sync_failures
                    self.capital = 0
                    logger.error(f"资金同步失败: 已重试 {max_retries} 次仍未成功 (连续失败 {fail_count} 次), 强制设为 0")
                    
                    # 连续失败超过 3 次发送告警
                    if fail_count >= 3:
                        alert_msg = f"API连接异常: 资金同步连续失败 {fail_count} 次，请检查网络/OKX API状态"
                        logger.warning(f"[告警] {alert_msg}")
                        if hasattr(self, 'alert') and self.alert:
                            self.alert.warning("资金同步异常", alert_msg)
                        if hasattr(self, 'email') and self.email:
                            self.email.send("资金同步异常", alert_msg, critical=True)

    def scan_opportunities(self) -> List[Dict]:
        """扫描机会 - [增强] 进度日志 + 超时保护"""
        import time as time_module
        
        # [关键修复] 每次扫描前同步资金
        self._sync_capital()

        opportunities = []
        total = len(self.symbols_rsi)
        start_time = time_module.time()
        logger.info(f"开始扫描 {total} 个RSI核心币种...")

        for i, symbol in enumerate(self.symbols_rsi):
            symbol_start = time_module.time()
            
            # [优化] 进度日志改为 DEBUG 级别，减少重复刷屏
            logger.debug(f"[Scan] {symbol} ({i+1}/{total})...")
            
            # 超时检查 - 单个币种超过 30 秒报警
            symbol_elapsed = time_module.time() - symbol_start
            if symbol_elapsed > 30:
                logger.warning(f"[Scan] {symbol} 耗时 {symbol_elapsed:.1f}s")

            pos = self.position_mgr.get_position(symbol)
            has_open = pos is not None and pos.status == 'open'

            # [修复] 实时计算当前持仓数量，防止并发漏洞
            current_positions = len([p for p in self.position_mgr.positions.values() if p.status == 'open'])

            if has_open and len(pos.batches) >= 2:
                # 记录已持仓币种
                self.logger.log_opportunity(symbol, {'action': 'hold', 'has_position': True})
                continue
            if not has_open and current_positions >= self.MAX_CONCURRENT_POSITIONS:
                logger.info(f"已达持仓上限 {self.MAX_CONCURRENT_POSITIONS}，跳过 {symbol}")
                # 记录扫描过的币种（无买入机会）
                self.logger.log_opportunity(symbol, {'action': 'skip_full', 'has_position': False})
                continue

            try:
                # [已简化] 移除 DataCollector，直接从交易所获取数据
                # [修正] 增加 limit 到 100 以确保 RSI 计算准确
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
                if not ohlcv or len(ohlcv) < 14:
                    # 记录数据获取失败的币种
                    self.logger.log_opportunity(symbol, {'action': 'data_error', 'has_position': False})
                    continue

                # [修复] API 返回最新数据在前，需要反转
                df = pd.DataFrame(ohlcv[::-1], columns=['ts', 'o', 'h', 'l', 'c', 'v'])
                df['close'] = df['c'].astype(float)

                # 计算简单 RSI
                delta = df['close'].diff()
                gain = delta.where(delta > 0, 0)
                loss = -delta.where(delta < 0, 0)
                avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
                avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
                rsi = (100 - (100 / (1 + avg_gain / avg_loss))).iloc[-1]

                # [优化] RSI 计算日志改为 DEBUG 级别，最终汇总时统一输出 INFO
                api_elapsed = (time_module.time() - symbol_start) * 1000
                logger.debug(f"[Scan] {symbol} RSI={rsi:.1f} ({api_elapsed:.0f}ms)")

                # 简单买入信号：RSI < 30
                if rsi >= 30:
                    # 记录 RSI 未达标的币种
                    self.logger.log_opportunity(symbol, {'action': 'rsi_high', 'has_position': False, 'rsi': rsi})
                    continue

                ml_pred = self._get_ml_prediction({})
                rsi_signal = self.strategies['rsi_contrarian'].generate_signal(df, ml_pred)

                if hasattr(rsi_signal, 'action') and rsi_signal.action == 'buy':
                    # --- [FIX START] 修复 features 未定义导致的崩溃 ---
                    # 使用 df 的最后一行数据
                    latest_row = df.iloc[-1]
                    latest_close = float(latest_row['close'])

                    # 时间戳从 df 获取 (单位已经是秒)
                    # 注意: ohlcv 是 [ts, o, h, l, c, v]，ts 是第0列
                    candle_ts = int(latest_row['ts'] * 1000)  # 转换为毫秒用于生成 ID

                    if has_open and getattr(rsi_signal, 'batch', 0) < 2:
                        logger.info(f"跳过 {symbol} 补仓: 信号强度 Batch{rsi_signal.batch} 不满足第二批要求")
                        continue

                    opportunities.append({
                        'symbol': symbol,
                        'strategy': 'rsi_contrarian',
                        'action': rsi_signal.action,
                        'batch': getattr(rsi_signal, 'batch', 1),
                        'confidence': getattr(rsi_signal, 'confidence', 0),
                        'price': latest_close,
                        'is_add_batch': has_open,
                        'rsi': getattr(rsi_signal, 'rsi', rsi),  # 从 signal 获取准确值
                        'bb_position': getattr(rsi_signal, 'bb_position', 0.5),  # 从 signal 获取
                        'signal_ts': candle_ts
                    })
                    # --- [FIX END] ---
            except Exception as e:
                logger.error(f"扫描 {symbol} 失败: {e}")
                self.logger.log_error('scan', str(e))

        # 按 batch（批次）降序，再按 confidence（置信度）降序
        opportunities.sort(key=lambda x: (x.get('batch', 0), x.get('confidence', 0)), reverse=True)
        
        # [新增] 扫描完成总结
        total_elapsed = time_module.time() - start_time
        logger.info(f"[Scan] 完成 {len(opportunities)} 个机会 | 总耗时: {total_elapsed:.1f}s")
        
        return opportunities

    def _get_ml_prediction(self, features: Dict) -> int:
        """ML 预测 [已禁用]"""
        return 0  # 返回中性，不参与决策

    def _features_to_df(self, features: Dict) -> pd.DataFrame:
        if not features or 'history' not in features:
            return pd.DataFrame()
        return pd.DataFrame(features['history'])

    def execute_trade(self, opportunity: Dict):
        """执行交易（建仓/补仓）- [增强版] 解决网络双重故障导致的持仓丢失风险"""
        symbol = opportunity['symbol']
        strategy_name = opportunity.get('strategy', 'rsi_contrarian')
        is_add_batch = opportunity.get('is_add_batch', False)

        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if not ticker or 'last' not in ticker:
                logger.warning(f"无法获取 {symbol} 价格，跳过交易")
                return
            current_price = ticker['last']

            # 资金限额检查 - RSI 建仓改为一次买 $50（去掉分批逻辑）
            invest_usdt = 50  # 固定 $50，不再分批
            if self.capital < invest_usdt:
                logger.warning(f"资金不足: 需要 ${invest_usdt}, 可用 ${self.capital:.2f}")
                return

            if opportunity.get('action') == 'buy':
                # [修复] 使用 K 线时间戳 + 批次号生成确定性 ID
                signal_ts = opportunity.get('signal_ts', int(time.time() * 1000))
                batch_num = opportunity.get('batch', 1)
                clean_sym = symbol.replace('/', '').replace('-', '')
                # 格式: b_ETHUSDT_最后8位时间戳_批次号 (不超过32位)
                short_ts = str(signal_ts)[-8:]
                client_oid = f"b{clean_sym[:4]}{short_ts}{batch_num}"

                logger.info(f"正在下单 {symbol} (clOrdId={client_oid})...")

                # 尝试下单
                order = self.exchange.create_market_buy_order(symbol, invest_usdt, clOrdId=client_oid)

                order_id = None
                filled_amount = 0.0

                # 1. 快乐路径：直接成功
                if order and order.get('code') == '0' and order.get('data'):
                    order_data = order['data'][0]
                    order_id = order_data.get('ordId')
                    filled_amount = invest_usdt / current_price  # 估算，稍后修正
                    logger.info(f"✅ 下单成功直接返回: {symbol} OrdId={order_id}")
                else:
                    # 2. 异常路径：进入坚固的核对循环
                    logger.warning(f"下单未直接返回成功，进入最终状态核对... {symbol} ID:{client_oid}")
                    
                    # 尝试核对 3 次，每次间隔增加
                    for i in range(3):
                        time.sleep(2 * (i + 1))
                        check_res = self.exchange.fetch_order_by_client_id(symbol, client_oid)
                        
                        if check_res and check_res.get('code') == '0' and check_res.get('data'):
                            checked_order = check_res['data'][0]
                            # 只要订单存在，无论状态如何，都必须记录
                            order_id = checked_order.get('ordId')
                            sz = float(checked_order.get('accFillSz', 0) or 0)
                            
                            # 如果完全没成交(canceled)，则不记录
                            # [修复] OKX state: '4' = canceled
                            if checked_order.get('state') == '4' and sz == 0:
                                logger.info(f"核对确认订单已取消，无需记录: {client_oid}")
                                return
                            
                            if sz > 0:
                                filled_amount = sz
                            else:
                                filled_amount = invest_usdt / current_price
                            
                            logger.info(f"✅ 第{i+1}次核对找回订单: {symbol} OrdId={order_id}")
                            break
                        elif check_res and check_res.get('code') == '51001':
                            # 51001 = 订单不存在，说明之前真的没发出去
                            logger.info(f"核对确认订单不存在(安全): {client_oid}")
                            return
                        else:
                            # 减少重试失败的日志输出频率
                            logger.debug(f"第{i+1}次核对失败，继续重试...")

                # 3. 只有确认拿到了 order_id 才记录持仓
                if order_id:
                    self.capital -= invest_usdt
                    pos = self.position_mgr.add_batch(symbol, filled_amount, current_price, invest_usdt)
                    if pos:
                        pos.note = strategy_name
                    self.position_mgr.save()

                    batch_type = "补仓(Batch 2)" if is_add_batch else "建仓(Batch 1)"
                    logger.info(f"✅ {batch_type} {symbol}: 投入 ${invest_usdt} @ ${current_price} (OrdId: {order_id})")
                    
                    # [新增] 记录买入日志
                    self.logger.log_buy(
                        symbol=symbol,
                        price=current_price,
                        amount=filled_amount,
                        batch=1 if not is_add_batch else 2,
                        entry_price=current_price,
                        confidence=opportunity.get('confidence', 0),
                        reason=batch_type
                    )
                    
                    self.email.send(f"交易执行: {symbol}", f"{batch_type} 成功 价格: {current_price} 金额: {invest_usdt}")

                    if not is_add_batch:
                        params = self.grid_manager.calculate_dynamic_params(symbol)
                        layers = params.get('grid_layers', 8)
                        entry_price = pos.avg_price if pos and pos.avg_price else current_price
                        self.grid_manager.init_grid(symbol, entry_price, layers=layers)
                        logger.info(f"网格已初始化 {symbol}: {layers}层 @ ${entry_price:.4f}")
                else:
                    # ⚠️ CRITICAL: 如果依然无法确认，我们不能假装无事发生
                    err_msg = f"CRITICAL: 订单状态未知，请人工核查 OKX! Symbol: {symbol}, ClOrdId: {client_oid}"
                    logger.error(err_msg)
                    self.email.send("紧急人工核查", err_msg)

        except Exception as e:
            logger.error(f"执行交易失败 {symbol}: {e}", exc_info=True)

    def execute_close(self, symbol: str, reason: str):
        """执行平仓 (增强版: 确保撤单完成)"""
        try:
            pos = self.position_mgr.get_position(symbol)
            if not pos or pos.status != 'open':
                return

            logger.info(f"开始平仓流程 {symbol}...")

            # 1. 撤销所有网格挂单 (不删除状态，等待平仓成功后再删除)
            self.grid_manager.cancel_all_grid_orders(symbol, delete_state=False)
            logger.info(f"已发送撤单请求 {symbol}")

            # === [修复] 循环确认挂单已撤销 ===
            max_retries = 5
            for i in range(max_retries):
                open_orders = self.exchange.fetch_open_orders(symbol)
                if not open_orders:
                    logger.info(f"确认 {symbol} 无活动挂单")
                    break
                logger.info(f"等待 {symbol} 撤单中... 剩余 {len(open_orders)} 个挂单")
                time.sleep(1)
                # 如果是最后一次尝试还不行，再次强制撤单
                if i == max_retries - 2:
                    self.grid_manager.cancel_all_grid_orders(symbol, delete_state=False)

            ticker = self.exchange.fetch_ticker(symbol)
            if not ticker or 'last' not in ticker:
                logger.warning("无法获取价格，平仓中止")
                return
            current_price = ticker['last']

            # 获取交易所真实余额
            base_currency = symbol.split('/')[0]
            balance = self.exchange.fetch_balance()
            actual_balance = 0
            frozen_balance = 0
            if balance and 'data' in balance:
                for item in balance['data'][0].get('details', []):
                    if item.get('ccy') == base_currency:
                        # [Bug2修复 2026-03-16] 只使用 availBal，不加 frozenBal
                        # 撤单是异步的，frozenBal 需要时间解冻，直接加会导致 Insufficient balance
                        actual_balance = float(item.get('availBal', 0))
                        frozen_balance = float(item.get('frozenBal', 0))
                        break

            # 取记录持仓量 和 实际可用余额 的较小值
            amount = min(pos.total_amount, actual_balance)

            if amount <= 0:
                logger.warning(f"平仓失败 {symbol}: 可用余额为 0 (记录: {pos.total_amount})")
                self.position_mgr.close_position(symbol, f"{reason} (余额不足手动平仓)")
                return

            logger.info(f"执行平仓 {symbol}: 计划 {pos.total_amount}, 实际可用 {actual_balance}, 下单 {amount}")

            # 市价全平
            order = self.exchange.create_market_sell_order(symbol, amount)
            if order and 'ordId' in order:
                usdt_back = amount * current_price
                self.capital += usdt_back

                # [Bug1修复 2026-03-16] PnL 计算需要加上已实现的盈亏
                # 网格卖出成交时更新了 grid_state 中的 realized_pnl，但没有同步到 batches
                grid_state = self.grid_manager.grid_state.get(symbol, {})
                realized_pnl = grid_state.get('realized_pnl', 0)
                
                total_cost = sum(b.cost for b in pos.batches)
                # 已实现盈亏 + 卖出剩余持仓的收益 - 总成本
                pnl = realized_pnl + usdt_back - total_cost
                pnl_pct = (pnl / total_cost) * 100 if total_cost > 0 else 0

                self.position_mgr.close_position(symbol, reason)
                
                # [修复] 平仓成功后删除网格状态
                self.grid_manager.cancel_all_grid_orders(symbol, delete_state=True)
                
                # [新增] 计算持仓时长并记录卖出日志
                holding_seconds = None
                try:
                    entry_ts = pos.entry_time.timestamp() if hasattr(pos.entry_time, 'timestamp') else pos.entry_time
                    holding_seconds = int(time.time() - entry_ts)
                except (AttributeError, TypeError, ValueError) as e:
                    logger.warning(f"计算持仓时长失败: {symbol} - {e}")
                
                self.logger.log_sell(
                    symbol=symbol,
                    price=current_price,
                    amount=amount,
                    pnl_pct=pnl_pct,
                    pnl_usd=pnl,
                    holding_seconds=holding_seconds,
                    reason=reason
                )
                
                logger.info(f"平仓 {symbol} ({reason}): PnL ${pnl:.2f} ({pnl_pct:.2f}%)")
                self.email.send(f"平仓通知: {symbol}", f"原因: {reason} 价格: {current_price} PnL: ${pnl:.2f} ({pnl_pct:.2f}%)")
            else:
                logger.warning(f"平仓失败 {symbol}，如需重试请手动处理")

        except Exception as e:
            logger.error(f"执行平仓失败 {symbol}: {e}")

    def check_positions(self):
        """检查持仓（止损止盈 + 网格）-[修复] 添加手动平仓检测"""
        positions = self.position_mgr.positions
        if not positions:
            return

        logger.info(f"检查 {len([p for p in positions.values() if p.status=='open'])} 个持仓...")

        # [新增] 获取所有余额，用于核对手动平仓
        all_balances = {}
        try:
            bal_res = self.exchange.fetch_balance()
            if bal_res and 'data' in bal_res:
                for item in bal_res['data'][0].get('details', []):
                    all_balances[item['ccy']] = float(item.get('availBal', 0)) + float(item.get('frozenBal', 0))
        except Exception as e:
            logger.warning(f"获取余额失败: {e}")

        for symbol, pos in list(positions.items()):
            if pos.status != 'open':
                continue

            # --- [增强] 手动平仓检测 ---
            # [修复 2026-03-01] 改进检测逻辑：增加时间窗口，避免误判
            # 只检测建仓超过 10 分钟的持仓，且余额价值低于持仓价值的 1%
            base_ccy = symbol.split('/')[0]
            # 复用 all_balances，避免重复 API 调用
            real_balance = all_balances.get(base_ccy, 0)
            
            # 计算持仓时长
            position_age_seconds = 0
            try:
                entry_ts = pos.created_at.timestamp() if hasattr(pos.created_at, 'timestamp') else pos.created_at
                if isinstance(pos.created_at, str):
                    entry_ts = datetime.fromisoformat(pos.created_at.replace('Z', '+00:00')).timestamp()
                position_age_seconds = time.time() - entry_ts
            except:
                pass
            
            # 只有持仓超过 10 分钟才检测手动平仓（避免网格操作时的误判）
            if position_age_seconds > 600:  # 10 分钟 = 600 秒
                # [Bug3修复 2026-03-16] 检测余额是否大幅减少（包含 100% 卖空场景）
                # 原来用 real_balance > 0 导致 100% 卖空时漏检
                balance_value = real_balance * pos.avg_price
                position_value = pos.total_amount * pos.avg_price
                if position_value > 10 and balance_value < position_value * 0.01:
                        logger.warning(f"⚠️ 检测到 {symbol} 手动平仓 (记录持仓:{pos.total_amount:.4f}, 余额:{real_balance:.4f}, 价值:${balance_value:.2f})")
                        try:
                            # 撤销所有网格订单
                            self.grid_manager.cancel_all_grid_orders(symbol)
                            
                            # 同步关闭持仓
                            self.position_mgr.close_position(symbol, "手动平仓同步")
                            self.email.send(f"{symbol} 手动平仓同步", f"检测到用户手动平仓，余额价值 ${balance_value:.2f}")
                        except Exception as e:
                            logger.error(f"[手动平仓] {symbol} 处理失败: {e}")
                            self.position_mgr.close_position(symbol, f"手动平仓同步 ({e})")
                        continue
            # --- [增强 结束] ---

            try:
                # [已简化] 移除 DataCollector/TradeRecorder，直接计算
                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']

                pnl = (current_price - pos.avg_price) / pos.avg_price * 100

                # 2. 获取并处理 K 线数据 [关键修复]
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1h', limit=200)
                if not ohlcv:
                    logger.warning(f"{symbol} K线数据获取失败，跳过策略检查")
                    continue

                # [修复] 强制指定列类型为 float，防止字符串比较BUG
                df = pd.DataFrame(ohlcv[::-1], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df = df.astype(float)

                # 3. 计算持仓期间的最高价 [关键修复：时间戳单位统一]
                # pos.created_at 是 ISO 字符串 (Local Time)
                # okx_client 返回的 timestamp 是 UTC Seconds (int)
                # 必须统一为 Seconds
                # [Python 3.6 兼容] datetime.fromisoformat 在 3.7+ 可用
                try:
                    created_str = pos.created_at.replace('Z', '+00:00') if pos.created_at else ''
                    if '+' in created_str:
                        # 手动解析 ISO 格式: 2026-02-28T01:19:24.681+00:00
                        dt_part, tz_part = created_str.rsplit('+', 1)
                        dt_part = dt_part.replace('T', ' ')
                        entry_dt = datetime.strptime(dt_part, '%Y-%m-%d %H:%M:%S.%f')
                    elif 'T' in created_str:
                        # 无时区 ISO 格式: 2026-02-28T01:05:30.683083
                        dt_part = created_str.replace('T', ' ')
                        entry_dt = datetime.strptime(dt_part, '%Y-%m-%d %H:%M:%S.%f')
                    else:
                        entry_dt = datetime.strptime(created_str, '%Y-%m-%d %H:%M:%S')
                    entry_ts = entry_dt.timestamp()  # 转换为秒 (float)
                except Exception as e:
                    logger.error(f"解析建仓时间失败: {e}")
                    entry_ts = time.time()

                max_history = 0
                if not df.empty:
                    # [修复] 比较单位现在都是 Seconds
                    # tolerance 1小时 (3600秒)
                    tolerance = 3600
                    since_entry_df = df[df['timestamp'] >= (entry_ts - tolerance)]
                    if not since_entry_df.empty:
                        max_history = since_entry_df['high'].max()
                    else:
                        # 如果没有找到对应的K线，说明数据可能太旧或时间戳偏差大
                        # 这种情况下使用 current_price 作为兜底，防止 highest_price 归零
                        max_history = current_price

                # 更新最高价逻辑
                prev_highest = getattr(pos, 'highest_price', 0) or 0
                
                # [安全防御] 如果计算出的历史最高价是 0，或者是 NaN，则忽略
                if pd.isna(max_history) or max_history <= 0:
                    max_history = current_price
                
                # 最终最高价取值
                highest_price = max(prev_highest, max_history, current_price, pos.avg_price)
                pos.highest_price = highest_price

                # 4. 策略判断
                strategy = self.strategies['rsi_contrarian']
                
                # 计算 RSI (增加数据长度检查)
                if len(df) > 14:
                    rsi = strategy.calculate_rsi(df).iloc[-1]
                else:
                    rsi = 50  # 默认中性
                
                pnl = (current_price - pos.avg_price) / pos.avg_price * 100
                logger.info(f"[持仓监控] {symbol}: 现价={current_price:.4f}, 均价={pos.avg_price:.4f}, PnL={pnl:.2f}%, 最高={highest_price:.4f}, RSI={rsi:.1f}")

                # [修复] 网格/RSSI策略：只保留止损，禁用止盈
                # 止盈逻辑只适合趋势策略，不适合网格/抄底策略
                # 网格应该永远运行，直到价格跌到止损线
                grid_state = self.grid_manager.grid_state.get(symbol, {})
                sl = grid_state.get('sl')
                
                # 检查是否有持仓（无论网格是否存在）
                has_position = pos.total_amount > 0 and pos.status == 'open'
                
                # 有持仓时只检查止损，禁用止盈
                if has_position:
                    if sl and current_price <= sl:
                        logger.info(f"触发止损 {symbol}: 现价 {current_price:.4f} <= 止损价 {sl:.4f}")
                        self.execute_close(symbol, "止损触发")
                        continue
                    # 跳过所有止盈检查（包括 strategy.should_close()）
                else:
                    # 无持仓时正常检查止盈止损
                    tp = grid_state.get('tp')
                    if tp and current_price >= tp:
                        logger.info(f"触发止盈 {symbol}: 现价 {current_price:.4f} >= 止盈价 {tp:.4f}")
                        self.execute_close(symbol, "止盈触发")
                        continue
                    if sl and current_price <= sl:
                        logger.info(f"触发止损 {symbol}: 现价 {current_price:.4f} <= 止损价 {sl:.4f}")
                        self.execute_close(symbol, "止损触发")
                        continue

                    should_close, reason = strategy.should_close(
                        df, pos.avg_price, pos.created_at, datetime.now(), highest_price
                    )

                    if should_close:
                        logger.info(f"触发平仓条件 {symbol}: {reason}")
                        self.execute_close(symbol, reason)
                        continue

                # [自动补建网格]
                if symbol not in self.grid_manager.grid_state:
                    # [V3.9 新增] 先验证交易所实际持仓，避免假持仓导致的问题
                    try:
                        resp = self.exchange._request('GET', '/api/v5/account/balance')
                        bal_data = resp.get('data', [{}])[0]
                        details = bal_data.get('details', [])
                        real_amount = 0
                        for d in details:
                            if d.get('ccy') == symbol.replace('/USDT', ''):
                                real_amount = float(d.get('eq', 0) or d.get('cashBal', 0) or 0)
                                break
                        
                        if real_amount <= 0:
                            logger.info(f"[跳过] {symbol} 交易所无持仓，不重建网格")
                            continue
                    except Exception as e:
                        logger.warning(f"验证持仓失败 {symbol}: {e}")
                    
                    # 计算持仓成本 (Batch 是 dataclass，用属性访问)
                    position_cost = sum(b.cost for b in pos.batches) if pos.batches else 0
                    # [修复] 如果 batches 为空但有持仓，用持仓市值作为成本
                    if position_cost == 0 and pos.total_amount > 0 and pos.avg_price > 0:
                        position_cost = pos.total_amount * pos.avg_price
                    # 可用资金 + 持仓成本 = 总可用资金
                    total_available = self.capital + position_cost
                    # [修复] 已有持仓时优先初始化网格（不严格检查资金）
                    if total_available >= self.GRID_BUDGET or (pos.total_amount > 0 and pos.avg_price > 0):
                        try:
                            safe_grid_budget = self.GRID_BUDGET * 0.98
                            self.grid_manager.budget = safe_grid_budget
                            self.grid_manager.init_grid(symbol, pos.avg_price, layers=8)
                            logger.info(f"✅ 自动重新初始化网格 {symbol} @ ${pos.avg_price:.4f} (持仓成本: ${position_cost:.2f})")
                        except Exception as e:
                            logger.warning(f"初始化网格失败 {symbol}: {e}")

                # ----------------------------------------------------
                # [实盘网格逻辑修正]
                # ----------------------------------------------------
                # 网格交易 - 始终开启，不判断震荡
                # 1. 同步挂单 + 崩溃恢复（返回成交订单）
                filled_orders = self.grid_manager.sync_orders(symbol) or []
                # 2. 检查常规订单状态
                check_result = self.grid_manager.check_order_status(symbol) or []
                if check_result:
                    filled_orders.extend(check_result)

                # [新增] 每3次检查强制对账一次（防止网络问题导致的状态丢失）
                check_count = getattr(self, '_position_check_count', {})
                count = check_count.get(symbol, 0) + 1
                check_count[symbol] = count
                self._position_check_count = check_count
                
                if count % 2 == 0:
                    logger.info(f"[周期对账] 第{count}次检查，强制同步 {symbol} 状态")
                    # [修复] 接收返回值，查漏补缺
                    late_fills = self.grid_manager.force_reconcile(symbol) or []
                    if late_fills:
                        filled_orders.extend(late_fills)
                    
                    # [P0修复] 定时从交易所同步持仓数据
                    try:
                        balance = self.exchange.fetch_balance()
                        if not balance:
                            raise Exception("交易所返回空余额")
                        
                        data = balance.get('data')
                        if not data or not isinstance(data, list) or len(data) == 0:
                            raise Exception("余额数据格式异常")
                        
                        details = data[0].get('details', [])
                        ccy = symbol.split('/')[0]
                        for d in details:
                            if d.get('ccy') == ccy:
                                eq_str = d.get('eq', '0')
                                avg_px_str = d.get('accAvgPx', '0')
                                if not eq_str or not avg_px_str:
                                    continue
                                total = float(eq_str)
                                avg_px = float(avg_px_str)
                                
                                # [修复] pos 可能为 None 的问题
                                if pos is not None and total > 0 and avg_px > 0:
                                    pos.total_amount = total
                                    pos.avg_price = avg_px
                                    logger.info(f"[交易所同步] {symbol} 持仓: {total} @ {avg_px}")
                                    self.position_mgr.save()
                                break
                    except Exception as e:
                        logger.warning(f"[交易所同步] {symbol} 失败: {e}")

                # 3. 补单 (Fill Counter Orders)
                if filled_orders:
                    self.grid_manager.update_grid_after_fill(symbol, filled_orders)
                    
                    # [P0修复] 确保 PositionManager 中的持仓存在
                    if pos is None:
                        pos = self.position_mgr.get_position(symbol)
                    if pos is None:
                        # 从交易所同步真实持仓
                        logger.warning(f"[持仓同步] {symbol} 持仓不存在，从交易所同步")
                        # 获取持仓均价
                        grid_state = self.grid_manager.grid_state.get(symbol, {})
                        entry_price = grid_state.get('entry_price', 0)
                        position_size = grid_state.get('position_size', 0)
                        if position_size > 0 and entry_price > 0:
                            cost = position_size * entry_price
                            pos = self.position_mgr.add_batch(symbol, position_size, entry_price, cost)
                        else:
                            logger.error(f"[持仓同步] {symbol} 无法同步持仓 (size={position_size}, price={entry_price})")
                            continue
                    
                    # [关键修复] 更新持仓数量 (考虑 0.1% 手续费)
                    fee_rate = 0.001  # 预估 0.1% 手续费
                    for order in filled_orders:
                        filled_amount = order.get('amount', 0)
                        
                        # 如果没有返回成交数量，估算
                        if filled_amount == 0:
                            state = self.grid_manager.grid_state.get(symbol, {})
                            invest = state.get('amount_per_trade', 0)
                            filled_amount = invest / order['price']
                        
                        if order['side'] == 'buy':
                            # 买入：实际到账 = 成交数量 × (1 - 手续费)
                            actual_amount = filled_amount * (1 - fee_rate)
                            pos.total_amount += actual_amount
                            # 同时更新均价
                            if pos.avg_price > 0:
                                pos.avg_price = (pos.avg_price * pos.total_amount + order['price'] * actual_amount) / (pos.total_amount + actual_amount)
                            else:
                                pos.avg_price = order['price']
                            logger.info(f"网格买入更新持仓: +{actual_amount:.4f} {symbol.split('/')[0]} (扣{fee_rate*100}%手续费)")
                        elif order['side'] == 'sell':
                            # 卖出前检查持仓，防止超卖导致负数
                            actual_sell = min(filled_amount, pos.total_amount)
                            if actual_sell > 0:
                                pos.total_amount -= actual_sell
                                logger.info(f"网格卖出更新持仓: -{actual_sell:.4f} {symbol.split('/')[0]}")
                            else:
                                logger.warning(f"网格卖出跳过: 持仓不足 (持仓: {pos.total_amount:.4f}, 卖出: {filled_amount:.4f})")
                    
                    # [P0修复] 只有在持仓为空时才用 grid_state 兜底
                    grid_state = self.grid_manager.grid_state.get(symbol, {})
                    if grid_state and (not pos or pos.total_amount <= 0):
                        grid_size = grid_state.get('position_size', 0)
                        grid_price = grid_state.get('entry_price', 0)
                        if grid_size > 0 and grid_price > 0:
                            pos.total_amount = grid_size
                            pos.avg_price = grid_price
                            logger.info(f"[持仓同步] {symbol} 从网格状态同步(兜底): {grid_size} @ {grid_price}")
                    
                    # 保存最新的持仓数量
                    self.position_mgr.save()
                    # 注意：不再重复调用 sync_orders，因为 sync_orders 内部已经处理了补单逻辑

            except Exception as e:
                logger.error(f"检查持仓 {symbol} 失败: {e}", exc_info=True)

    def run(self):
        """运行主循环"""
        self.is_running = True
        
        # 启动通知
        if hasattr(self, 'alert') and ALERT_CONFIG.get('enabled'):
            self.alert.info("机器人启动", f"初始资金: ${self.capital:.2f}")
        
        logger.info("=" * 60)
        logger.info("量化交易系统启动 (实盘 V2)")
        logger.info(f"初始总资金: ${self.initial_capital}")
        logger.info(f"当前可用资金: ${self.capital:.2f} (已扣除持仓)")
        logger.info(f"网格/批次配置: 网格预算${self.GRID_BUDGET}, 单批${self.FIRST_BATCH}")
        logger.info("=" * 60)

        # ----------------------------------------------
        # [修复完备版] 启动时的崩溃恢复检查 (Crash Recovery)
        # ----------------------------------------------
        logger.info("正在执行启动自检与恢复...")
        
        # [新增] 检查持仓是否在 SYMBOLS 中，不在则自动平仓
        symbols_set = set(self.symbols_rsi)
        for symbol, pos in list(self.position_mgr.positions.items()):
            if pos.status == 'open' and symbol not in symbols_set:
                logger.warning(f"[启动检查] {symbol} 不在 SYMBOLS 列表中，自动平仓...")
                try:
                    # 先撤销网格订单
                    if symbol in self.grid_manager.grid_state:
                        self.grid_manager.cancel_all_grid_orders(symbol)
                    # 获取实际余额进行平仓
                    ticker = self.exchange.fetch_ticker(symbol)
                    if ticker and 'last' in ticker:
                        current_price = ticker['last']
                        base_currency = symbol.split('/')[0]
                        balance = self.exchange.fetch_balance()
                        actual_balance = 0
                        if balance and 'data' in balance:
                            for item in balance['data'][0].get('details', []):
                                if item.get('ccy') == base_currency:
                                    actual_balance = float(item.get('availBal', 0))
                                    break
                        
                        if actual_balance > 0:
                            order = self.exchange.create_market_sell_order(symbol, actual_balance)
                            if order and 'ordId' in order:
                                self.capital += actual_balance * current_price
                                logger.info(f"[启动检查] {symbol} 平仓完成，回笼 ${actual_balance * current_price:.2f}")
                        
                        self.position_mgr.close_position(symbol, "不在 SYMBOLS 列表中自动平仓")
                        self.email.send(f"{symbol} 自动平仓", f"该币种不在 SYMBOLS 列表中，已自动平仓")
                except Exception as e:
                    logger.error(f"[启动检查] {symbol} 自动平仓失败: {e}")
                    self.position_mgr.close_position(symbol, f"不在 SYMBOLS 列表中 ({e})")
                continue
        
        for symbol, pos in self.position_mgr.positions.items():
            if pos.status == 'open':
                # [P0修复] 验证交易所实际持仓，避免清仓后重建虚假网格
                try:
                    resp = self.exchange._request('GET', '/api/v5/account/balance')
                    bal_data = resp.get('data', [{}])[0]
                    details = bal_data.get('details', [])
                    real_amount = 0
                    for d in details:
                        if d.get('ccy') == symbol.replace('/USDT', ''):
                            real_amount = float(d.get('eq', 0) or d.get('cashBal', 0) or 0)
                            break
                    
                    if real_amount <= 0:
                        logger.warning(f"[启动恢复] {symbol} 交易所无持仓 ({real_amount})，关闭虚假持仓记录")
                        self.position_mgr.close_position(symbol, "交易所无实际持仓")
                        continue
                except Exception as e:
                    logger.warning(f"[启动恢复] {symbol} 验证持仓失败: {e}，继续恢复流程")
                
                if symbol in self.grid_manager.grid_state:
                    # 1. 对账：清理幽灵单 + 捕获离线成交
                    offline_fills = self.grid_manager.reconcile_orders(symbol)
                    # 2. 如果有离线成交，补反向单
                    if offline_fills:
                        logger.info(f"[启动恢复] {symbol} 发现 {len(offline_fills)} 笔离线成交，触发网格流转")
                        # A. 先让网格流转 (更新 done/side/id)
                        self.grid_manager.update_grid_after_fill(symbol, offline_fills)
                        # B. 【关键修复】同步更新 PositionManager 的持仓数量
                        fee_rate = 0.001
                        for order in offline_fills:
                            filled_amt = order.get('amount', 0)
                            if order['side'] == 'buy':
                                pos.total_amount += filled_amt * (1 - fee_rate)
                                logger.info(f"[启动恢复] {symbol} 补录买入: +{filled_amt:.4f} (扣{fee_rate*100}%手续费)")
                            elif order['side'] == 'sell':
                                # 卖出前检查持仓，防止超卖导致负数
                                actual_sell = min(filled_amt, pos.total_amount)
                                if actual_sell > 0:
                                    pos.total_amount -= actual_sell
                                    logger.info(f"[启动恢复] {symbol} 补录卖出: -{actual_sell:.4f}")
                                else:
                                    logger.warning(f"[启动恢复] {symbol} 卖出跳过: 持仓不足 (持仓: {pos.total_amount:.4f}, 卖出: {filled_amt:.4f})")
                        self.position_mgr.save()
                    # 3. 同步挂单：补挂所有缺失的单（包括反向单）
                    self.grid_manager.sync_orders(symbol)
        logger.info("自检完成，进入主循环")

        # [已禁用] self.ml.load_models()

        while self.is_running:
            try:
                current_time = datetime.now()

                self.check_positions()

                if not self.last_scan_time or \
                   (current_time - self.last_scan_time).total_seconds() > self.scan_interval:
                    opportunities = self.scan_opportunities()
                    # [修复] 执行前检查持仓数量，防止同时买入多个
                    current_positions = len([p for p in self.position_mgr.positions.values() if p.status == 'open'])
                    executed = 0
                    for opp in opportunities:
                        if current_positions >= self.MAX_CONCURRENT_POSITIONS:
                            break  # 已达持仓上限，停止执行
                        invest_amt = self.SECOND_BATCH if opp.get('is_add_batch') else self.FIRST_BATCH
                        if self.capital >= invest_amt:
                            self.execute_trade(opp)
                            current_positions += 1  # 更新持仓计数
                            executed += 1
                    if executed > 0:
                        logger.info(f"执行了 {executed} 个交易信号")
                    self.last_scan_time = current_time

                time.sleep(self.check_interval)

            except KeyboardInterrupt:
                logger.info("收到停止信号 (KeyboardInterrupt)")
                break
            except Exception as e:
                logger.error(f"主循环错误: {e}", exc_info=True)
                # 使用 AlertManager 发送报警
                if hasattr(self, 'alert'):
                    self.alert.critical("系统异常", f"主循环错误: {e}\n{traceback.format_exc()}", force=True)
                time.sleep(60)
            
            # 检查优雅停止标志
            if GRACEFUL_STOP:
                logger.info("收到退出信号，优雅关闭中...")
                # 退出时保存状态
                self.position_mgr.save()
                self.grid_manager.save_state()
                break

        logger.info("量化交易系统停止")

    def stop(self):
        self.is_running = False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--capital', type=float, default=150)
    args = parser.parse_args()

    system = QuantTradingSystem(capital=args.capital)
    system.run()


if __name__ == '__main__':
    main()
