#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据记录器 (DataLogger)
==================
职责：只负责写入日志，与计算逻辑完全解耦

原则：
- 只写不读
- 不参与任何计算
- 实时数据来自交易所 API，不从历史记录读取
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional


class DataLogger:
    """只负责写入，不做计算"""
    
    def __init__(self, log_dir: str = 'data/logs'):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        
        # 记录器开关（生产环境可关闭以节省 IO）
        self.enabled = True
    
    def log_opportunity(self, symbol: str, signal_data: Dict):
        """记录候选机会（不影响决策，仅用于回测分析）"""
        if not self.enabled:
            return
            
        record = {
            'timestamp': datetime.now().isoformat(),
            'type': 'opportunity',
            'symbol': symbol,
            'action': signal_data.get('action', ''),
            'batch': signal_data.get('batch', 1),
            'confidence': signal_data.get('confidence', 0),
            'price': signal_data.get('price', 0),
            'rsi': signal_data.get('rsi', None),
            'bb_position': signal_data.get('bb_position', None),
            'signal_ts': signal_data.get('signal_ts', None),
        }
        self._write('opportunities.jsonl', record)
    
    def log_buy(self, symbol: str, price: float, amount: float, 
                batch: int = 1, entry_price: float = None, 
                confidence: float = None, reason: str = ''):
        """记录买入（开仓）"""
        if not self.enabled:
            return
            
        record = {
            'timestamp': datetime.now().isoformat(),
            'type': 'buy',
            'symbol': symbol,
            'side': 'buy',
            'price': price,
            'amount': amount,
            'batch': batch,
            'entry_price': entry_price or price,
            'confidence': confidence,
            'reason': reason,
        }
        self._write('trades.jsonl', record)
    
    def log_sell(self, symbol: str, price: float, amount: float,
                 pnl_pct: float = None, pnl_usd: float = None,
                 holding_seconds: int = None, reason: str = ''):
        """记录卖出（平仓）"""
        if not self.enabled:
            return
            
        record = {
            'timestamp': datetime.now().isoformat(),
            'type': 'sell',
            'symbol': symbol,
            'side': 'sell',
            'price': price,
            'amount': amount,
            'pnl_pct': pnl_pct,
            'pnl_usd': pnl_usd,
            'holding_seconds': holding_seconds,
            'reason': reason,
        }
        self._write('trades.jsonl', record)
    
    def log_grid_buy(self, symbol: str, price: float, amount: float,
                     grid_id: str = None, reason: str = ''):
        """记录网格买入"""
        if not self.enabled:
            return
            
        record = {
            'timestamp': datetime.now().isoformat(),
            'type': 'grid_buy',
            'symbol': symbol,
            'price': price,
            'amount': amount,
            'grid_id': grid_id,
            'reason': reason,
        }
        self._write('grid_trades.jsonl', record)
    
    def log_grid_sell(self, symbol: str, price: float, amount: float,
                       grid_id: str = None, pnl_pct: float = None, reason: str = ''):
        """记录网格卖出"""
        if not self.enabled:
            return
            
        record = {
            'timestamp': datetime.now().isoformat(),
            'type': 'grid_sell',
            'symbol': symbol,
            'price': price,
            'amount': amount,
            'grid_id': grid_id,
            'pnl_pct': pnl_pct,
            'reason': reason,
        }
        self._write('grid_trades.jsonl', record)
    
    def log_error(self, component: str, error_msg: str, error_details: str = ''):
        """记录错误"""
        if not self.enabled:
            return
            
        record = {
            'timestamp': datetime.now().isoformat(),
            'type': 'error',
            'component': component,
            'message': error_msg,
            'details': error_details,
        }
        self._write('errors.jsonl', record)
    
    def log_reconciliation(self, symbol: str, action: str, details: Dict):
        """记录对账结果"""
        if not self.enabled:
            return
            
        record = {
            'timestamp': datetime.now().isoformat(),
            'type': 'reconciliation',
            'symbol': symbol,
            'action': action,
            **details
        }
        self._write('reconciliation.jsonl', record)
    
    def _write(self, filename: str, record: Dict):
        """追加写入"""
        path = os.path.join(self.log_dir, filename)
        
        try:
            # 直接追加到目标文件
            with open(path, 'a', encoding='utf-8') as f:
                json.dump(record, f, ensure_ascii=False, default=str)
                f.write('\n')
        except Exception as e:
            # 静默失败，不影响主交易逻辑
            pass
    
    def get_stats(self) -> Dict:
        """获取记录统计（不读取历史，只看文件存在性）"""
        stats = {
            'opportunities': 0,
            'trades': 0,
            'grid_trades': 0,
            'errors': 0,
        }
        
        for key in stats.keys():
            path = os.path.join(self.log_dir, f"{key.replace('_trades', '')}.jsonl")
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        stats[key] = sum(1 for _ in f)
                except:
                    pass
        
        return stats
