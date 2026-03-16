#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋势跟踪策略回测
用法: python scripts/trend_backtest.py --symbol BTC/USDT --start 2024-01-01
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import pandas as pd
import numpy as np
from datetime import datetime
from okx_client import get_client
from strategies.trend_following import TrendFollowingStrategy


def backtest_trend(symbol, start_date, initial_capital=1000):
    """回测趋势跟踪策略"""
    client = get_client()
    strategy = TrendFollowingStrategy()
    
    # 获取数据
    ohlcv = client.fetch_ohlcv(symbol, timeframe='1h', limit=500)
    df = pd.DataFrame(ohlcv[::-1], columns=['ts', 'o', 'h', 'l', 'c', 'v'])
    df['close'] = df['c'].astype(float)
    df['high'] = df['h'].astype(float)
    df['low'] = df['l'].astype(float)
    
    # 过滤日期
    start = datetime.strptime(start_date, '%Y-%m-%d')
    df = df[df['ts'] >= start.timestamp()]
    
    # 模拟交易
    capital = initial_capital
    position = 0  # 持仓数量
    entry_price = 0
    trades = []
    equity_curve = []
    
    for i in range(60, len(df)-1):
        row = df.iloc[i]
        price = float(row['close'])
        
        # 生成信号
        signal = strategy.generate_signal(df[:i+1], price, entry_price if position > 0 else None)
        
        # 买入
        if signal.action == 'buy' and position == 0:
            position = capital / price
            entry_price = price
            capital = 0
            trades.append({
                'type': 'BUY',
                'price': price,
                'ts': row['ts'],
                'signal': signal.reason
            })
        
        # 卖出
        elif signal.action == 'sell' and position > 0:
            capital = position * price
            pnl = capital - initial_capital
            trades.append({
                'type': 'SELL',
                'price': price,
                'ts': row['ts'],
                'pnl': pnl,
                'return': (price / entry_price - 1) * 100,
                'signal': signal.reason
            })
            position = 0
            entry_price = 0
        
        # 记录权益
        equity = capital if capital > 0 else position * price
        equity_curve.append({'ts': row['ts'], 'equity': equity})
    
    # 平仓结算
    if position > 0:
        final_price = float(df.iloc[-1]['close'])
        capital = position * final_price
    
    # 计算指标
    total_return = (capital / initial_capital - 1) * 100
    
    closed_trades = [t for t in trades if t['type'] == 'SELL']
    wins = [t for t in closed_trades if t.get('pnl', 0) > 0]
    win_rate = len(wins) / max(len(closed_trades), 1) * 100
    
    # 最大回撤
    equity_values = [e['equity'] for e in equity_curve]
    max_equity = max(equity_values)
    drawdowns = [(max_equity - e) / max_equity * 100 for e in equity_values]
    max_drawdown = max(drawdowns)
    
    # 输出报告
    print(f"\n{'='*60}")
    print(f"趋势跟踪策略回测: {symbol}")
    print(f"{'='*60}")
    print(f"时间范围: {start_date} ~ {datetime.now().strftime('%Y-%m-%d')}")
    print(f"初始资金: ${initial_capital}")
    print(f"最终资金: ${capital:.2f}")
    print(f"总收益率: {total_return:.2f}%")
    print(f"交易次数: {len(closed_trades)}")
    print(f"胜率: {win_rate:.1f}%")
    print(f"最大回撤: {max_drawdown:.2f}%")
    
    # 交易记录
    print(f"\n交易记录:")
    for t in trades[-10:]:  # 显示最近10笔
        if t['type'] == 'BUY':
            print(f"  {t['type']} @ ${t['price']:.2f} ({t['signal'][:30]})")
        else:
            print(f"  {t['type']} @ ${t['price']:.2f} {t.get('return', 0):+.2f}% ({t['signal'][:30]})")
    
    return {
        'symbol': symbol,
        'return': total_return,
        'trades': len(closed_trades),
        'win_rate': win_rate,
        'max_drawdown': max_drawdown
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='BTC/USDT')
    parser.add_argument('--start', default='2024-01-01')
    args = parser.parse_args()
    
    backtest_trend(args.symbol, args.start)
