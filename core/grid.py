"""
网格交易管理系统
基于 PositionManager 的网格功能
修复版：解决浮点数精度、状态同步、交易所精度问题
实盘挂单版 (Real Limit Orders)
[P1] 升级：使用 StateManager 统一存储
"""

import hashlib
import logging
import math
import time
import os
import json
from typing import Dict, List, Tuple, Set
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
import pandas as pd
import numpy as np

# 导入 StateManager 和 position_adapter
from core.state_manager import StateManager
from core.position_adapter import save_grid_state


def round_price(price: float, precision: int = 8) -> str:
    """
    安全的价格四舍五入，使用Decimal避免浮点误差
    """
    d = Decimal(str(price))
    quantized = d.quantize(Decimal('1.' + '0' * precision), rounding=ROUND_HALF_UP)
    return str(quantized)


def round_amount(amount: float, precision: int) -> float:
    """
    数量必须向下取整（ROUND_DOWN），防止金额超出余额
    """
    d = Decimal(str(amount))
    quantized = d.quantize(Decimal('1.' + '0' * precision), rounding=ROUND_DOWN)
    return float(quantized)


def parse_price(price_str: str) -> float:
    """将价格字符串转回浮点数"""
    if not price_str or price_str == '0':
        return 0.0
    try:
        return float(price_str)
    except (ValueError, TypeError):
        return 0.0


def deterministic_hash(s: str, mod: int = 10000) -> int:
    """
    确定性哈希函数，不受 PYTHONHASHSEED 影响
    用于生成稳定的 client_oid，确保重启后能正确查找订单
    """
    return int(hashlib.md5(s.encode('utf-8')).hexdigest(), 16) % mod


class GridManager:
    """
    标准双向网格策略 - 实盘挂单版 (Real Limit Orders)
    """

    # [新增] 价格偏离阈值配置
    PRICE_DEVIATION_THRESHOLD = 0.05  # 5% 偏离阈值
    PRICE_DEVIATION_CHECK_ENABLED = True  # 是否启用价格偏离检查

    def __init__(self, budget: float = 50, data_dir: str = '/root/clawd/okx_trading/data',
                 state_mgr: StateManager = None):
        self.budget = budget
        self.grid_layers = 5
        self.grid_spread = 0.08
        self.logger = logging.getLogger(__name__)

        self.data_dir = data_dir
        self.state_mgr = state_mgr
        
        self.grid_state = {}
        self.exchange = None
        self.markets = {}

        self.load_state()

    def init_exchange(self, exchange):
        """初始化交易所API并加载市场精度"""
        self.exchange = exchange
        
        # 重要：不要修改 exchange.markets，保持原始结构供 Exchange.get_precision 使用
        # 只使用 getattr 读取，不做修改
        if hasattr(exchange, 'markets'):
            self.markets = exchange.markets
        else:
            self.markets = {}
        
        # 如果需要额外字段，在调用时才从 markets 中提取

    def save_state(self):
        """保存网格状态（优先使用 StateManager）"""
        # 优先保存到 StateManager
        if self.state_mgr:
            save_grid_state(self.state_mgr, self.grid_state)
            return
        
        # 回退到文件存储
        self._save_to_file()
    
    def _save_to_file(self):
        """保存到文件（兼容旧代码）"""
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            save_data = {}
            for symbol, state in self.grid_state.items():
                state_copy = state.copy()
                if 'prices_set' in state_copy:
                    del state_copy['prices_set']
                for k, v in state_copy.items():
                    if v is None:
                        state_copy[k] = ''
                save_data[symbol] = state_copy

            temp_file = os.path.join(self.data_dir, 'grid_state.json') + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(save_data, f, indent=2, default=str)
            os.replace(temp_file, os.path.join(self.data_dir, 'grid_state.json'))
            self.logger.info(f"✅ 网格状态已保存: {len(save_data)} 个币种")
        except Exception as e:
            self.logger.error(f"保存网格状态失败: {e}")

    def load_state(self):
        """加载网格状态（优先使用 StateManager）"""
        # 优先从 StateManager 加载
        if self.state_mgr:
            data = self.state_mgr.get_grid()
            self._parse_grid_data(data)
            return
        
        # 回退到文件加载
        self._load_from_file()
    
    def _load_from_file(self):
        """从文件加载（兼容旧代码）"""
        state_file = os.path.join(self.data_dir, 'grid_state.json')
        if not os.path.exists(state_file):
            return

        try:
            with open(state_file, 'r') as f:
                data = json.load(f)
            self._parse_grid_data(data)
        except Exception as e:
            self.logger.error(f"加载网格状态失败: {e}")
    
    def _parse_grid_data(self, data: Dict):
        """解析加载的数据"""
        for symbol, state in data.items():
            if 'prices' in state:
                state['prices_set'] = set(state['prices'])
            for k, v in state.items():
                if v == '':
                    state[k] = None
            
            # [修复] 从 buy_orders/sell_orders 重建 pending 结构
            # 这样可以处理：手动添加持仓、重启后丢失 pending 等情况
            self._rebuild_pending_from_orders(symbol, state)
        
        self.grid_state = data
        self.logger.info(f"✅ 已恢复 {len(self.grid_state)} 个网格策略状态")
    
    def _rebuild_pending_from_orders(self, symbol: str, state: Dict):
        """从 buy_orders/sell_orders 重建 pending 结构
        用于处理：手动添加持仓、重启后 pending 丢失等情况
        [修复] 保留原始方向 original_side，用于恢复
        [修复] 保留原始 prices 顺序，不改变索引
        
        [方案D修复] 重启时清除所有 order_id + 挂单前验证
        - 当 pending 非空时，清除所有未成交订单的 order_id
        - 原因：重启后这些 order_id 可能已经失效（被取消/成交/过期）
        """
        # 如果已有 pending 且有数据，需要清理 order_id（重启场景）
        if 'pending' in state and state['pending']:
            cleared_count = 0
            # [Bug修复] 确保每个 pending 都有完整的必要字段
            for p_str, info in state['pending'].items():
                # 确保 original_side 存在
                if 'original_side' not in info:
                    info['original_side'] = info.get('side', '')
                
                # [Bug修复] 确保 fill_count 存在（避免后续递增出错）
                if 'fill_count' not in info:
                    info['fill_count'] = 0
                
                # [Bug修复] 确保 suspect 字段存在
                if 'suspect' not in info:
                    info['suspect'] = False
                
                # [Bug修复] 确保 done 字段存在
                if 'done' not in info:
                    info['done'] = False
                
                # [方案D] 重启时清除所有未成交订单的 order_id
                # 原因：重启后无法确认这些订单是否仍然有效
                if info.get('order_id') and not info.get('done'):
                    old_oid = info['order_id']
                    info['order_id'] = None
                    info['suspect'] = True  # 标记为可疑，触发后续验证
                    cleared_count += 1
                    self.logger.warning(f"[重启清理] {symbol} @ {p_str} 清除失效 order_id: {old_oid}")
            
            if cleared_count > 0:
                self.logger.info(f"[重启清理] {symbol} 清除 {cleared_count} 个失效 order_id，将重新挂单")
            
            return
        
        # [新增] 检查并清理损坏的 prices
        original_prices = list(state.get('prices', []))
        if not original_prices or any(p == '0' or not p for p in original_prices):
            self.logger.warning(f"[重建] {symbol} prices 包含损坏数据: {original_prices}，跳过重建")
            return
        
        # 过滤掉无效价格
        original_prices = [p for p in original_prices if p and p != '0']
        if not original_prices:
            self.logger.warning(f"[重建] {symbol} 无有效价格，跳过")
            return
        
        pending = {}
        processed_prices = set()
        
        # 从 sell_orders 重建
        for order in state.get('sell_orders', []):
            p_str = str(order.get('price', ''))
            if not p_str or p_str in processed_prices:
                continue
            processed_prices.add(p_str)
            pending[p_str] = {
                'side': 'sell',
                'original_side': 'sell',
                'order_id': order.get('order_id') or None,
                'done': order.get('status') == 'filled',
                'fill_count': 0,
                'suspect': False,
            }
        
        # 从 buy_orders 重建
        for order in state.get('buy_orders', []):
            p_str = str(order.get('price', ''))
            if not p_str or p_str in processed_prices:
                continue
            processed_prices.add(p_str)
            pending[p_str] = {
                'side': 'buy',
                'original_side': 'buy',
                'order_id': order.get('order_id') or None,
                'done': order.get('status') == 'filled',
                'fill_count': 0,
                'suspect': False,
            }
        
        # 保留原始 prices 顺序
        if original_prices:
            state['prices'] = original_prices
        
        state['pending'] = pending
        
        # 初始化交易计数
        if 'trade_count' not in state:
            state['trade_count'] = 0
        
        if pending:
            self.logger.info(f"[重建] {symbol} 从 {len(pending)} 个订单重建 pending 结构")

    def get_precision(self, symbol: str) -> Tuple[int, int]:
        """获取市场精度配置，返回 (amount_precision, price_precision)"""
        if symbol in self.markets:
            m = self.markets[symbol].get('precision', {})
            return m.get('amount', 2), m.get('price', 6)
        return 2, 6  # 默认值

    def calculate_dynamic_params(self, symbol: str) -> Dict:
        """静态网格参数 - 固定 8 层
        层数: 8
        价格档位: 9 (去除中间点)
        每层金额: $6.25 ($25 / 4)
        """
        return {
            'grid_layers': 8,
            'volatility': 10.0,
            'rsi_std': 10.0,
            'follow_budget': 25,
            'amount_per_trade': 6.25
        }

    def init_grid(self, symbol: str, entry_price: float,
                  tp: float = None, sl: float = None, layers: int = 5) -> Dict:
        """初始化网格数据结构（不直接下单，只生成计划）
        改动：中间价那格设为 side='buy', done=True，避免重复买入
        """
        # 获取精度 (tuple) 用于计算
        prec_amt, prec_price = self.get_precision(symbol)
        # 获取完整精度 dict 用于存储
        precision_dict = self.markets.get(symbol, {}).get('precision', {'amount': 2, 'price': 6})
        
        price_prec = prec_price
        amount_prec = prec_amt
        
        # [新增] 精度保护：极低价格币种（如 PEPE）精度可能为 0，需要自动提升
        if price_prec == 0 and entry_price < 0.001:
            # 根据价格自动计算合适精度
            if entry_price > 0:
                price_prec = max(6, int(-math.log10(entry_price)) + 2)
            else:
                price_prec = 8  # 默认
            self.logger.warning(f"[精度保护] {symbol} 原始精度={prec_price}，自动提升为 {price_prec} (entry_price={entry_price})")

        if tp is None:
            tp = entry_price * 1.10
        if sl is None:
            sl = entry_price * 0.90

        # 获取市价 (增加安全校验)
        market_price = entry_price  # 默认回退值
        if self.exchange:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                # 必须检查 'last' 是否存在以及是否有效
                if ticker and 'last' in ticker and ticker['last'] is not None:
                    market_price = float(ticker['last'])
            except Exception as e:
                self.logger.warning(f"初始化网格时获取市价失败，使用入场价代替: {e}")

        # 计算网格区间
        entry_d = Decimal(str(entry_price))
        spread_d = Decimal(str(self.grid_spread))

        upper = float(entry_d * (Decimal('1') + spread_d))
        lower = float(entry_d * (Decimal('1') - spread_d))

        step = (upper - lower) / layers
        prices = []
        for i in range(layers + 1):
            p = lower + step * i
            prices.append(round_price(p, price_prec))

        prices = sorted(list(set(prices)))

        amount_per_trade = self.budget / layers
        amount_per_trade = round_amount(amount_per_trade, amount_prec)

        # 生成挂单计划
        safety_threshold = step * 0.2
        pending = {}

        # 找到中间价那格（最接近 entry_price 的格子索引）
        mid_idx = len(prices) // 2
        
        for i, p_str in enumerate(prices):
            p = parse_price(p_str)
            
            # [新增] 建仓点处理：中间价那格强制添加，不受 safety_threshold 影响
            is_mid = (i == mid_idx)
            
            if not is_mid and abs(p - market_price) < safety_threshold:
                self.logger.info(f"铺单跳过 {symbol} @ {p_str} (距离市价 ${market_price:.4f})")
                continue

            # [修复] 使用 entry_price（持仓均价）决定方向，而不是当前市场价格
            # 网格核心逻辑：高于均价的格子挂卖单，低于均价的格子挂买单
            side = 'sell' if p > entry_price else 'buy'
            
            # 建仓点处理：中间价那格保持基于 entry_price 的方向判断
            # 只确保 done=False（等待挂单，不是已完成）
            if is_mid:
                done = False  # [修复] 建仓点应该等待挂单，不是已完成
                self.logger.info(f"[建仓点] {symbol} 中间价 {p_str} 方向={side} done=False (等待挂单)")
            else:
                done = False
            
            pending[p_str] = {
                'side': side,
                'original_side': side,  # 记录原始方向，用于恢复
                'order_id': None,
                'done': done,
                'fill_count': 0,
                'suspect': False,
            }

        self.grid_state[symbol] = {
            'symbol': symbol,
            'precision': precision_dict,
            'prices': prices,
            'prices_set': set(prices),
            'pending': pending,
            'amount_per_trade': amount_per_trade,
            'entry_price': entry_price,  # 持仓均价
            'position_size': 0,          # 持仓数量（网格独立追踪）
            'market_price': market_price,
            'init_time': time.time(),
            'trade_count': 0,
            'realized_pnl': 0,
            'upper': upper,
            'lower': lower,
            'tp': tp,                    # 止盈价格
            'sl': sl,                    # 止损价格
        }

        self.logger.info(f"网格计划已生成 {symbol}: 区间[{lower:.4f}-{upper:.4f}]")

        self.save_state()

        return self.grid_state[symbol]

    def is_market_choppy(self, symbol: str) -> Tuple[bool, str]:
        """判断是否震荡行情"""
        try:
            if not self.exchange:
                return True, '测试模式'
            
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe='1h', limit=100)
            if not ohlcv or len(ohlcv) < 20:
                return True, '数据不足'
            
            import pandas as pd
            import numpy as np
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # 使用 Wilder's Smoothing (与主策略一致)
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            
            rs = avg_gain / avg_loss
            rsi = (100 - (100 / (1 + rs))).iloc[-1]
            
            prices = df['close'].iloc[-10:].values
            x = np.arange(len(prices))
            slope = np.polyfit(x, prices, 1)[0]
            trend_pct = slope / prices[-1] * 100
            
            df['tr'] = np.maximum(
                df['high'] - df['low'],
                np.maximum(
                    abs(df['high'] - df['close'].shift(1)),
                    abs(df['low'] - df['close'].shift(1))
                )
            )
            atr = df['tr'].rolling(14).mean().iloc[-1]
            atr_ratio = atr / df['close'].iloc[-1]
            
            score = 0
            reasons = []
            
            if 40 < rsi < 60:
                score += 1
                reasons.append(f'RSI={rsi:.0f}')
            elif 35 < rsi < 65:
                score += 0.5
            
            if abs(trend_pct) < 0.1:
                score += 1
                reasons.append('趋势平缓')
            elif abs(trend_pct) < 0.3:
                score += 0.5
            
            if atr_ratio < 0.02:
                score += 1
            elif atr_ratio < 0.05:
                score += 0.5
            
            choppy = score >= 1.5
            reason = '; '.join(reasons) if reasons else '数据不完整'
            
            self.logger.info(f"市场 {symbol}: 分数={score}/3, 震荡={choppy}, {reason}")
            
            return choppy, reason
            
        except Exception as e:
            self.logger.error(f"判断市场状态失败 {symbol}: {e}")
            return True, '异常'

    def _handle_ghost_order(self, symbol: str, p_str: str, info: Dict, client_oid: str, filled_orders: List[Dict]) -> bool:
        """
        [新增] 处理幽灵单：本地有订单记录但交易所查询不到
        
        原因可能是：
        1. 订单已成交但未同步
        2. 订单被撤销/过期
        3. 网络丢单
        
        处理方式：查询历史订单确认状态
        
        返回: True 表示已处理（找到历史记录），False 表示未处理（需要标记 suspect）
        """
        try:
            if not self.exchange:
                return False
            
            # 查询历史订单
            hist_result = self.exchange.fetch_order_history(symbol, limit=100)
            if not hist_result or not hist_result.get('data'):
                return False
            
            # 遍历历史订单查找
            for order in hist_result['data']:
                hist_cl_oid = order.get('clOrdId', '')
                
                # 检查是否匹配当前订单
                if hist_cl_oid == client_oid:
                    state_val = order.get('state', '')
                    filled_sz = float(order.get('accFillSz', 0) or order.get('sz', 0))
                    
                    if state_val in ['2', 'filled'] or filled_sz > 0:
                        # 订单已成交
                        info['done'] = True
                        info['filled_at'] = time.time()
                        info['order_id'] = None
                        info['filled_amount'] = filled_sz
                        filled_orders.append({
                            'price': parse_price(p_str),
                            'side': info.get('original_side', 'buy'),
                            'price_str': p_str,
                            'amount': filled_sz
                        })
                        self.logger.info(f"[幽灵单] 订单 {client_oid} 历史确认已成交: {filled_sz}")
                        return True
                    
                    elif state_val in ['3', '4', '6', 'canceled', 'rejected']:
                        # 订单已取消或失败
                        info['order_id'] = None
                        info['done'] = False
                        self.logger.info(f"[幽灵单] 订单 {client_oid} 历史确认已取消")
                        return True
            
            # 历史订单中也没找到
            return False
            
        except Exception as e:
            self.logger.error(f"[幽灵单] 查询历史订单失败 {symbol}: {e}")
            return False

    def _sync_and_recover_grid(self, symbol: str) -> List[Dict]:
        """崩溃恢复核心逻辑 - 网格策略"高卖低买"
        
        网格策略：
        - prices 从低到高排序 [p0, p1, p2, p3, p4, p5]
        - 买单挂在低价格，卖单挂在高价格
        - 买单成交(index=i) → 在 index+1 挂卖单（更高价卖出）
        - 卖单成交(index=i) → 在 index-1 挂买单（更低价买入）
        
        恢复算法：
        1. 对每个格子，尝试用 original_side 反查 OKX 订单
        2. 找到订单 → 更新 order_id，已成交则标记 done
        3. 未找到订单 → 根据价格推断成交情况
        4. 返回 filled_orders 列表，供 update_grid_after_fill 补单
        """
        if symbol not in self.grid_state:
            return []
        
        state = self.grid_state[symbol]
        pending = state.get('pending', {})
        
        # 获取当前市场价格
        current_price = state.get('entry_price', 0)
        if self.exchange:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and 'last' in ticker and ticker['last']:
                    current_price = float(ticker['last'])
            except Exception as e:
                self.logger.debug(f"获取市场价格失败: {e}")
        
        # [修复] 初始化 curr_char，避免变量未定义错误
        curr_char = None
        
        filled_orders = []
        clean_sym = symbol.replace('/', '').replace('-', '')[:4]
        
        # 获取格子索引
        prices = state.get('prices', [])
        
        for p_str, info in pending.items():
            p = parse_price(p_str)
            original_side = info.get('original_side') or info.get('side', 'buy')
            current_side = 'sell' if p > current_price else 'buy'
            
            try:
                cell_idx = prices.index(p_str)
            except (ValueError, KeyError):
                cell_idx = deterministic_hash(p_str)  # 使用确定性哈希
            
            cell_fill_count = info.get('fill_count', 0)
            
            # [修复] 只跳过 done=true 的订单
            # 如果 order_id 存在但 done=false，仍然需要检查订单是否成交
            if info.get('done'):
                continue
            
            order_found = False
            
            # 尝试用 original_side 反查
            orig_char = 's' if original_side == 'sell' else 'b'
            orig_oid = f"g{clean_sym}{orig_char}{cell_idx}t{cell_fill_count}"
            
            if self.exchange:
                try:
                    result = self.exchange.fetch_order_by_client_id(symbol, orig_oid)
                    
                    if result and result.get('state'):
                        # 用 original_side 找到订单
                        order_data = result
                        ord_id = order_data.get('ordId')
                        state_val = order_data.get('state', '')
                        
                        info['order_id'] = ord_id
                        order_found = True
                        
                        if state_val in ['2', 'filled']:
                            info['done'] = True
                            filled_sz = float(order_data.get('accFillSz', 0) or order_data.get('sz', 0))
                            filled_orders.append({
                                'price': p,
                                'side': original_side,
                                'price_str': p_str,
                                'amount': filled_sz
                            })
                            self.logger.info(f"[恢复] {symbol} @ {p_str} original方向订单成交: {original_side}")
                        
                        elif state_val in ['4', '6', 'canceled', 'rejected']:
                            info['done'] = False
                            info['order_id'] = None
                            self.logger.info(f"[恢复] {symbol} @ {p_str} original方向订单已取消")
                    
                except Exception as e:
                    # [修复] 检查是否是订单不存在错误
                    err_msg = str(e).lower()
                    if '51603' in err_msg or '51016' in err_msg or 'order not found' in err_msg or 'does not exist' in err_msg:
                        # 订单不存在，可能是幽灵单，检查历史订单确认状态
                        self.logger.info(f"[恢复] {symbol} @ {p_str} original方向订单不存在，检查历史订单")
                        
                        # [新增] 幽灵单处理：检查历史订单
                        ghost_handled = self._handle_ghost_order(symbol, p_str, info, orig_oid, filled_orders)
                        if not ghost_handled:
                            # 历史订单也没找到，标记 suspect 等待重新挂单
                            info['suspect'] = True
                            info['order_id'] = None
                            self.logger.info(f"[恢复] {symbol} @ {p_str} original方向标记为可疑，等待重新挂单")
                    else:
                        self.logger.debug(f"[恢复] {symbol} @ {p_str} original方向反查异常: {e}")
            
            # 如果 original_side 没找到，尝试 current_side
            if not order_found and current_side != original_side:
                curr_char = 's' if current_side == 'sell' else 'b'
                curr_oid = f"g{clean_sym}{curr_char}{cell_idx}t{cell_fill_count}"
                
                if self.exchange:
                    try:
                        result = self.exchange.fetch_order_by_client_id(symbol, curr_oid)
                        
                        if result and result.get('state'):
                            # 用 current_side 找到订单
                            order_data = result
                            ord_id = order_data.get('ordId')
                            state_val = order_data.get('state', '')
                            
                            info['order_id'] = ord_id
                            # 保持 original_side，让 update_grid_after_fill 处理补单方向
                            order_found = True
                            
                            if state_val in ['2', 'filled']:
                                info['done'] = True
                                filled_sz = float(order_data.get('accFillSz', 0) or order_data.get('sz', 0))
                                # 记录实际成交方向，用于补单
                                filled_orders.append({
                                    'price': p,
                                    'side': original_side,  # 使用原始方向
                                    'price_str': p_str,
                                    'amount': filled_sz
                                })
                                self.logger.info(f"[恢复] {symbol} @ {p_str} current方向订单成交: {current_side}")
                            
                            elif state_val in ['4', '6', 'canceled', 'rejected']:
                                info['done'] = False
                                info['order_id'] = None
                                self.logger.info(f"[恢复] {symbol} @ {p_str} current方向订单已取消")
                    
                    except Exception as e:
                        # [修复] 检查是否是订单不存在错误
                        err_msg = str(e).lower()
                        if '51603' in err_msg or '51016' in err_msg or 'order not found' in err_msg or 'does not exist' in err_msg:
                            # 订单不存在，可能是幽灵单，检查历史订单确认状态
                            self.logger.info(f"[恢复] {symbol} @ {p_str} current方向订单不存在，检查历史订单")
                            
                            # [新增] 幽灵单处理：检查历史订单
                            ghost_handled = self._handle_ghost_order(symbol, p_str, info, curr_oid, filled_orders)
                            if not ghost_handled:
                                # 历史订单也没找到，标记 suspect 等待重新挂单
                                info['suspect'] = True
                                info['order_id'] = None
                                self.logger.info(f"[恢复] {symbol} @ {p_str} current方向标记为可疑，等待重新挂单")
                        else:
                            self.logger.debug(f"[恢复] {symbol} @ {p_str} current方向反查异常: {e}")
                
                # [完备] 即使 current_side 找到订单且是live，也要检查历史订单确认是否之前已成交
                # 特别是：fill_count=n返回live时，可能fill_count=n-1已经成交过
                if order_found and not info.get('done'):
                    if self.exchange:
                        try:
                            # 检查当前fill_count的历史订单
                            hist_result = self.exchange.fetch_order_history(symbol, limit=50)
                            if hist_result and hist_result.get('data'):
                                # 先检查当前fill_count
                                for order in hist_result['data']:
                                    cl_oid = order.get('clOrdId', '')
                                    if cl_oid == orig_oid or cl_oid == curr_oid:
                                        state_val = order.get('state', '')
                                        if state_val in ['2', 'filled']:
                                            filled_sz = float(order.get('accFillSz', 0) or order.get('sz', 0))
                                            info['done'] = True
                                            # [精简] pending 只保留 6 字段，删除 filled_at/filled_amount
                                            filled_orders.append({
                                                'price': p,
                                                'side': original_side,
                                                'price_str': p_str,
                                                'amount': filled_sz
                                            })
                                            self.logger.info(f"[恢复] {symbol} @ {p_str} 历史订单确认成交: {original_side}")
                                            break
                                
                                # 如果当前fill_count没成交，检查fill_count-1的历史（可能之前已成交并重试挂单）
                                if not info.get('done') and cell_fill_count > 0:
                                    prev_fill_count = cell_fill_count - 1
                                    prev_orig_oid = f"g{clean_sym}{orig_char}{cell_idx}t{prev_fill_count}"
                                    # [修复] curr_char 可能为 None，使用 fallback
                                    curr_char_fallback = curr_char if curr_char else ('s' if current_side == 'sell' else 'b')
                                    prev_curr_oid = f"g{clean_sym}{curr_char_fallback}{cell_idx}t{prev_fill_count}"
                                    for order in hist_result['data']:
                                        cl_oid = order.get('clOrdId', '')
                                        if cl_oid == prev_orig_oid or cl_oid == prev_curr_oid:
                                            state_val = order.get('state', '')
                                            if state_val in ['2', 'filled']:
                                                # fill_count-1已经成交了，说明当前fill_count是重试的新订单
                                                filled_sz = float(order.get('accFillSz', 0) or order.get('sz', 0))
                                                info['done'] = True
                                                # [修复] 不回退fill_count，保持当前值（因为连续网格，-1成交意味着当前也应该成交）
                                                # info['fill_count'] = prev_fill_count  # 不再回退
                                                filled_orders.append({
                                                    'price': p,
                                                    'side': original_side,
                                                    'price_str': p_str,
                                                    'amount': filled_sz
                                                })
                                                self.logger.info(f"[恢复] {symbol} @ {p_str} fill_count={prev_fill_count}已成交(当前fill_count={cell_fill_count}): {original_side}")
                                                break
                        except Exception as e:
                            self.logger.debug(f"[恢复] {symbol} @ {p_str} 查询历史异常: {e}")
            
            # 两个方向都没找到订单，查询历史订单确认
            if not order_found:
                # 尝试查询历史订单
                filled_from_history = False
                
                if self.exchange:
                    try:
                        # 尝试用 original_side 查询历史订单
                        result = self.exchange.fetch_order_history(symbol, limit=50)
                        if result and result.get('data'):
                            for order in result['data']:
                                cl_oid = order.get('clOrdId', '')
                                if cl_oid == orig_oid or cl_oid == curr_oid:
                                    state_val = order.get('state', '')
                                    if state_val in ['2', 'filled']:
                                        # 成交了
                                        filled_sz = float(order.get('accFillSz', 0) or order.get('sz', 0) or 0)
                                        actual_side = 'sell' if 's' in cl_oid else 'buy'
                                        info['done'] = True
                                        # [精简] pending 只保留 6 字段，删除 filled_at/filled_amount
                                        info['order_id'] = order.get('ordId')
                                        # [修复] 不改变side，只记录成交信息，让后续挂单逻辑处理
                                        filled_orders.append({
                                            'price': p,
                                            'side': original_side,  # 使用原始方向
                                            'price_str': p_str,
                                            'amount': filled_sz
                                        })
                                        self.logger.info(f"[恢复] {symbol} @ {p_str} 历史订单确认成交: {actual_side} {filled_sz}")
                                        filled_from_history = True
                                        break
                                    elif state_val in ['4', '6', 'canceled', 'rejected']:
                                        # 已取消，重新挂单
                                        self.logger.info(f"[恢复] {symbol} @ {p_str} 历史订单确认已取消")
                                        filled_from_history = True
                                        break
                    except Exception as e:
                        self.logger.debug(f"[恢复] {symbol} @ {p_str} 查询历史订单异常: {e}")
                
                # [修复] 如果当前 fill_count 没找到订单，检查 fill_count-1 的历史订单
                # 这处理了：fill_count=2 时，fill_count=1 已经成交并重试挂单的情况
                if not filled_from_history and cell_fill_count > 0:
                    prev_fill_count = cell_fill_count - 1
                    prev_orig_oid = f"g{clean_sym}{orig_char}{cell_idx}t{prev_fill_count}"
                    # curr_char 可能在 original_side == current_side 时未定义，需要检查
                    prev_curr_oid = None
                    if current_side != original_side:
                        curr_char_fallback = 's' if current_side == 'sell' else 'b'
                        prev_curr_oid = f"g{clean_sym}{curr_char_fallback}{cell_idx}t{prev_fill_count}"
                    
                    if self.exchange:
                        try:
                            hist_result = self.exchange.fetch_order_history(symbol, limit=50)
                            if hist_result and hist_result.get('data'):
                                for order in hist_result['data']:
                                    cl_oid = order.get('clOrdId', '')
                                    if cl_oid == prev_orig_oid or (prev_curr_oid and cl_oid == prev_curr_oid):
                                        state_val = order.get('state', '')
                                        if state_val in ['2', 'filled']:
                                            # fill_count-1 已经成交
                                            filled_sz = float(order.get('accFillSz', 0) or order.get('sz', 0))
                                            info['done'] = True
                                            # [Bug修复2] 移除fill_count回退，保持当前fill_count不变
                                            # info['fill_count'] = prev_fill_count  # 回退到之前的 fill_count
                                            filled_orders.append({
                                                'price': p,
                                                'side': original_side,
                                                'price_str': p_str,
                                                'amount': filled_sz
                                            })
                                            self.logger.info(f"[恢复] {symbol} @ {p_str} fill_count={prev_fill_count}已成交(当前fill_count={cell_fill_count}): {original_side}")
                                            filled_from_history = True
                                            break
                        except Exception as e:
                            self.logger.debug(f"[恢复] {symbol} @ {p_str} 查询fill_count-1历史异常: {e}")
                
                if not filled_from_history:
                    # [Bug修复3] 移除价格推断成交逻辑，用价格比较推断成交不可靠
                    # 只记录日志，由后续同步逻辑处理
                    self.logger.info(f"[恢复] {symbol} @ {p_str} 未找到订单，无法确认成交状态")
        
        if filled_orders:
            self.save_state()
        
        return filled_orders
    
    def _sync_pending_orders_from_exchange(self, symbol: str):
        """[兼容旧版] 从交易所同步 pending 订单状态
        调用完备版恢复逻辑
        """
        self._sync_and_recover_grid(symbol)
    
    def _adjust_grid_direction(self, symbol: str):
        """根据当前价格调整网格方向（保守模式）
        只修复方向重叠和低价卖问题，不干扰正常网格流转
        """
        if symbol not in self.grid_state:
            return
        
        state = self.grid_state[symbol]
        entry_price = state.get('entry_price')
        if not entry_price:
            return
        
        # 获取当前市场价格
        current_price = entry_price
        if self.exchange:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and 'last' in ticker and ticker['last']:
                    current_price = float(ticker['last'])
            except Exception as e:
                self.logger.debug(f"获取市场价格失败，使用均价: {e}")
        
        pending = state.get('pending', {})
        
        # [修复] 只处理没有挂单且未成交的格子
        buy_prices = []
        sell_prices = []
        
        for p_str, info in pending.items():
            # 只处理没有挂单的格子
            if info.get('order_id') or info.get('done'):
                continue
            
            p = parse_price(p_str)
            side = info.get('side', '')
            
            if side == 'buy':
                buy_prices.append(p)
            elif side == 'sell':
                sell_prices.append(p)
        
        adjusted = False
        
        # 1. 检查方向重叠（买入价 >= 卖出价）
        if buy_prices and sell_prices:
            min_buy = min(buy_prices)
            max_sell = max(sell_prices)
            
            if min_buy >= max_sell:
                self.logger.warning(f"[方向调整] {symbol} 检测到方向重叠! 最低买价={min_buy:.2e}, 最高卖价={max_sell:.2e}")
                adjusted = True
        
        # [Medium Bug修复 2026-03-17] 移除低价卖翻转逻辑
        # 原逻辑：如果卖单价位低于当前市场价，强制翻转成买单
        # 风险：价格瞬间暴涨时不应追高买入，会增加单向敞口风险
        # 正确做法：让卖单被成交或等待自然撤销，不主动翻转方向
        
        if adjusted:
            self.save_state()
            return
        
        # 无需调整
        self.logger.debug(f"[方向调整] {symbol} 无需调整")

    def sync_orders(self, symbol: str) -> List[Dict]:
        """
        同步挂单 (核心实盘逻辑)
        [完备版] 结合订单同步和方向调整
        返回：崩溃恢复期间成交的订单列表，供调用者处理补单
        """
        if symbol not in self.grid_state:
            return []

        state = self.grid_state[symbol]
        
        # [完备版] 崩溃恢复：同步订单 + 调整方向 + 返回成交记录
        filled_orders = self._sync_and_recover_grid(symbol)
        
        # [新增] 动态调整网格方向 - 基于持仓均价
        self._adjust_grid_direction(symbol)
        
        # [P1修复] suspect 状态冻结问题：获取当前市场价格
        current_price = state.get('entry_price')
        if self.exchange:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and 'last' in ticker and ticker['last']:
                    current_price = float(ticker['last'])
            except Exception as e:
                self.logger.debug(f"sync_orders 获取市场价格失败: {e}")
        
        pending = state.get('pending', {})
        
        # 费率
        FEE_RATE = 0.001

        for p_str, info in pending.items():
            # [P1修复] suspect 状态冻结：如果 suspect=True 且 done=True（已成交）
            # 当价格回到该格子附近时，只清除 suspect，不重置 done
            # 重要：done=True 表示订单已成交，不应该被重置！
            if info.get('suspect') and info.get('done') and current_price:
                p = parse_price(p_str)
                if p:
                    # 计算价格偏差（使用相对偏差百分比）
                    price_deviation = abs(p - current_price) / current_price if current_price > 0 else 1
                    # 阈值：5% 或 2 个网格间距（取较大值）
                    prices = state.get('prices', [])
                    grid_spacing = 0.02  # 默认 2%
                    if len(prices) > 1:
                        # 估算网格间距
                        try:
                            sorted_prices = sorted([parse_price(px) for px in prices if px])
                            spacings = [sorted_prices[i+1] - sorted_prices[i] for i in range(len(sorted_prices)-1) if sorted_prices[i] > 0]
                            if spacings:
                                grid_spacing = max(spacings) / max(sorted_prices)
                        except:
                            pass
                    
                    threshold = max(0.05, grid_spacing * 2)  # 至少 5% 或 2 倍网格间距
                    
                    if price_deviation <= threshold:
                        # [修复] 只清除 suspect，不重置 done
                        # done=True 表示订单已成交，应该保持不变
                        info['suspect'] = False
                        # 不要重置 done！让它保持 True，等待 update_grid_after_fill 处理
                        self.logger.info(f"[P1修复] suspect冻结恢复: {symbol} @ {p_str} 价格接近当前 ${current_price:.2e}，清除 suspect 但保持 done=True")
                    else:
                        # 价格尚未回到该格子，跳过
                        continue
            # [修复] 当 done=True 时，不应该在这里重置！
            # 网格核心逻辑：
            # - 卖单成交 → 在 index-1（更低价）挂买单 → 由 update_grid_after_fill 处理
            # - 买单成交 → 在 index+1（更高价）挂卖单 → 由 update_grid_after_fill 处理
            # 
            # 这里的 done=True 是成交标记，应该保持不变，等待 update_grid_after_fill
            # 处理目标格子。当前格子（成交格）不需要重新挂单！
            if info.get('done'):
                # [P1修复] 问题7: done=True + order_id=None 逻辑混乱
                # 明确区分"正常成交"和"异常状态"
                
                # 情况A: 正常成交 - done=True 且 order_id 存在
                # 保持 done=True，等待 update_grid_after_fill 处理目标格子
                if info.get('order_id'):
                    self.logger.debug(f"[网格循环] {symbol} @ {p_str} 保持 done=True（正常成交），等待补单逻辑")
                    continue
                
                # 情况B: 异常状态 - done=True 但 order_id=None
                # 这说明订单被取消或不存在，需要重置并重新挂单
                # [Bug修复1] 增加suspect标记，避免误判重复挂单
                # [第2轮修复] done=True + order_id=None 表示订单已成交（order_id 在成交后被清除）
                # 应该保持 done=True，只标记 suspect，不重复挂单
                else:
                    info['suspect'] = True  # 标记为可疑
                    # [第2轮修复] 不要重置 done=False，保持 done=True 表示已成交
                    # 继续往下会检查 done=True 并跳过挂单
                    self.logger.warning(f"[网格循环] {symbol} @ {p_str} 订单已成交(done=True)，标记suspect，保持done状态")
                    continue  # [第2轮修复] 直接跳过，不重复挂单
            
            # 1. 如果已经完成，跳过
            if info.get('done'):
                continue
            
            # [方案D修复] 挂单前验证 order_id 是否有效
            # 如果有 order_id 但标记为 suspect，或者需要验证，检查订单是否仍然有效
            if info.get('order_id') and (info.get('suspect') or not info.get('verified')):
                try:
                    if self.exchange:
                        # 查询订单状态
                        order = self.exchange.fetch_order(info['order_id'], symbol)
                        status = order.get('status', '')
                        okx_state = order.get('state', '')
                        
                        # 检查订单是否已失效（canceled/rejected/filled）
                        if status in ['canceled', 'rejected', 'filled'] or okx_state in ['2', '3', '4', '6', 'filled', 'canceled', 'rejected']:
                            self.logger.info(f"[订单验证] {symbol} @ {p_str} order_id={info['order_id']} 状态={status or okx_state}，清除 order_id")
                            info['order_id'] = None
                            info['suspect'] = True
                            info['verified'] = True
                            # 如果已成交，标记 done
                            if status == 'filled' or okx_state in ['2', 'filled']:
                                info['done'] = True
                                self.logger.info(f"[订单验证] {symbol} @ {p_str} 订单已成交，标记 done=True")
                        else:
                            # 订单仍然有效，标记 verified
                            info['verified'] = True
                            self.logger.debug(f"[订单验证] {symbol} @ {p_str} order_id={info['order_id']} 仍有效")
                except Exception as e:
                    # 验证失败，检查是否是订单不存在错误
                    err_msg = str(e).lower()
                    if '51603' in err_msg or '51016' in err_msg or 'order not found' in err_msg or 'does not exist' in err_msg:
                        # 订单不存在，清除 order_id
                        self.logger.info(f"[订单验证] {symbol} @ {p_str} order_id={info['order_id']} 不存在，清除 order_id")
                        info['order_id'] = None
                        info['suspect'] = True
                        info['verified'] = True
                    else:
                        # 其他错误，标记 verified 让后续流程处理
                        info['verified'] = True
                        self.logger.warning(f"[订单验证] {symbol} @ {p_str} 验证失败: {e}")
            
            # [修复] suspect 标记检查需要优先处理
            # 如果 suspect=True，先检查订单是否真的存在
            if info.get('suspect'):
                # [Bug5修复 2026-03-16] suspect=True 且 order_id 不存在时
                # 需要区分是否已成交，避免遗漏补单
                if not info.get('order_id'):
                    if info.get('done'):
                        # 已成交但 order_id 被清除（可能是成交后状态同步问题）
                        # 清除 suspect，保持 done，触发补单流程
                        info['suspect'] = False
                        self.logger.info(f"[sync_orders] suspect订单已成交(done=True)，清除suspect触发补单")
                        # 不 continue，继续处理补单
                    else:
                        # 未成交，清除 suspect 并跳过（不需要重新挂单）
                        info['suspect'] = False
                        self.logger.info(f"[sync_orders] suspect订单未成交，清除suspect标记")
                        continue
                else:
                    try:
                        if self.exchange:
                            open_orders = self.exchange.fetch_open_orders(symbol)
                            order_ids = [str(o.get('id') or o.get('ordId', '')) for o in open_orders]
                            if info['order_id'] not in order_ids:
                                # 订单确实不在挂单列表了，清除 suspect 并重置 order_id
                                info['suspect'] = False
                                info['order_id'] = None
                                self.logger.info(f"[sync_orders] suspect订单 {info.get('order_id')} 已确认不在交易所，重置状态")
                            else:
                                # 订单仍在，清除 suspect
                                info['suspect'] = False
                    except Exception as e:
                        self.logger.warning(f"[sync_orders] suspect检查失败: {e}")
                        # suspect 检查失败时，清除 suspect 标记让后续逻辑处理
                        info['suspect'] = False
            
            # 2. [关键] 如果已经有 order_id，说明已经在交易所了，跳过！
            if info.get('order_id'):
                continue
            
            # 3. 以下是挂单逻辑
            
            # 获取该格的预算金额
            amount_per_trade = state.get('amount_per_trade', 10.0)
            
            # 如果 p_str 为空或 0，跳过此格
            if not p_str or p_str == '0':
                self.logger.warning(f"[sync_orders] {symbol} 价格为空或0，跳过")
                continue
            
            # 如果有记录的实际成交数量，使用它
            if info.get('filled_amount'):
                if info['side'] == 'sell' and 'last_filled_amount' in info:
                    actual_amount = info['last_filled_amount'] * (1 - FEE_RATE)
                else:
                    actual_amount = info.get('last_filled_amount', amount_per_trade / float(p_str))
            else:
                actual_amount = amount_per_trade / float(p_str)
            
            price = float(p_str)
            
            # 精度处理
            prec_amt, prec_price = self.get_precision(symbol)
            safe_amount = round_amount(actual_amount, prec_amt)
            safe_price = round_price(price, prec_price)

            if safe_amount <= 0:
                self.logger.warning(f"下单数量过小 {symbol} @ {p_str}: {actual_amount}")
                continue

            # [新增] 最小数量检查
            min_amount = self.markets.get(symbol, {}).get('limits', {}).get('amount', {}).get('min', 0)
            if safe_amount < min_amount:
                # 如果已经有 order_id，说明之前已经挂过单了，复用之
                if info.get('order_id'):
                    self.logger.warning(f"下单数量低于最小值但已有挂单 {symbol} @ {p_str}，复用 order_id={info['order_id']}")
                    continue
                else:
                    self.logger.warning(f"下单数量低于交易所最小值 {symbol}: {safe_amount} < {min_amount}，跳过此格")
                    info['done'] = True  # 标记完成，防止死循环
                    continue

            # --- [修复] 确定性 Client OID ---
            # 格式: g + 交易对(4位) + 方向 + 格子索引 + 成交计数
            # 例如: gBNBs0t0 (第0个格子，第0次成交)
            # [修复] 使用格子索引代替价格哈希，避免低价币种哈希冲突（如 0.0005 和 0.0015 都会生成 "0"）
            clean_sym = symbol.replace('/', '').replace('-', '')[:4]  # ETHUSDT -> ETHU
            side_char = 's' if info['side'] == 'sell' else 'b'
            
            # 获取格子在价格列表中的索引
            prices = state.get('prices', [])
            try:
                cell_idx = prices.index(p_str)  # 用格子索引
            except (ValueError, KeyError):
                cell_idx = deterministic_hash(p_str)  # 回退：使用确定性哈希
            
            cell_fill_count = info.get('fill_count', 0)  # 成交计数
            client_oid = f"g{clean_sym}{side_char}{cell_idx}t{cell_fill_count}"
            # --- [修复结束] ---

            try:
                if self.exchange:
                    order = self.exchange.create_limit_order(symbol, info['side'], safe_price, safe_amount, client_oid=client_oid)
                    if order and order.get('code') == '0':
                        order_id = order.get('data', [{}])[0].get('ordId')
                        if order_id:
                            info['order_id'] = order_id
                            info['pending_amount'] = safe_amount
                            self.logger.info(f"网格挂单 {symbol}: {info['side']} {safe_amount:.4f} @ {safe_price} (ID: {order_id})")
                        else:
                            self.logger.warning(f"挂单成功但无订单ID: {order}")
                    else:
                        s_code = order.get('data', [{}])[0].get('sCode', '')
                        if s_code == '51139':
                            # Client Order ID 重复，说明这个格子已经有订单了，反查
                            self.logger.warning(f"Client Order ID 重复 {symbol} @ {p_str}，尝试反查")
                            check_res = self.exchange.fetch_order_by_client_id(symbol, client_oid)
                            if check_res and check_res.get('data'):
                                exist_order = check_res['data'][0]
                                info['order_id'] = exist_order['ordId']
                                exist_state = exist_order.get('state', '')
                                self.logger.info(f"✅ 反查找回网格单: {exist_order['ordId']}, state={exist_state}")
                                
                                # [P0修复] 只有确认订单已成交(state='2')后才标记完成
                                if exist_state == '2':
                                    info['done'] = True
                                    info['fill_count'] = info.get('fill_count', 0) + 1
                                    # [Bug修复] 获取成交数量并加入 filled_orders，触发补单
                                    filled_sz = float(exist_order.get('accFillSz', 0) or exist_order.get('sz', 0) or 0)
                                    filled_orders.append({
                                        'price': parse_price(p_str),
                                        'side': info.get('original_side', 'buy'),
                                        'price_str': p_str,
                                        'amount': filled_sz
                                    })
                                    self.logger.info(f"✅ 订单已成交，标记完成 {symbol} @ {p_str}")
                                else:
                                    # 订单存在但未成交，复用之
                                    self.logger.info(f"✅ 复用已有挂单 {symbol} @ {p_str}: order_id={info['order_id']}, state={exist_state}")
                            else:
                                # 反查无结果，标记为可疑订单
                                info['suspect'] = True
                                info['order_id'] = None
                                self.logger.warning(f"⚠️ Client Order ID 重复但反查无结果 {symbol} @ {p_str}，标记 suspect")
                                continue  # [修复] 跳过挂单，避免重复下单
                        elif s_code == '51016':
                            # Client order ID already exists - 订单已存在，反查状态
                            self.logger.warning(f"Client Order ID 已存在 {symbol} @ {p_str}，反查订单状态")
                            check_res = self.exchange.fetch_order_by_client_id(symbol, client_oid)
                            
                            if check_res and 'ordId' in check_res:
                                # 订单存在，获取状态
                                info['order_id'] = check_res.get('ordId')
                                exist_state = check_res.get('state', '')
                                
                                if exist_state == '2':  # 已成交
                                    info['done'] = True
                                    info['filled_at'] = time.time()
                                    # [Bug修复] 使用正确的变量 check_res 而不是 exist_order
                                    filled_sz = float(check_res.get('accFillSz', 0) or check_res.get('filled', 0) or 0)
                                    info['filled_amount'] = filled_sz
                                    filled_orders.append({
                                        'price': parse_price(p_str),
                                        'side': info.get('original_side', 'buy'),
                                        'price_str': p_str,
                                        'amount': filled_sz
                                    })
                                    self.logger.info(f"✅ Client Order ID 已存在但已成交 {symbol} @ {p_str}: {info['side']}")
                                else:
                                    # 订单存在且未成交（state='live'），复用之
                                    self.logger.info(f"✅ 复用已有挂单 {symbol} @ {p_str}: order_id={info['order_id']}, state={exist_state}")
                                    continue  # 不用再挂单了
                            elif check_res and check_res.get('code') == '51603':
                                # 订单不存在，标记为可疑订单，依赖 reconcile 处理深检
                                # 不再直接递增 fill_count，避免幽灵单导致计数器失控
                                info['suspect'] = True
                                info['order_id'] = None
                                self.logger.warning(f"⚠️ 订单不存在 {symbol} @ {p_str}，标记 suspect，依赖 reconcile 处理")
                            else:
                                # 反查失败，标记为完成避免重复尝试
                                self.logger.warning(f"⚠️ 反查失败 {symbol} @ {p_str}，标记为完成")
                                info['done'] = True
                        elif s_code == '51008':
                            info['done'] = True
                            self.logger.warning(f"资金不足，跳过 {symbol} {info['side']} @ {price}")
                        else:
                            self.logger.error(f"挂单失败 {symbol}: {order}")
                else:
                    info['order_id'] = f"test_{symbol}_{info['side']}_{p_str}"
            except Exception as e:
                self.logger.error(f"网格挂单失败 {symbol} {info['side']} @ {price}: {e}")

        self.save_state()
        
        # [修复] 不再在 sync_orders 内部调用 update_grid_after_fill
        # 由调用者（启动流程、check_positions）统一调用补单逻辑
        # 避免重复补单导致的双重挂单问题
        
        return filled_orders if 'filled_orders' in dir() else []

    def reconcile_orders(self, symbol: str) -> List[Dict]:
        """[修复完备版] 启动时对账：清理幽灵单并捕获离线成交
        返回: 离线期间成交的订单列表，供后续补单使用
        """
        if symbol not in self.grid_state:
            return []

        state = self.grid_state[symbol]
        pending = state['pending']
        cleaned = False
        filled_offline_orders = []  # 收集离线成交单

        for p_str, info in pending.items():
            order_id = info.get('order_id')
            if not order_id:
                continue

            try:
                if self.exchange:
                    order = self.exchange.fetch_order(order_id, symbol)
                    status = order.get('status', '')
                    # 兼容 OKX 的 state 字段
                    okx_state = order.get('state', '')
                    if status == 'filled' or okx_state in ['2', 'filled']:
                        # 1. 发现离线期间完全成交
                        self.logger.info(f"[对账] 发现离线成交 {symbol}: {info['side']} @ {p_str}")
                        info['done'] = True
                        info['fill_count'] = info.get('fill_count', 0) + 1  # 递增成交计数
                        info['filled_at'] = time.time()
                        # 获取实际成交量：优先使用 ccxt 的 filled，其次从 info 中获取
                        filled_sz = float(order.get('filled', 0) or 0)
                        if filled_sz == 0:
                            order_info = order.get('info', {})
                            filled_sz = float(order_info.get('accFillSz', 0) or order_info.get('sz', 0))
                        # 构造数据包，格式需与 check_order_status 返回的一致
                        filled_offline_orders.append({
                            'price': parse_price(p_str),
                            'side': info.get('original_side', 'buy'),
                            'price_str': p_str,
                            'amount': filled_sz
                        })
                        info['order_id'] = None
                        cleaned = True

                    elif status in ['canceled', 'rejected'] or okx_state in ['3', '6']:
                        # 2. 订单失效 (OKX: 3=canceled, 6=failed)
                        self.logger.info(f"[对账] 订单失效 {symbol} @ {p_str}: {status} (state={okx_state})")
                        info['done'] = False
                        info['order_id'] = None
                        cleaned = True

                    elif status == 'closed' or okx_state == '4':
                        # 3. 处理部分成交后关闭的情况
                        filled_sz = float(order.get('filled', 0) or 0)
                        if filled_sz == 0:
                            order_info = order.get('info', {})
                            filled_sz = float(order_info.get('accFillSz', 0) or order_info.get('sz', 0))
                        if filled_sz > 0:
                            self.logger.info(f"[对账] 发现离线部分成交 {symbol}: {info['side']} @ {p_str}")
                            info['done'] = True
                            info['fill_count'] = info.get('fill_count', 0) + 1  # 递增成交计数
                            info['filled_at'] = time.time()
                            filled_offline_orders.append({
                                'price': parse_price(p_str),
                                'side': info.get('original_side', 'buy'),
                                'price_str': p_str,
                                'amount': filled_sz
                            })
                        info['order_id'] = None
                        cleaned = True

                    elif status == 'open' or okx_state == '1':
                        # 订单正常，不做处理 (OKX: 1=open)
                        pass

                    else:
                        # 测试模式或其他状态，清理 ID
                        info['order_id'] = None
                        cleaned = True

                else:
                    info['order_id'] = None
                    cleaned = True
            except Exception as e:
                # 检查是否是订单不存在错误 (51016, 51603)
                err_msg = str(e).lower()
                if '51603' in err_msg or '51016' in err_msg or 'order not found' in err_msg or 'does not exist' in err_msg:
                    self.logger.warning(f"[对账] 订单不存在 {symbol} {order_id}，可能是已撤销或过期，重置状态")
                    info['order_id'] = None
                    info['done'] = False
                    cleaned = True
                else:
                    self.logger.warning(f"[对账] 查询失败 {symbol} {order_id}: {e}")

        if cleaned:
            self.save_state()

        if filled_offline_orders:
            self.logger.info(f"[对账] {symbol} 捕获 {len(filled_offline_orders)} 笔离线成交，准备补单")

        return filled_offline_orders

    def force_reconcile(self, symbol: str) -> List[Dict]:
        """[修复完备版] 周期强制对账：从交易所同步真实状态
        返回: 查漏补缺发现的成交单列表，供后续补单使用
        """
        if symbol not in self.grid_state:
            return []

        state = self.grid_state[symbol]
        pending = state['pending']
        reconciled = False
        filled_late_orders = []  # 收集漏掉的成交单

        for p_str, info in pending.items():
            if not info.get('order_id'):
                continue
            # 如果已经标记 done，就不用再查了
            if info.get('done'):
                continue

            try:
                order = self.exchange.fetch_order(info['order_id'], symbol)
                status = order.get('status', '')
                # 兼容 OKX 的 state 字段
                okx_state = order.get('state', '')
                if status in ['filled', 'closed'] or okx_state in ['2', 'filled', '4']:
                    # 优先使用 ccxt 的 filled 字段，其次从 info 中获取
                    filled_sz = float(order.get('filled', 0) or 0)
                    if filled_sz == 0:
                        order_info = order.get('info', {})
                        filled_sz = float(order_info.get('accFillSz', 0) or order_info.get('sz', 0))
                    # 只有成交量 > 0 才处理
                    if filled_sz > 0:
                        info['done'] = True
                        info['fill_count'] = info.get('fill_count', 0) + 1  # 递增成交计数
                        info['filled_at'] = time.time()
                        info['order_id'] = None  # [修复] 清理 order_id，避免"幽灵已完成"
                        info['filled_amount'] = filled_sz
                        filled_late_orders.append({
                            'price': parse_price(p_str),
                            'side': info.get('original_side', 'buy'),
                            'price_str': p_str,
                            'amount': filled_sz
                        })
                        self.logger.info(f"[强制对账] 同步漏单成交 {symbol}: {info.get('original_side', 'buy')} @ {p_str} ({filled_sz:.4f})")
                        reconciled = True

                    if (status == 'closed' or okx_state == '4') and filled_sz == 0:
                        # 被取消且无成交
                        info['order_id'] = None
                        reconciled = True

                elif status == 'canceled' or okx_state in ['3', '6']:
                    info['order_id'] = None
                    info['done'] = False
                    self.logger.info(f"[强制对账] 订单已取消 {symbol} @ {p_str}，将重新挂单")
                    reconciled = True

            except Exception as e:
                # 网络问题，记录日志但不中断
                # 检查是否是订单不存在错误 (51016, 51603)
                err_msg = str(e).lower()
                if '51603' in err_msg or '51016' in err_msg or 'order not found' in err_msg or 'does not exist' in err_msg:
                    self.logger.warning(f"[强制对账] 订单不存在 {symbol} @ {p_str}，可能是已撤销或过期，重置状态")
                    info['order_id'] = None
                    info['done'] = False
                    reconciled = True
                else:
                    self.logger.warning(f"[强制对账] 网络异常 {symbol} @ {p_str}: {e}")

        if reconciled:
            self.save_state()
            self.logger.info(f"[强制对账] {symbol} 状态已同步")

        return filled_late_orders

    def check_order_status(self, symbol: str) -> List[Dict]:
        """
        检查订单状态 (核心实盘逻辑)
        职责：只负责查状态，不负责挂单！
        返回成交记录供 update_grid_after_fill 使用。
        """
        if symbol not in self.grid_state:
            return []

        state = self.grid_state[symbol]
        
        # [修复] 添加 pending 检查，防止 None 错误
        if not state or 'pending' not in state:
            return []
        
        pending = state['pending']
        
        if not pending:
            return []
            
        filled_orders = []

        for p_str, info in pending.items():
            if not info.get('order_id') or info.get('done'):
                continue

            order_id = info['order_id']

            try:
                if self.exchange:
                    order = self.exchange.fetch_order(order_id, symbol)
                    status = order.get('status')
                    # 优先使用 ccxt 的 filled 字段，其次从 info 中获取 OKX 的 accFillSz
                    filled_sz = float(order.get('filled', 0) or 0)
                    if filled_sz == 0:
                        # 兼容：从 info 中获取 OKX 的 accFillSz
                        order_info = order.get('info', {})
                        filled_sz = float(order_info.get('accFillSz', 0) or order_info.get('sz', 0))
                else:
                    status = 'open'
                    filled_sz = 0

                # 只有明确查到 FILLED 才处理
                # 兼容 OKX 的 state 字段：state="2" 表示成交
                okx_state = order.get('state', '')
                if status == 'filled' or okx_state in ['2', 'filled']:
                    info['done'] = True
                    info['filled_at'] = time.time()
                    info['order_id'] = None  # [P0修复] 清除订单ID
                    info['fill_count'] = info.get('fill_count', 0) + 1  # [P0修复] 递增成交计数

                    filled_orders.append({
                        'price': parse_price(p_str),
                        'side': info.get('original_side', 'buy'),
                        'price_str': p_str,
                        'amount': filled_sz
                    })

                    self.logger.info(f"✅ 网格单成交 {symbol}: {info.get('original_side', 'buy')} @ {p_str} ({filled_sz:.4f})")

                elif status == 'canceled' or okx_state in ['3', '6']:
                    # 被取消，重置 ID 让 sync_orders 重新补挂 (OKX: 3=canceled, 6=failed)
                    self.logger.info(f"网格单被取消 {symbol} @ {p_str}，将重新挂单")
                    info['order_id'] = None

                elif status == 'closed' or okx_state == '4':
                    # CLOSED 可能是部分成交或完全没成交 (OKX: 4=partially filled)
                    if filled_sz > 0:
                        info['done'] = True
                        info['filled_at'] = time.time()
                        info['order_id'] = None  # [P0修复] 清除订单ID
                        info['fill_count'] = info.get('fill_count', 0) + 1  # [P0修复] 递增成交计数
                        filled_orders.append({
                            'price': parse_price(p_str),
                            'side': info.get('original_side', 'buy'),
                            'price_str': p_str,
                            'amount': filled_sz
                        })
                        self.logger.info(f"✅ 网格单部分成交 {symbol}: {info.get('original_side', 'buy')} @ {p_str} ({filled_sz:.4f})")
                    else:
                        info['order_id'] = None
                        self.logger.info(f"网格单已关闭无成交 {symbol} @ {p_str}，将重新挂单")

            except Exception as e:
                self.logger.warning(f"查询订单异常 {symbol} {order_id}: {e}")
                
                # [增强] 专门处理 51603 "Order does not exist" 错误
                err_msg = str(e).lower()
                if '51603' in err_msg or '51016' in err_msg or 'order not found' in err_msg or 'does not exist' in err_msg:
                    # 订单不存在，这是"幽灵单"问题 - 本地有记录但交易所没有
                    # 可能是：已成交未同步、被撤销过期、网络丢单等
                    self.logger.warning(f"[幽灵单处理] 订单 {order_id} 在交易所不存在 (51603)，检查是否成交")
                    
                    # 回退机制：查询历史订单确认状态
                    try:
                        if self.exchange:
                            # 尝试查询历史订单
                            hist_result = self.exchange.fetch_order_history(symbol, limit=50)
                            if hist_result and hist_result.get('data'):
                                for order in hist_result['data']:
                                    # 检查是否是同一个订单（通过 clOrdId 或 ordId）
                                    hist_ord_id = str(order.get('ordId', ''))
                                    hist_cl_oid = order.get('clOrdId', '')
                                    
                                    if hist_ord_id == order_id or hist_cl_oid:
                                        state_val = order.get('state', '')
                                        filled_hist_sz = float(order.get('accFillSz', 0) or order.get('sz', 0))
                                        
                                        if state_val in ['2', 'filled'] or filled_hist_sz > 0:
                                            # 历史订单显示已成交
                                            info['done'] = True
                                            info['filled_at'] = time.time()
                                            info['order_id'] = None  # 清除 order_id
                                            info['fill_count'] = info.get('fill_count', 0) + 1  # [P0修复] 递增成交计数
                                            info['filled_amount'] = filled_hist_sz
                                            filled_orders.append({
                                                'price': parse_price(p_str),
                                                'side': info.get('original_side', 'buy'),
                                                'price_str': p_str,
                                                'amount': filled_hist_sz
                                            })
                                            self.logger.info(f"[幽灵单处理] 订单 {order_id} 历史确认已成交: {filled_hist_sz}")
                                            break
                                        elif state_val in ['3', '4', '6', 'canceled', 'rejected']:
                                            # 已取消或失败，重置状态等待重新挂单
                                            info['order_id'] = None
                                            info['done'] = False
                                            info['suspect'] = True
                                            self.logger.info(f"[幽灵单处理] 订单 {order_id} 历史确认已取消，将重新挂单")
                                            break
                            
                            # 如果历史订单也没找到，假定订单已失效，重置状态
                            if not info.get('done'):
                                info['order_id'] = None
                                info['done'] = False
                                info['suspect'] = True
                                self.logger.warning(f"[幽灵单处理] 订单 {order_id} 历史未找到，标记为可疑等待重新挂单")
                                
                    except Exception as hist_e:
                        self.logger.error(f"[幽灵单处理] 查询历史订单失败: {hist_e}")
                        # 保守处理：清除 order_id，标记 suspect，让 sync_orders 重新处理
                        info['order_id'] = None
                        info['suspect'] = True
                else:
                    # 非 51603 错误，回退机制：查询交易所挂单列表
                    # 但这次只检查是否还在，不盲目假定成交
                    try:
                        if self.exchange:
                            open_orders = self.exchange.fetch_open_orders(symbol)
                            order_ids_on_exchange = [o.get('ordId') or o.get('id') for o in open_orders]
                            
                            if order_id not in order_ids_on_exchange:
                                # 订单不在挂单列表了
                                # 但我们不知道是被撤了还是网络问题，不要假定成交
                                # 标记为可疑，等待下次检查
                                self.logger.warning(f"[回退对账] 订单 {order_id} 不在挂单列表，标记为可疑")
                                info['suspect'] = True  # 标记可疑，下次同步时处理
                    except Exception as fallback_e:
                        self.logger.error(f"[回退对账] 也失败了 {symbol}: {fallback_e}")

        if filled_orders:
            self.save_state()

        return filled_orders

    def update_grid_after_fill(self, symbol: str, filled_orders: List[Dict]):
        """成交后补反向单 - 实现网格"高卖低买"循环
        
        补单规则：
        - 买单成交(index=i) → 在 index+1 挂卖单（更高价卖出获利）
        - 卖单成交(index=i) → 在 index-1 挂买单（更低价买入等待）
        
        这样价格就在网格间循环：买→卖→买→卖...
        
        使用 6 字段结构: side, original_side, order_id, done, fill_count, suspect
        """
        if not filled_orders:
            return

        state = self.grid_state[symbol]
        pending = state['pending']
        prices = state['prices']

        # 费率预估（OKX 现货 Maker 约 0.1%）
        FEE_RATE = 0.001

        # 计算已实现盈亏
        realized_pnl = state.get('realized_pnl', 0)
        
        # 去重：记录已处理的订单，避免重复处理
        processed = set()
        
        # [Bug修复5] 在处理成交订单前先清理源格子
        # 成交后源格子的order_id应该被清除，标记为done=True
        for order in filled_orders:
            p_str = order['price_str']
            if p_str in pending:
                source_info = pending[p_str]
                source_info['order_id'] = None
                source_info['done'] = True
                self.logger.info(f"[网格流转] 清理源格子 {symbol} @ {p_str}")
        
        for order in filled_orders:
            p_str = order['price_str']
            
            # 去重
            order_key = f"{p_str}_{order.get('side')}"
            if order_key in processed:
                continue
            processed.add(order_key)
            
            # [P1修复] 问题5: 当前格子不存在导致 ValueError
            # 使用 try-except 包裹 prices.index(p_str)
            try:
                current_idx = prices.index(p_str)
            except ValueError:
                self.logger.warning(f"网格流转 {symbol}: 成交价格 {p_str} 不在网格价格列表中，跳过处理")
                continue
            
            side = order['side']
            
            # [P0修复] 移除此处递增 fill_count
            # fill_count 已在 check_order_status/reconcile_orders/force_reconcile 中递增
            # 避免重复递增导致每笔成交 +2
            
            # 获取实际成交数量
            filled_amount = order.get('amount', 0)
            if filled_amount == 0:
                filled_amount = state.get('amount_per_trade', 10.0) / float(p_str)

            # 买单成交 -> 更高价位挂卖单（index + 1）
            # 卖单成交 -> 更低价位挂买单（index - 1）
            target_idx = current_idx + 1 if side == 'buy' else current_idx - 1

            if 0 <= target_idx < len(prices):
                target_p_str = prices[target_idx]
                
                # [P1修复] 问题4: 目标格子不存在导致 KeyError
                # 在访问 pending[target_p_str] 前检查是否存在
                if target_p_str not in pending:
                    self.logger.warning(f"网格流转 {symbol}: 目标价格 {target_p_str} 不存在于 pending 中，跳过处理")
                    continue
                
                target_info = pending[target_p_str]

                # 检查目标格子是否已有挂单 - 核心冲突处理逻辑
                existing_order_id = target_info.get('order_id')
                existing_done = target_info.get('done', False)
                existing_side = target_info.get('side', '')
                
                new_side = 'sell' if side == 'buy' else 'buy'
                
                # 情况1: 目标格子已有未成交的同向挂单 → 复用，不重复挂单
                if existing_order_id and not existing_done and existing_side == new_side:
                    self.logger.info(f"网格流转 {symbol}: {side}@{p_str} -> {target_p_str} 已有同向挂单 {existing_order_id}，复用之")
                    # [Bug修复4] 移除目标格子处的fill_count递增，避免重复
                    # fill_count已在check_order_status/reconcile_orders中递增
                    state['trade_count'] += 1
                    continue
                
                # 情况2: 目标格子有未成交的挂单但方向不同 → 先取消旧订单，再挂新单
                if existing_order_id and not existing_done and existing_side != new_side:
                    self.logger.warning(f"网格流转 {symbol}: {side}@{p_str} -> {target_p_str} 方向冲突 (旧:{existing_side} 新:{new_side})，先撤销旧单")
                    cancel_failed = False
                    try:
                        if self.exchange:
                            self.exchange.cancel_order(symbol, existing_order_id)
                            self.logger.info(f"已撤销冲突挂单 {existing_order_id}")
                    except Exception as e:
                        self.logger.error(f"撤销冲突挂单失败: {e}")
                        # [P1修复] 问题8: 撤销失败时也要更新目标格子状态
                        # 即使撤销失败，仍然需要更新目标格子的状态以便后续处理
                        cancel_failed = True
                    
                    if cancel_failed:
                        # 撤销失败，清除旧的 order_id，保留其他状态让下次同步处理
                        target_info['order_id'] = None
                        target_info['done'] = False
                        self.logger.warning(f"网格流转 {symbol}: 撤销失败，标记目标格子 {target_p_str} 需要重新挂单")
                        # 不 continue，继续执行后续逻辑
                
                # 情况3: 目标格子 done=True（之前已成交）或没有 order_id → 重置并挂新单
                if existing_done:
                    self.logger.info(f"网格流转 {symbol}: {side}@{p_str} -> {target_p_str} 目标格子之前已成交(done=True)，重置挂新单")
                
                # 计算已实现盈亏（正确逻辑）
                # 买单成交：更新持仓
                # 卖单成交：(卖出价 - 持仓均价) × 数量 - 手续费
                filled_value = filled_amount * float(p_str)
                
                if side == 'sell':
                    # 卖出：计算真正的盈亏
                    # [修复] 优先使用 avg_price (真实持仓成本)
                    cost_price = state.get('avg_price', state.get('entry_price', float(p_str)))
                    position_size = state.get('position_size', 0)
                    
                    if position_size > 0:
                        # 计算实际卖出的数量（不能超过持仓）
                        actual_sell_amount = min(filled_amount, position_size)
                        
                        # 卖出盈利 = (卖出价 - 成本价) × 实际卖出数量
                        profit = (float(p_str) - cost_price) * actual_sell_amount
                        # 扣除手续费
                        profit -= (actual_sell_amount * float(p_str)) * FEE_RATE
                        realized_pnl += profit
                        
                        # 更新持仓数量
                        state['position_size'] = max(0, position_size - actual_sell_amount)
                        # 如果清仓了，重置 avg_price
                        if state['position_size'] == 0:
                            state['avg_price'] = 0
                        self.logger.info(f"网格卖出盈亏计算: 卖出价={p_str}, 成本价={cost_price}, 实际卖出={actual_sell_amount}, 盈利={profit:.2f}")
                    else:
                        self.logger.warning(f"网格卖出 {symbol}@{p_str} 但无持仓记录")
                else:
                    # 买入：更新持仓均价和数量
                    old_size = state.get('position_size', 0)
                    # [修复] 优先使用 avg_price (真实持仓成本)
                    old_price = state.get('avg_price', state.get('entry_price', 0))
                    
                    if old_size > 0:
                        # [Bug修复] 买入时扣除手续费，与 PositionManager 保持一致
                        actual_amount = filled_amount * (1 - FEE_RATE)
                        # 加权平均计算新均价
                        new_size = old_size + actual_amount
                        new_avg = (old_price * old_size + float(p_str) * actual_amount) / new_size
                        # [修复] 更新 avg_price 而不是 entry_price
                        state['avg_price'] = new_avg
                        state['position_size'] = new_size
                    else:
                        # 首次买入
                        state['avg_price'] = float(p_str)
                        state['position_size'] = filled_amount
                    
                    self.logger.info(f"网格买入更新持仓: 均价={state.get('avg_price', 0):.4f}, 数量={state['position_size']:.4f}")
                
                # 简化逻辑：成交后直接设置目标格子 done=False，准备挂单
                # 不设置任何等待标记
                target_info['done'] = False
                target_info['order_id'] = None
                target_info['side'] = new_side
                target_info['original_side'] = new_side  # 更新 original_side
                
                # [Bug修复4] 只在情况2（目标格子需要新挂单）时递增fill_count
                # 情况1（复用已有挂单）不应递增，避免重复
                target_info['fill_count'] = target_info.get('fill_count', 0) + 1
                
                # 记录成交数量（保留用于计算）
                if side == 'buy':
                    target_info['last_filled_amount'] = filled_amount * (1 - FEE_RATE)
                else:
                    target_info['last_filled_amount'] = filled_amount

                if 'trade_count' not in state:
                    state['trade_count'] = 0
                state['trade_count'] += 1
                self.logger.info(f"网格流转 {symbol}: {side}@{p_str}({filled_amount:.4f}) -> 计划 {new_side}@{target_p_str}(fill_count={target_info['fill_count']})")
            else:
                # [P1修复] 问题6: 边界情况未处理（买在最低价/卖在最高价）
                # 当 target_idx 超出网格价格范围时，记录日志并跳过
                if side == 'buy':
                    self.logger.warning(f"网格流转 {symbol}: 买单@{p_str} 已触及最高价 {prices[-1]}，无法继续向上流转")
                else:
                    self.logger.warning(f"网格流转 {symbol}: 卖单@{p_str} 已触及最低价 {prices[0]}，无法继续向下流转")
        
        # 更新已实现盈亏
        state['realized_pnl'] = realized_pnl
        self.save_state()

    def cancel_all_grid_orders(self, symbol: str, delete_state: bool = True):
        """撤销所有网格挂单
        
        Args:
            symbol: 交易对
            delete_state: 是否删除网格状态。False 时仅撤销挂单但保留状态，用于平仓流程中先撤单再平仓的场景
        """
        if symbol not in self.grid_state:
            return

        state = self.grid_state[symbol]
        pending = state.get('pending', {})

        for p_str, info in pending.items():
            if info.get('order_id') and not info.get('done'):
                try:
                    if self.exchange:
                        self.exchange.cancel_order(symbol, info['order_id'])
                    self.logger.info(f"撤销网格单 {symbol} {info['order_id']}")
                except Exception as e:
                    self.logger.error(f"撤销失败 {symbol}: {e}")

        # [修复] 只有在 delete_state=True 时才删除状态
        # 避免平仓失败后网格状态丢失
        if delete_state and symbol in self.grid_state:
            del self.grid_state[symbol]
            self.save_state()

    # =========================================================================
    # [新增] 价格偏离处理 - 当市场价格偏离网格价格时主动撤单重挂
    # =========================================================================
    
    def check_price_deviation(self, symbol: str) -> Dict:
        """
        检查价格偏离情况并处理
        [已修复] 逻辑断层问题：使用 init_grid 彻底重构 pending
        """
        if not self.PRICE_DEVIATION_CHECK_ENABLED:
            return {'action_taken': False, 'reason': 'disabled'}
        
        if symbol not in self.grid_state:
            return {'action_taken': False, 'reason': 'no_grid'}
        
        state = self.grid_state[symbol]
        entry_price = state.get('entry_price', 0)
        
        if not entry_price or entry_price == 0:
            return {'action_taken': False, 'reason': 'no_entry_price'}
        
        # 获取当前市场价格
        current_price = entry_price
        if self.exchange:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and 'last' in ticker and ticker['last']:
                    current_price = float(ticker['last'])
            except Exception as e:
                self.logger.warning(f"获取市场价格失败 {symbol}: {e}")
                return {'action_taken': False, 'reason': 'fetch_ticker_failed'}
        
        # 计算偏离百分比
        deviation = abs(current_price - entry_price) / entry_price
        
        result = {
            'action_taken': False,
            'deviation': deviation,
            'current_price': current_price,
            'grid_center': entry_price,
            'cancelled_orders': 0,
            'replaced_orders': 0
        }
        
        # 检查是否超过阈值
        if deviation <= self.PRICE_DEVIATION_THRESHOLD:
            self.logger.debug(f"{symbol} 价格偏离 {deviation*100:.2f}% <= {self.PRICE_DEVIATION_THRESHOLD*100}%，无需处理")
            return result
        
        # 超过阈值，需要处理
        self.logger.warning(f"{symbol} 价格偏离 {deviation*100:.2f}% > {self.PRICE_DEVIATION_THRESHOLD*100}%，执行撤单重挂")
        
        pending = state.get('pending', {})
        cancelled_count = 0
        
        # 1. 取消所有活跃挂单
        # [Bug4修复 2026-03-17] 检查订单不存在错误，可能已成交
        # 注意：这里只记录警告，不强行恢复持仓
        # 原因：main.py 的 check_positions 中有交易所同步逻辑，会自动矫正持仓
        
        for p_str, info in pending.items():
            order_id = info.get('order_id')
            if order_id and not info.get('done'):
                try:
                    if self.exchange:
                        self.exchange.cancel_order(symbol, order_id)
                    cancelled_count += 1
                    self.logger.info(f"[价格偏离] 撤销订单 {symbol} @ {p_str}: {order_id}")
                except Exception as e:
                    err_msg = str(e).lower()
                    if '51603' in err_msg or '51016' in err_msg or 'order not found' in err_msg or 'does not exist' in err_msg:
                        self.logger.warning(f"[价格偏离] 订单不存在，可能已成交或被撤销: {order_id}")
                    else:
                        self.logger.error(f"[价格偏离] 撤销订单失败 {symbol}: {e}")
        
        # 2. [关键修复] 使用 init_grid 重新生成所有数据结构 (pending, prices 等)
        # 保存关键状态，防止持仓数据丢失
        saved_pnl = state.get('realized_pnl', 0)
        saved_trade_count = state.get('trade_count', 0)
        saved_position_size = state.get('position_size', 0)  # [修复] 保存持仓数量
        saved_avg_price = state.get('avg_price', state.get('entry_price', 0))  # [修复] 保存真实持仓均价
        
        self.logger.warning(f"[价格偏离] 以现价 {current_price} 重构网格，保留持仓: {saved_position_size}")
        
        # 调用初始化函数，根据新价格生成全新的 pending 字典
        self.init_grid(
            symbol,
            entry_price=current_price,
            layers=self.grid_layers  # 保持原层数
        )
        
        # 恢复累积数据
        if symbol in self.grid_state:
            new_state = self.grid_state[symbol]
            new_state['realized_pnl'] = saved_pnl
            new_state['trade_count'] = saved_trade_count
            # [修复] 恢复持仓数据
            new_state['position_size'] = saved_position_size
            new_state['avg_price'] = saved_avg_price  # 区分网格中轴和真实成本
        
        # 更新统计结果
        new_pending_count = len(self.grid_state[symbol].get('pending', {}))
        result['replaced_orders'] = new_pending_count
        result['action_taken'] = True
        result['cancelled_orders'] = cancelled_count
        
        self.save_state()
        self.logger.info(f"[价格偏离] 处理完成: 取消 {cancelled_count} 单, 新生成 {result['replaced_orders']} 个挂单计划")
        
        return result
    
    def handle_price_deviation_and_sync(self, symbol: str) -> List[Dict]:
        """
        综合处理：价格偏离检查 + 订单同步
        
        这是主要调用方法，结合了价格偏离检查和订单同步
        返回: 成交订单列表（供补单使用）
        """
        # 1. 先检查价格偏离
        dev_result = self.check_price_deviation(symbol)
        
        # 2. 执行订单同步
        filled_orders = self.sync_orders(symbol)
        
        # 如果有成交订单，返回给调用者处理补单
        return filled_orders