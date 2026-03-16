#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
趋势跟踪机器人
================
基于 MA 交叉 + ADX 趋势强度
"""

import sys
import os

# 添加路径
sys.path.insert(0, '/root/clawd/okx_trading')

import json
import time
import logging
from datetime import datetime
from typing import Dict

# 配置日志
from config_secrets import OKX_CONFIG, EMAIL_CONFIG

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/trend.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 导入策略和配置
from okx_client import get_client, OKXConfig
from strategies.trend_following import TrendFollowingStrategy
from robots.trend_following.config import STRATEGY_CONFIG, SYMBOLS


class TrendBot:
    """趋势跟踪机器人"""
    
    def __init__(self):
        # 初始化 API（使用 secrets）
        config = OKXConfig(
            api_key=OKX_CONFIG['api_key'],
            api_secret=OKX_CONFIG['api_secret'],
            passphrase=OKX_CONFIG['passphrase'],
            proxy=OKX_CONFIG.get('proxies'),
        )
        self.client = get_client(config)
        
        # 初始化策略（使用策略配置）
        self.strategy = TrendFollowingStrategy()
        self.strategy.ma_short = STRATEGY_CONFIG['ma_short']
        self.strategy.ma_long = STRATEGY_CONFIG['ma_long']
        self.strategy.adx_threshold = STRATEGY_CONFIG['adx_threshold']
        self.strategy.stop_loss_pct = STRATEGY_CONFIG['stop_loss_pct']
        
        # 配置
        self.symbols = SYMBOLS
        self.check_interval = STRATEGY_CONFIG['check_interval']
        
        # 状态
        self.positions = {}
        
        logger.info("趋势跟踪机器人初始化完成")
    
    def sync_balance(self):
        """同步余额"""
        try:
            balance = self.client.fetch_balance()
            if balance.get('code') == '0':
                data = balance['data'][0]
                usdt = float(data.get('totalEq', 0))
                logger.info(f"余额: ${usdt:.2f}")
                return usdt
        except Exception as e:
            logger.error(f"余额同步失败: {e}")
        return 0
    
    def fetch_klines(self, symbol, limit=100):
        """获取 K 线"""
        try:
            ohlcv = self.client.fetch_ohlcv(symbol, timeframe='1h', limit=limit)
            if not ohlcv or len(ohlcv) < 60:
                return None
            return self._ohlcv_to_df(ohlcv)
        except Exception as e:
            logger.error(f"获取 K 线失败 {symbol}: {e}")
            return None
    
    def _ohlcv_to_df(self, ohlcv):
        """OHLCV 转 DataFrame"""
        import pandas as pd
        df = pd.DataFrame(ohlcv[::-1], columns=['ts', 'o', 'h', 'l', 'c', 'v'])
        df['close'] = df['c'].astype(float)
        df['high'] = df['h'].astype(float)
        df['low'] = df['low'].astype(float)
        return df
    
    def execute_buy(self, symbol, df, price):
        """执行买入"""
        try:
            if symbol in self.positions:
                return
            
            balance = self.sync_balance()
            signal = self.strategy.generate_signal(df, price)
            position_size = self.strategy.calculate_position_size(signal, balance)
            
            if position_size <= 0:
                return
            
            amount = position_size / price
            
            logger.info(f"买入 {symbol}: ${position_size} @ ${price}")
            
            result = self.client.create_order(
                symbol, 'buy', amount, 'limit', price
            )
            
            if result.get('code') == '0':
                self.positions[symbol] = {
                    'entry': price,
                    'amount': amount,
                    'time': datetime.now().isoformat()
                }
                logger.info(f"买入成功: {symbol}")
            else:
                logger.error(f"买入失败: {result}")
                
        except Exception as e:
            logger.error(f"买入异常 {symbol}: {e}")
    
    def execute_sell(self, symbol, df, price):
        """执行卖出"""
        try:
            if symbol not in self.positions:
                return
            
            pos = self.positions[symbol]
            amount = pos['amount']
            
            logger.info(f"卖出 {symbol}: {amount} @ ${price}")
            
            result = self.client.create_order(
                symbol, 'sell', amount, 'limit', price
            )
            
            if result.get('code') == '0':
                pnl = (price - pos['entry']) / pos['entry'] * 100
                logger.info(f"卖出成功: {symbol} PnL: {pnl:.2f}%")
                del self.positions[symbol]
            else:
                logger.error(f"卖出失败: {result}")
                
        except Exception as e:
            logger.error(f"卖出异常 {symbol}: {e}")
    
    def check_position(self, symbol, df, price):
        """检查持仓"""
        if symbol not in self.positions:
            return
        
        pos = self.positions[symbol]
        entry = pos['entry']
        
        # 止损
        if price <= entry * (1 - self.strategy.stop_loss_pct):
            logger.warning(f"{symbol}: 触发止损 ${price}")
            self.execute_sell(symbol, df, price)
            return
        
        # 趋势检查
        signal = self.strategy.generate_signal(df, price, entry)
        
        if signal.action == 'sell':
            logger.info(f"{symbol}: 触发卖出 ({signal.reason})")
            self.execute_sell(symbol, df, price)
    
    def scan(self):
        """扫描"""
        logger.info(f"开始扫描 {len(self.symbols)} 个币种...")
        
        for symbol in self.symbols:
            try:
                df = self.fetch_klines(symbol)
                if df is None:
                    continue
                
                price = float(df.iloc[-1]['close'])
                
                # 检查持仓
                if symbol in self.positions:
                    self.check_position(symbol, df, price)
                    continue
                
                # 生成信号
                signal = self.strategy.generate_signal(df, price)
                
                if signal.action == 'buy':
                    trend_emoji = '🐂' if signal.trend == 'bull' else '🐻'
                    logger.info(f"信号: {symbol} {signal.action} {trend_emoji} "
                              f"MA20={signal.ma20:.0f} MA50={signal.ma50:.0f} "
                              f"ADX={signal.adx:.1f} 置信度={signal.confidence:.0f}%")
                    self.execute_buy(symbol, df, price)
                
            except Exception as e:
                logger.error(f"扫描异常 {symbol}: {e}")
    
    def run(self):
        """运行"""
        logger.info("="*60)
        logger.info("趋势跟踪机器人启动")
        logger.info("="*60)
        
        while True:
            self.scan()
            time.sleep(self.check_interval)


def main():
    try:
        bot = TrendBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("手动停止")
    except Exception as e:
        logger.error(f"崩溃: {e}", exc_info=True)


if __name__ == "__main__":
    main()
