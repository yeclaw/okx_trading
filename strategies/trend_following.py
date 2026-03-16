#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋势跟踪策略
================
基于 MA 交叉 + ADX 趋势强度

特点：
1. 使用 MA20/MA50 判断趋势方向
2. ADX > 25 确认趋势强度
3. 金叉买入，死叉卖出
4. 趋势强度决定仓位大小

买入条件：
- MA20 > MA50（多头趋势）
- ADX > 25（趋势明确）
- 价格回踩 MA20 买入

卖出条件：
- MA20 < MA50（趋势反转）
- ADX < 20（趋势变弱）
- 止损 -10%
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, Any, Tuple


@dataclass
class TrendSignal:
    """趋势信号"""
    action: str  # 'buy', 'sell', 'hold'
    confidence: float  # 0-1
    trend: str  # 'bull', 'bear', 'neutral'
    adx: float
    ma20: float
    ma50: float
    reason: str


class TrendFollowingStrategy:
    """趋势跟踪策略"""
    
    def __init__(self, capital=1000):
        self.name = "TrendFollowing"
        self.capital = capital
        
        # 参数
        self.ma_short = 20   # 短期均线
        self.ma_long = 50    # 长期均线
        self.adx_period = 14  # ADX 周期
        self.adx_threshold = 25  # ADX 阈值
        self.stop_loss_pct = 0.10  # 止损 10%
        self.take_profit_pct = 0.20  # 止盈 20%
        
        # 仓位管理
        self.max_position_pct = 0.20  # 单币最大 20% 仓位
        self.trend_confidence = 0.0  # 趋势强度
    
    def calculate_ma(self, df: pd.DataFrame, period: int) -> pd.Series:
        """计算移动平均线"""
        return df['close'].rolling(period).mean()
    
    def calculate_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """计算 ADX 趋势强度指标"""
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)
        
        # +DM 和 -DM
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        # TR（真实波幅）
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR
        atr = tr.rolling(period).mean()
        
        # +DI 和 -DI
        plus_di = (plus_dm.rolling(period).mean() / atr) * 100
        minus_di = (minus_dm.rolling(period).mean() / atr) * 100
        
        # DX
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        
        # ADX
        adx = dx.rolling(period).mean()
        
        return adx
    
    def calculate_trend_strength(self, df: pd.DataFrame) -> Dict:
        """计算趋势强度"""
        ma20 = self.calculate_ma(df, self.ma_short).iloc[-1]
        ma50 = self.calculate_ma(df, self.ma_long).iloc[-1]
        adx = self.calculate_adx(df, self.adx_period).iloc[-1]
        
        # 计算趋势方向
        if ma20 > ma50:
            trend = 'bull'
            trend_strength = min((ma20 - ma50) / ma50 * 100, 10)  # 最大 10 分
        elif ma20 < ma50:
            trend = 'bear'
            trend_strength = min((ma50 - ma20) / ma50 * 100, 10)
        else:
            trend = 'neutral'
            trend_strength = 0
        
        # ADX 加分
        if adx > self.adx_threshold:
            adx_score = min((adx - 25) / 10, 5)  # 最大 5 分
        elif adx < 15:
            adx_score = -2
        else:
            adx_score = 0
        
        total_strength = trend_strength + adx_score
        
        return {
            'trend': trend,
            'ma20': ma20,
            'ma50': ma50,
            'adx': adx,
            'strength': total_strength,
            'ma_distance_pct': (ma20 - ma50) / ma50 * 100 if ma50 else 0
        }
    
    def generate_signal(self, df: pd.DataFrame, 
                       current_price: float = None,
                       entry_price: float = None) -> TrendSignal:
        """生成交易信号
        
        Args:
            df: K 线数据
            current_price: 当前价格（可选）
            entry_price: 入场价格（持仓时检查）
        """
        if df is None or len(df) < 60:
            return TrendSignal(
                action='hold',
                confidence=0,
                trend='neutral',
                adx=0,
                ma20=0,
                ma50=0,
                reason='数据不足'
            )
        
        latest = df.iloc[-1]
        current_price = current_price or float(latest['close'])
        
        # 计算指标
        trend_data = self.calculate_trend_strength(df)
        
        ma20 = trend_data['ma20']
        ma50 = trend_data['ma50']
        adx = trend_data['adx']
        trend = trend_data['trend']
        strength = trend_data['strength']
        
        reasons = []
        
        # 买入信号
        if trend == 'bull':
            # 条件 1: 多头趋势确认
            if adx >= self.adx_threshold:
                reasons.append(f'多头趋势(ADX={adx:.1f}>25)')
                
                # 条件 2: 价格回踩 MA20 或突破
                price_vs_ma20 = (current_price - ma20) / ma20 * 100
                
                if -2 <= price_vs_ma20 <= 5:
                    # 价格在 MA20 附近（回踩或微突破）
                    confidence = 0.5 + min(strength / 20, 0.3) + min(adx / 50, 0.2)
                    
                    return TrendSignal(
                        action='buy',
                        confidence=min(confidence, 0.95),
                        trend=trend,
                        adx=adx,
                        ma20=ma20,
                        ma50=ma50,
                        reason=f'趋势多头+ADX={adx:.1f}+回踩MA20({price_vs_ma20:+.1f}%)'
                    )
                elif price_vs_ma20 > 5:
                    # 已经突破 MA20，追涨风险较高
                    return TrendSignal(
                        action='hold',
                        confidence=0.3,
                        trend=trend,
                        adx=adx,
                        ma20=ma20,
                        ma50=ma50,
                        reason=f'已突破MA20({price_vs_ma20:+.1f}%), 等待回踩'
                    )
            else:
                return TrendSignal(
                    action='hold',
                    confidence=0.2,
                    trend=trend,
                    adx=adx,
                    ma20=ma20,
                    ma50=ma50,
                    reason=f'趋势形成中(ADX={adx:.1f}<25)'
                )
        
        # 卖出信号（持仓检查）
        elif entry_price and entry_price > 0:
            # 条件 1: 趋势反转
            if trend == 'bear':
                reasons.append('趋势反转(死叉)')
                return TrendSignal(
                    action='sell',
                    confidence=0.7,
                    trend=trend,
                    adx=adx,
                    ma20=ma20,
                    ma50=ma50,
                    reason='MA20下穿MA50，死叉'
                )
            
            # 条件 2: 止损
            if current_price <= entry_price * (1 - self.stop_loss_pct):
                return TrendSignal(
                    action='sell',
                    confidence=1.0,
                    trend=trend,
                    adx=adx,
                    ma20=ma20,
                    ma50=ma50,
                    reason=f'止损触发({(current_price/entry_price-1)*100:.1f}%)'
                )
            
            # 条件 3: 止盈（趋势减弱）
            if adx < 15:
                return TrendSignal(
                    action='sell',
                    confidence=0.5,
                    trend=trend,
                    adx=adx,
                    ma20=ma20,
                    ma50=ma50,
                    reason=f'趋势减弱(ADX={adx:.1f}<15)'
                )
        
        # 持有或观望
        return TrendSignal(
            action='hold',
            confidence=max(0.3 - strength / 20, 0.1),
            trend=trend,
            adx=adx,
            ma20=ma20,
            ma50=ma50,
            reason='无交易信号'
        )
    
    def calculate_position_size(self, signal: TrendSignal, 
                               total_capital: float,
                               atr_pct: float = 0.03) -> float:
        """计算仓位大小
        
        Args:
            signal: 交易信号
            total_capital: 总资金
            atr_pct: ATR 百分比（波动率）
        """
        if signal.action != 'buy':
            return 0
        
        # 趋势强度决定仓位
        base_position = min(signal.confidence, 0.8)
        
        # 波动率调整
        if atr_pct > 0.05:  # 高波动
            position = base_position * 0.5
        elif atr_pct < 0.02:  # 低波动
            position = base_position * 1.2
        else:
            position = base_position
        
        # 最大仓位限制
        position = min(position, self.max_position_pct)
        
        # 转换为金额
        position_value = total_capital * position
        
        return position_value
    
    def get_params(self) -> Dict[str, Any]:
        """获取策略参数"""
        return {
            'name': self.name,
            'ma_short': self.ma_short,
            'ma_long': self.ma_long,
            'adx_period': self.adx_period,
            'adx_threshold': self.adx_threshold,
            'stop_loss_pct': self.stop_loss_pct,
            'take_profit_pct': self.take_profit_pct,
            'max_position_pct': self.max_position_pct
        }


# ==================== 趋势策略测试 ====================
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/root/clawd/okx_trading')
    
    from okx_client import get_client
    
    client = get_client()
    strategy = TrendFollowingStrategy()
    
    # 获取数据
    symbol = 'BTC/USDT'
    ohlcv = client.fetch_ohlcv(symbol, timeframe='1h', limit=100)
    df = pd.DataFrame(ohlcv[::-1], columns=['ts', 'o', 'h', 'l', 'c', 'v'])
    df['close'] = df['c'].astype(float)
    df['high'] = df['h'].astype(float)
    df['low'] = df['l'].astype(float)
    
    # 生成信号
    signal = strategy.generate_signal(df)
    
    print("="*60)
    print(f"趋势跟踪策略: {symbol}")
    print("="*60)
    print(f"动作: {signal.action.upper()}")
    print(f"置信度: {signal.confidence:.1%}")
    print(f"趋势: {signal.trend}")
    print(f"ADX: {signal.adx:.1f}")
    print(f"MA20: ${signal.ma20:.2f}")
    print(f"MA50: ${signal.ma50:.2f}")
    print(f"原因: {signal.reason}")
    print("="*60)
    
    # 趋势状态
    trend_data = strategy.calculate_trend_strength(df)
    print(f"\n趋势分析:")
    print(f"  趋势方向: {trend_data['trend']}")
    print(f"  趋势强度: {trend_data['strength']:.1f}")
    print(f"  MA距离: {trend_data['ma_distance_pct']:+.2f}%")
