#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
左侧抄底 RSI 反弹策略
适合大跌后的超卖反弹行情

特点：
1. RSI 超卖时买入
2. 价格接近布林带下轨时买入
3. 分批建仓，摊低成本
4. 止损宽（-15%），止盈适中（+10-15%）
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple
from dataclasses import dataclass


@dataclass
class RSISignal:
    """RSI反弹信号"""
    action: str  # 'buy', 'sell', 'hold'
    rsi: float
    bb_position: float
    confidence: float = 0.0  # 0-1 (带默认值)
    batch: int = 0  # 0=hold, 1=第一批, 2=第二批 (带默认值)
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reasons: list = None  # 带默认值
    # RSI背离相关字段
    divergence: str = 'none'  # 'none', 'bullish', 'bearish'
    divergence_strength: float = 0.0  # 0-1 背离强度


class RSIContrarianStrategy:
    """左侧抄底 RSI 反弹策略"""
    
    def __init__(self, capital=150):
        self.name = "RSIContrarian"
        self.capital = capital
        
        # 参数 - 分批建仓策略
        self.rsi_oversold_1 = 30  # 第一批建仓：RSI < 30（2026-02-07 调整为更严格）
        self.rsi_oversold_2 = 25  # 第二批建仓：RSI < 25
        self.rsi_overbought = 70  # 超买阈值 (2026-03-01 调整: 65->70 避免过于激进)
        self.bb_position_buy_1 = 0.30  # 第一批：BB位置 < 30%
        self.bb_position_buy_2 = 0.20  # 第二批：BB位置 < 20%
        self.bb_position_sell = 0.75  # BB位置高于此值卖出
        
        # 止损止盈
        self.stop_loss_pct = 0.15  # -15%
        self.take_profit_pct = 0.12  # +12%
        
        # 分批建仓
        self.max_positions = 2  # 最多2批
        self.batch_amount = 50  # 每批$50
        
        # 持仓管理
        self.max_holding_hours = 168  # 最长7天
        self.trailing_stop_pct = 0.05  # 移动止盈 5%
    
    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算RSI (修正为 Wilder's Smoothing 以匹配 OKX/TradingView)"""
        delta = df['close'].diff()
        
        # 分离涨跌
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        # 使用 ewm (指数加权移动平均) 模拟 Wilder's Smoothing
        # alpha=1/period 是标准做法
        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_bb(self, df: pd.DataFrame, period: int = 20, std: int = 2) -> pd.DataFrame:
        """计算布林带"""
        df = df.copy()
        
        df['bb_mid'] = df['close'].rolling(period).mean()
        df['bb_std'] = df['close'].rolling(period).std()
        df['bb_upper'] = df['bb_mid'] + std * df['bb_std']
        df['bb_lower'] = df['bb_mid'] - std * df['bb_std']
        
        # 布林带位置
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
        
        return df
    
    def detect_divergence(self, df: pd.DataFrame, lookback: int = 20) -> Tuple[str, float]:
        """检测RSI背离
        
        Args:
            df: K线数据
            lookback: 回看周期数
            
        Returns:
            Tuple[str, float]: (背离类型, 背离强度)
            背离类型: 'none', 'bullish' (底背离-买入), 'bearish' (顶背离-卖出)
            背离强度: 0-1, 表示背离的显著程度
        """
        if df is None or len(df) < lookback + 5:
            return 'none', 0.0
        
        # 计算RSI
        rsi = self.calculate_rsi(df, 14)
        
        # 获取最近lookback周期的数据
        prices = df['close'].values
        rsi_values = rsi.values
        
        # 排除NaN
        valid_idx = np.where(~np.isnan(rsi_values))[0]
        if len(valid_idx) < lookback:
            return 'none', 0.0
        
        # 取最近的数据段
        prices = prices[-lookback:]
        rsi_values = rsi_values[-lookback:]
        
        # 找到局部最高点和最低点
        # 使用简单的方法：找到显著的高点/低点
        
        # 1. 顶背离检测：价格创新高，但RSI没有创新高
        # 找最近的两个明显高点
        price_highs = self._find_peaks(prices, lookback=5)
        rsi_highs = self._find_peaks(rsi_values, lookback=5)
        
        if len(price_highs) >= 2 and len(rsi_highs) >= 2:
            # 比较最近的两个高点
            last_price_high_idx = price_highs[-1]
            prev_price_high_idx = price_highs[-2]
            
            last_rsi_high_idx = rsi_highs[-1]
            prev_rsi_high_idx = rsi_highs[-2]
            
            # 价格创新高 (最近高点高于之前高点)
            price_new_high = prices[last_price_high_idx] > prices[prev_price_high_idx]
            
            # RSI没有创新高 (最近RSI高点低于之前RSI高点)
            rsi_no_new_high = rsi_values[last_rsi_high_idx] <= rsi_values[prev_rsi_high_idx] + 2  # 允许2点微小差异
            
            if price_new_high and rsi_no_new_high:
                # 计算顶背离强度
                price_strength = (prices[last_price_high_idx] - prices[prev_price_high_idx]) / prices[prev_price_high_idx]
                rsi_weakening = (rsi_values[prev_rsi_high_idx] - rsi_values[last_rsi_high_idx]) / max(rsi_values[prev_rsi_high_idx], 1)
                strength = min(0.5 + price_strength * 5 + rsi_weakening * 2, 1.0)
                return 'bearish', max(strength, 0.3)
        
        # 2. 底背离检测：价格创新低，但RSI没有创新低
        price_lows = self._find_troughs(prices, lookback=5)
        rsi_lows = self._find_troughs(rsi_values, lookback=5)
        
        if len(price_lows) >= 2 and len(rsi_lows) >= 2:
            # 比较最近的两个低点
            last_price_low_idx = price_lows[-1]
            prev_price_low_idx = price_lows[-2]
            
            last_rsi_low_idx = rsi_lows[-1]
            prev_rsi_low_idx = rsi_lows[-2]
            
            # 价格创新低 (最近低点低于之前低点)
            price_new_low = prices[last_price_low_idx] < prices[prev_price_low_idx]
            
            # RSI没有创新低 (最近RSI低点高于之前RSI低点)
            rsi_no_new_low = rsi_values[last_rsi_low_idx] >= rsi_values[prev_rsi_low_idx] - 2
            
            if price_new_low and rsi_no_new_low:
                # 计算底背离强度
                price_drop = (prices[prev_price_low_idx] - prices[last_price_low_idx]) / prices[prev_price_low_idx]
                rsi_strength = (rsi_values[last_rsi_low_idx] - rsi_values[prev_rsi_low_idx]) / max(rsi_values[prev_rsi_low_idx], 1)
                strength = min(0.5 + price_drop * 5 + rsi_strength * 2, 1.0)
                return 'bullish', max(strength, 0.3)
        
        return 'none', 0.0
    
    def _find_peaks(self, values: np.ndarray, lookback: int = 5) -> list:
        """找到局部高点"""
        peaks = []
        n = len(values)
        
        for i in range(lookback, n - lookback):
            # 检查是否是局部高点
            is_peak = True
            for j in range(max(0, i - lookback), min(n, i + lookback + 1)):
                if j != i and values[j] >= values[i]:
                    is_peak = False
                    break
            if is_peak:
                peaks.append(i)
        
        return peaks
    
    def _find_troughs(self, values: np.ndarray, lookback: int = 5) -> list:
        """找到局部低点"""
        troughs = []
        n = len(values)
        
        for i in range(lookback, n - lookback):
            # 检查是否是局部低点
            is_trough = True
            for j in range(max(0, i - lookback), min(n, i + lookback + 1)):
                if j != i and values[j] <= values[i]:
                    is_trough = False
                    break
            if is_trough:
                troughs.append(i)
        
        return troughs
    
    def calculate_confidence(self, df: pd.DataFrame, rsi: float, 
                           bb_position: float, ml_prediction: Dict = None) -> float:
        """计算置信度"""
        score = 0.0
        latest = df.iloc[-1]
        price = latest['close']
        
        # RSI 条件（与 STRATEGY.md 一致）
        if rsi < 30:
            score += 0.30  # 第一批/第二批都满足
        elif rsi < 35:
            score += 0.20  # 第一批满足
        elif rsi < 40:
            score += 0.10  # 关注区
        
        # RSI 极值加分
        if rsi < 20:
            score += 0.10
        
        # 布林带位置
        if bb_position < 0.15:
            score += 0.2
        elif bb_position < 0.25:
            score += 0.15
        elif bb_position < 0.35:
            score += 0.1
        
        # 价格接近下轨
        bb_lower = latest['bb_lower']
        if price < bb_lower * 1.02:  # 价格在布林带下轨附近
            score += 0.1
        
        # 24h 跌幅
        if len(df) >= 24:
            change24h = (price - df['close'].iloc[-24]) / df['close'].iloc[-24] * 100
            if change24h < -15:
                score += 0.15
            elif change24h < -10:
                score += 0.1
            elif change24h < -5:
                score += 0.05
        
        # ML 预测加成
        if ml_prediction and 'pullback_ended_prob' in ml_prediction:
            pullback_prob = ml_prediction['pullback_ended_prob']
            if pullback_prob > 0.6:
                score += 0.1
            elif pullback_prob > 0.5:
                score += 0.05
        
        return min(max(score, 0.1), 0.95)
    
    def generate_signal(self, df: pd.DataFrame, 
                       ml_prediction: Dict = None) -> RSISignal:
        """生成信号
        
        注意：batch 字段仅代表信号强度（满足哪一批的条件）
        实际是建仓还是补仓，由主程序根据持仓状态决定
        
        RSI背离说明:
        - 底背离 (bullish): 价格创新低但RSI没有创新低 → 买入信号增强
        - 顶背离 (bearish): 价格创新高但RSI没有创新高 → 卖出信号增强
        """
        # 确保有足够数据计算 RSI (Wilder's Smoothing 需要约 14+ 周期)
        if df is None or len(df) < 30:
            return RSISignal(
                action='hold',
                confidence=0,
                rsi=50,
                bb_position=0.5,
                stop_loss=0,
                take_profit=0,
                reasons=['数据不足'],
                divergence='none',
                divergence_strength=0.0
            )
        
        latest = df.iloc[-1]
        price = latest['close']
        
        # 计算指标
        rsi = self.calculate_rsi(df, 14).iloc[-1]
        df_bb = self.calculate_bb(df)
        bb_position = df_bb['bb_position'].iloc[-1]
        
        # [新增] RSI背离检测
        divergence, divergence_strength = self.detect_divergence(df, lookback=20)
        
        # 计算置信度
        confidence = self.calculate_confidence(df_bb, rsi, bb_position, ml_prediction)
        
        # [新增] 背离信号增强置信度
        if divergence == 'bullish' and divergence_strength > 0:
            # 底背离：买入信号增强
            confidence = min(confidence + divergence_strength * 0.25, 0.95)
        elif divergence == 'bearish' and divergence_strength > 0:
            # 顶背离：卖出信号增强
            confidence = min(confidence + divergence_strength * 0.25, 0.95)
        
        # 生成信号 - 分批建仓策略
        reasons = []
        action = 'hold'
        batch = 0  # 0=hold, 1=第一批条件, 2=第二批条件
        
        # [新增] 背离辅助决策
        # 底背离时，可以适当放宽买入条件
        relax_factor = 1.0
        if divergence == 'bullish' and divergence_strength > 0.3:
            relax_factor = 0.85  # 放宽15%的条件
            reasons.append(f'底背离信号增强: 强度={divergence_strength:.1%}')
        
        # 计算调整后的阈值
        rsi_threshold_2 = self.rsi_oversold_2 / relax_factor
        bb_threshold_2 = self.bb_position_buy_2 / relax_factor
        rsi_threshold_1 = self.rsi_oversold_1 / relax_factor
        bb_threshold_1 = self.bb_position_buy_1 / relax_factor
        
        # 信号强度分级（仅表示满足哪一批的条件）
        # 强买入信号：满足第二批条件 (RSI < 25 + BB < 20%)
        if rsi < rsi_threshold_2 and bb_position < bb_threshold_2:
            action = 'buy'
            batch = 2  # 强信号
            reasons.append(f'强超卖信号(Batch2条件): RSI={rsi:.1f}<{rsi_threshold_2:.1f}, BB={bb_position:.0%}<{bb_threshold_2:.0%}')
        
        # 普通买入信号：满足第一批条件 (RSI < 30 + BB < 30%)
        elif rsi < rsi_threshold_1 and bb_position < bb_threshold_1:
            action = 'buy'
            batch = 1  # 普通信号
            reasons.append(f'超卖信号(Batch1条件): RSI={rsi:.1f}<{rsi_threshold_1:.1f}, BB={bb_position:.0%}<{bb_threshold_1:.0%}')
        
        # [新增] 顶背离强制卖出
        if divergence == 'bearish' and divergence_strength > 0.4:
            # 强烈的顶背离信号，即使RSI未超买也考虑卖出
            action = 'sell'
            batch = 0
            reasons.append(f'顶背离警告: 强度={divergence_strength:.1%}, 价格可能反转')
        
        # 卖点条件
        elif rsi > self.rsi_overbought:
            action = 'sell'
            reasons.append(f'RSI超买: {rsi:.1f}>65')
        
        # 计算止损止盈
        if action == 'buy':
            stop_loss = price * (1 - self.stop_loss_pct)
            take_profit = price * (1 + self.take_profit_pct)
        else:
            stop_loss = 0
            take_profit = 0
        
        # 补充原因
        if action != 'hold':
            reasons.append(f'置信度: {confidence:.1%}')
        
        # [新增] 背离信息加入原因
        if divergence != 'none':
            reasons.append(f'RSI背离: {divergence} (强度: {divergence_strength:.1%})')
        
        return RSISignal(
            action=action,
            batch=batch,  # 仅表示信号强度，实际批次由主程序决定
            confidence=confidence,
            rsi=rsi,
            bb_position=bb_position,
            divergence=divergence,
            divergence_strength=divergence_strength,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasons=reasons
        )
    
    def calculate_position_size(self, signal: RSISignal, total_capital: float) -> float:
        """计算仓位大小 - 分批建仓"""
        if signal.action == 'hold':
            return 0
        
        # 分批建仓仓位 - 固定 $50 每批
        if signal.batch in [1, 2]:
            return self.batch_amount  # 每批$50
        else:
            return 0
    
    def should_close(self, df: pd.DataFrame, entry_price: float,
                    entry_time, current_time: pd.Timestamp,
                    highest_price: float) -> Tuple[bool, str]:
        """检查是否应该平仓"""
        if df is None or len(df) < 30:
            return False, ''
        
        latest = df.iloc[-1]
        current_price = latest['close']
        
        # [防御] 如果当前价格为 0 或 NaN，绝对不能平仓
        if current_price <= 0 or pd.isna(current_price):
            return False, ''
        
        current_time_ts = pd.Timestamp(current_time)
        
        rsi = self.calculate_rsi(df, 14).iloc[-1]
        
        # [调试] 打印内部计算
        import logging
        logger = logging.getLogger()
        logger.info(f"[should_close 内部] entry_price={entry_price}, current_price={current_price}, rsi={rsi}, highest_price={highest_price}")
        
        # 止损
        if current_price < entry_price * (1 - self.stop_loss_pct):
            return True, f'止损触发 (当前 {current_price:.4f} < 阈值 {entry_price * (1 - self.stop_loss_pct):.4f})'
        
        # 止盈
        if current_price >= entry_price * (1 + self.take_profit_pct):
            return True, f'止盈触发 (当前 {current_price:.4f} >= 阈值 {entry_price * (1 + self.take_profit_pct):.4f})'
        
        # ---------------------------------------------------------------------
        # [优化版] 分段移动止盈逻辑
        # ---------------------------------------------------------------------
        # 计算基于最高价的收益率
        roi_peak = (highest_price - entry_price) / entry_price
        
        # 阶段 1: 保护期 (最高收益 3% ~ 8%)
        # 逻辑: 既然已经涨了3%以上，就绝对不能亏着出去。
        # 动作: 价格一旦跌回 (入场价 + 0.2%)，也就是刚刚够手续费和微利，立即平仓。
        if 0.03 <= roi_peak < 0.08:
            break_even_price = entry_price * 1.002  # 保本线 (0.2% 利润) [2026-03-01 调整: 0.5%->0.2%]
            if current_price < break_even_price:
                return True, f'保本平仓 (最高浮盈 {roi_peak:.2%}, 回撤至保本线)'
        
        # 阶段 2: 冲刺期 (最高收益 > 8%)
        # 逻辑: 利润已经很厚了，允许回撤 3% 甚至 5% 来博取更大收益，但要锁定大部分利润。
        elif roi_peak >= 0.08:
            # 动态调整回撤比例：收益越高，允许的回撤可以稍微收紧一点，或者保持固定
            callback_rate = 0.03  # 回撤 3% 止盈 (比之前的 5% 更灵敏，锁利效果更好)
            trail_stop_price = highest_price * (1 - callback_rate)
            # 确保止盈线永远高于保本线 (双重保险)
            trail_stop_price = max(trail_stop_price, entry_price * 1.01)
            if current_price < trail_stop_price:
                return True, f'移动止盈 (最高浮盈 {roi_peak:.2%}, 回撤 {callback_rate:.1%})'
        # ---------------------------------------------------------------------
        
        # RSI 超买
        # [修复 2026-03-01] 只有 RSI > 75 或 RSI > 70 且盈利 > 8% 才平仓
        if rsi > 75:
            return True, f'RSI极度超买 ({rsi:.1f} > 75)'
        elif rsi > 70 and current_price > entry_price * 1.08:
            return True, f'RSI超买且盈利 ({rsi:.1f} > 70, 盈利>{8}%)'
        
        # 超时（7天）
        try:
            # entry_time 可能是字符串，转换为 Timestamp
            entry_time_ts = pd.Timestamp(entry_time)
            holding_hours = (current_time_ts - entry_time_ts).total_seconds() / 3600
            if holding_hours > self.max_holding_hours:
                return True, '超时强制平仓'
        except Exception as e:
            # 防止因时间格式问题导致程序崩溃
            pass
        
        return False, ''
    
    def get_strategy_params(self) -> Dict[str, Any]:
        """获取策略参数"""
        return {
            'name': self.name,
            'rsi_oversold_1': self.rsi_oversold_1,
            'rsi_oversold_2': self.rsi_oversold_2,
            'rsi_overbought': self.rsi_overbought,
            'bb_position_buy_1': self.bb_position_buy_1,
            'bb_position_buy_2': self.bb_position_buy_2,
            'stop_loss_pct': self.stop_loss_pct,
            'take_profit_pct': self.take_profit_pct,
            'max_positions': self.max_positions,
            'max_holding_hours': self.max_holding_hours,
        }


# ==================== 测试代码（已禁用）====================
# 注意：此测试代码引用了已移除的 data_collector 模块
# 如需测试，请手动运行或单独创建测试脚本
# if __name__ == "__main__":
#     from data_collector import DataCollector
#
#     collector = DataCollector()
#     strategy = RSIContrarianStrategy()
#
#     # 测试 BTC
#     df = collector.fetch_ohlcv('BTC/USDT', '1h', limit=100)
#
#     if df is not None:
#         signal = strategy.generate_signal(df)
#
#         print("RSI 反弹策略测试:")
#         print(f"  动作: {signal.action}")
#         print(f"  置信度: {signal.confidence:.1%}")
#         print(f"  RSI: {signal.rsi:.1f}")
#         print(f"  BB位置: {signal.bb_position:.2f}")
#         print(f"  入场价: ${df['close'].iloc[-1]:.2f}")
#         print(f"  止损: ${signal.stop_loss:.2f}")
#         print(f"  止盈: ${signal.take_profit:.2f}")
#         print(f"  原因: {signal.reasons}")
