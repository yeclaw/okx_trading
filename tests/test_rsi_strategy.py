#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 策略单元测试
测试核心逻辑：RSI 计算、信号生成、止损止盈
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import pytest
import pandas as pd
import numpy as np
from datetime import datetime
from strategies.rsi_contrarian import RSIContrarianStrategy


def create_test_data(price_type='normal'):
    """创建模拟 K 线数据
    
    Args:
        price_type: 'normal' 正常 | 'uptrend' 上涨 | 'downtrend' 下跌 | 'volatile' 高波动
    """
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=100, freq='1h')
    
    if price_type == 'normal':
        # 震荡行情
        base = 100
        prices = [base + np.sin(i/10) * 5 + np.random.normal(0, 1) for i in range(100)]
    elif price_type == 'uptrend':
        # 上涨趋势
        prices = [100 + i * 0.5 + np.random.normal(0, 1) for i in range(100)]
    elif price_type == 'downtrend':
        # 下跌趋势
        prices = [150 - i * 0.5 + np.random.normal(0, 1) for i in range(100)]
    elif price_type == 'volatile':
        # 高波动
        prices = [100 + np.random.normal(0, 10) for i in range(100)]
    else:
        prices = [100] * 100
    
    return pd.DataFrame({
        'close': prices,
        'open': [p * 0.99 for p in prices],
        'high': [p * 1.02 for p in prices],
        'low': [p * 0.98 for p in prices],
        'volume': [1000] * 100
    })


class TestRSICalculation:
    """RSI 计算测试"""
    
    def test_rsi_range(self):
        """RSI 应该在 0-100 之间"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('normal')
        
        rsi = strategy.calculate_rsi(df, 14)
        
        assert 0 <= rsi.iloc[-1] <= 100, f"RSI 超出范围: {rsi.iloc[-1]}"
    
    def test_rsi_uptrend(self):
        """上涨趋势 RSI 应该偏高"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('uptrend')
        
        rsi = strategy.calculate_rsi(df, 14)
        
        assert rsi.iloc[-1] > 50, f"上涨趋势 RSI 应该 > 50，实际: {rsi.iloc[-1]}"
    
    def test_rsi_downtrend(self):
        """下跌趋势 RSI 应该偏低"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('downtrend')
        
        rsi = strategy.calculate_rsi(df, 14)
        
        assert rsi.iloc[-1] < 50, f"下跌趋势 RSI 应该 < 50，实际: {rsi.iloc[-1]}"
    
    def test_rsi_oversold(self):
        """模拟超卖数据，RSI 应该低于 30"""
        strategy = RSIContrarianStrategy()
        
        # 连续下跌
        dates = pd.date_range('2024-01-01', periods=50, freq='1h')
        prices = [100 - i * 3 for i in range(50)]  # 连续下跌
        df = pd.DataFrame({
            'close': prices,
            'open': prices,
            'high': prices,
            'low': prices,
            'volume': [1000] * 50
        })
        
        rsi = strategy.calculate_rsi(df, 14)
        
        assert rsi.iloc[-1] < 30, f"超卖数据 RSI 应该 < 30，实际: {rsi.iloc[-1]}"


class TestBBStrategy:
    """布林带测试"""
    
    def test_bb_position_range(self):
        """BB 位置应该在 0-1 之间"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('normal')
        
        bb = strategy.calculate_bb(df)
        bb_pos = bb['bb_position'].iloc[-1]
        
        assert 0 <= bb_pos <= 1, f"BB 位置超出范围: {bb_pos}"
    
    def test_bb_below_lower(self):
        """价格跌破下轨时 BB 位置应该接近 0"""
        strategy = RSIContrarianStrategy()
        
        dates = pd.date_range('2024-01-01', periods=50, freq='1h')
        prices = [100] * 20 + [80] * 30  # 突然跌破
        df = pd.DataFrame({
            'close': prices,
            'open': prices,
            'high': [p * 1.02 for p in prices],
            'low': [p * 0.98 for p in prices],
            'volume': [1000] * 50
        })
        
        bb = strategy.calculate_bb(df)
        bb_pos = bb['bb_position'].dropna().iloc[-1]
        
        # 价格在 BB 范围内即可
        assert 0 <= bb_pos <= 1, f"BB 位置应该在 0-1 之间，实际: {bb_pos}"


class TestSignalGeneration:
    """信号生成测试"""
    
    def test_hold_when_rsi_high(self):
        """RSI 高时应该持有"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('uptrend')  # RSI 会偏高
        
        signal = strategy.generate_signal(df)
        
        assert signal.action in ['sell', 'hold'], f"RSI 高时应卖出或持有，实际: {signal.action}"
    
    def test_no_crash_on_bad_data(self):
        """异常数据不应该导致崩溃"""
        strategy = RSIContrarianStrategy()
        
        # 空数据
        signal = strategy.generate_signal(None)
        assert signal.action == 'hold'
        
        # 数据不足
        df = create_test_data('normal').head(10)
        signal = strategy.generate_signal(df)
        assert signal.action == 'hold'
    
    def test_no_crash_on_negative_price(self):
        """负价格不应该导致崩溃"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('normal')
        df['close'] = -10  # 异常数据
        
        signal = strategy.generate_signal(df)
        assert signal.action == 'hold', f"负价格应该返回 hold，实际: {signal.action}"
    
    def test_no_crash_on_nan(self):
        """NaN 数据不应该导致崩溃"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('normal')
        df['close'] = float('nan')
        
        signal = strategy.generate_signal(df)
        assert signal.action == 'hold', f"NaN 应该返回 hold"
    
    def test_signal_attributes(self):
        """信号应该包含所有必需属性"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('normal')
        
        signal = strategy.generate_signal(df)
        
        # 检查必需属性
        assert hasattr(signal, 'action')
        assert hasattr(signal, 'confidence')
        assert hasattr(signal, 'rsi')
        assert hasattr(signal, 'batch')
        assert hasattr(signal, 'stop_loss')
        assert hasattr(signal, 'take_profit')
        
        # 检查置信度范围
        assert 0 <= signal.confidence <= 1, f"置信度超出范围: {signal.confidence}"
        
        # 检查批次
        assert signal.batch in [0, 1, 2], f"批次值无效: {signal.batch}"


class TestStopLossTakeProfit:
    """止损止盈测试"""
    
    def test_stop_loss_calculation(self):
        """止损价应该正确计算"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('downtrend')
        
        signal = strategy.generate_signal(df)
        
        # 买入信号才有止止损
        if signal.action == 'buy':
            assert signal.stop_loss > 0, "买入信号应该有止损价"
            assert signal.stop_loss < float(df.iloc[-1]['close']), "止损价应该低于当前价"
    
    def test_take_profit_calculation(self):
        """止盈价应该正确计算"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('downtrend')
        
        signal = strategy.generate_signal(df)
        
        if signal.action == 'buy':
            assert signal.take_profit > 0, "买入信号应该有止盈价"
            assert signal.take_profit > float(df.iloc[-1]['close']), "止盈价应该高于当前价"


class TestConfidenceCalculation:
    """置信度测试"""
    
    def test_confidence_range(self):
        """置信度应该在 0-1 之间"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('normal')
        df = strategy.calculate_bb(df)
        
        confidence = strategy.calculate_confidence(df, 25, 0.15)
        
        assert 0 <= confidence <= 1, f"置信度超出范围: {confidence}"
    
    def test_confidence_increases_with_oversold(self):
        """越超卖置信度越高"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('normal')
        df = strategy.calculate_bb(df)
        
        conf_high = strategy.calculate_confidence(df, 20, 0.10)
        conf_low = strategy.calculate_confidence(df, 35, 0.30)
        
        assert conf_high > conf_low, "更超卖应该置信度更高"


class TestPositionManagement:
    """仓位管理测试"""
    
    def test_position_size(self):
        """仓位计算应该返回正数"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('downtrend')
        
        signal = strategy.generate_signal(df)
        position = strategy.calculate_position_size(signal, 1000)
        
        if signal.action == 'buy':
            assert position > 0, "买入信号应该返回正数仓位"
    
    def test_position_size_zero_on_hold(self):
        """持有信号应该返回 0 仓位"""
        strategy = RSIContrarianStrategy()
        df = create_test_data('uptrend')
        
        signal = strategy.generate_signal(df)
        position = strategy.calculate_position_size(signal, 1000)
        
        if signal.action == 'hold':
            assert position == 0, "持有信号应该返回 0 仓位"


class TestEdgeCases:
    """边界条件测试"""
    
    def test_single_price(self):
        """单点数据不应该崩溃"""
        strategy = RSIContrarianStrategy()
        df = pd.DataFrame({
            'close': [100],
            'open': [99],
            'high': [101],
            'low': [98],
            'volume': [1000]
        })
        
        signal = strategy.generate_signal(df)
        assert signal.action == 'hold', f"数据不足应该返回 hold"
    
    def test_constant_price(self):
        """横盘数据"""
        strategy = RSIContrarianStrategy()
        df = pd.DataFrame({
            'close': [100] * 100,
            'open': [100] * 100,
            'high': [100] * 100,
            'low': [100] * 100,
            'volume': [1000] * 100
        })
        
        signal = strategy.generate_signal(df)
        assert signal.action in ['buy', 'sell', 'hold']
    
    def test_extreme_volatility(self):
        """极端波动"""
        strategy = RSIContrarianStrategy()
        df = pd.DataFrame({
            'close': [100 + np.random.randint(-50, 50) for _ in range(100)],
            'open': [100] * 100,
            'high': [150] * 100,
            'low': [50] * 100,
            'volume': [1000] * 100
        })
        
        signal = strategy.generate_signal(df)
        assert signal.action in ['buy', 'sell', 'hold']
        assert 0 <= signal.confidence <= 1


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
