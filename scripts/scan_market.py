#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场 RSI 扫描脚本

使用机器人 config.py 中定义的 RSI_SYMBOLS，确保扫描结果与机器人一致。

使用方法：
    python3 scripts/scan_market.py          # 扫描所有币种
    python3 scripts/scan_market.py --top 5   # 只显示 Top 5
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from okx_client import get_client
from config import RSI_SYMBOLS
import pandas as pd
import time


def calculate_rsi(prices, period=14):
    """计算 RSI"""
    delta = prices.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]


def calculate_bb(prices, period=20, std_dev=2):
    """计算布林带"""
    middle = prices.rolling(window=period).mean()
    std = prices.rolling(window=period).std()
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    # BB position: 价格在布林带中的位置 (0% = 下轨, 100% = 上轨)
    bb_position = (prices.iloc[-1] - lower.iloc[-1]) / (upper.iloc[-1] - lower.iloc[-1])
    return {
        'middle': middle.iloc[-1],
        'upper': upper.iloc[-1],
        'lower': lower.iloc[-1],
        'position': bb_position * 100  # 转为百分比
    }


def scan_market(top_n=None):
    """扫描市场 RSI"""
    client = get_client()
    
    print('=' * 70)
    print(f'RSI 市场扫描 | {time.strftime("%H:%M")} | 币种数: {len(RSI_SYMBOLS)}')
    print('=' * 70)
    print(f'{"币种":<12} | {"价格":>12} | {"RSI":>6} | {"BB中":>8} | {"BB下":>8} | {"状态"}')
    print('-' * 70)
    
    results = []
    for symbol in RSI_SYMBOLS:
        try:
            ohlcv = client.fetch_ohlcv(symbol, limit=100)
            if not ohlcv or len(ohlcv) < 14:
                print(f'{symbol:<12} | {"无数据":>12}')
                continue
            
            df = pd.DataFrame(ohlcv[::-1], columns=['ts', 'o', 'h', 'l', 'c', 'v'])
            df['close'] = df['c'].astype(float)
            
            rsi = calculate_rsi(df['close'])
            bb = calculate_bb(df['close'])
            price = df['close'].iloc[-1]
            distance = rsi - 30
            
            results.append({'symbol': symbol, 'price': price, 'rsi': rsi, 'bb': bb, 'distance': distance})
            
            if rsi < 30:
                status = '🟢 买入!'
            elif rsi < 35:
                status = '🟡 接近'
            elif rsi < 40:
                status = '🟠 观察'
            else:
                status = '⚪ 中性'
            
            print(f'{symbol:<12} | ${price:>10,.2f} | {rsi:>5.1f} | {bb["position"]:>6.1f}% | {status}')
            
        except Exception as e:
            print(f'{symbol:<12} | 错误: {e}')
    
    print('=' * 70)
    
    # 排序并显示 Top N
    if results:
        results.sort(key=lambda x: x['rsi'])
        
        if top_n:
            results = results[:top_n]
        
        print()
        print('🎯 RSI 最低 TOP3:')
        for i, r in enumerate(results, 1):
            emoji = '🟢' if r['rsi'] < 30 else ('🟡' if r['rsi'] < 35 else '⚪')
            bb = r['bb']
            dist = r['rsi'] - 30  # 正数表示距离30的距离
            print(f'   {i}. {emoji} {r["symbol"]:<10} 价格: ${r["price"]:>12,.2f}  RSI: {r["rsi"]:>5.1f}  BB: {bb["position"]:>5.1f}%  距30: +{dist:.1f}')
    
    return results


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='市场 RSI 扫描')
    parser.add_argument('--top', type=int, help='只显示 Top N')
    
    args = parser.parse_args()
    scan_market(args.top)
