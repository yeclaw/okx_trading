#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网格挂单逻辑测试 - 验证核心挂单流程
"""

import unittest
import json
from unittest.mock import MagicMock, patch
import sys
sys.path.insert(0, '/root/clawd/okx_trading')
from core.grid import GridManager


class TestGridFillLogic(unittest.TestCase):
    """测试网格挂单核心逻辑"""
    
    def setUp(self):
        self.grid = GridManager(budget=50, data_dir='/root/clawd/okx_trading/data')
        self.grid.exchange = MagicMock()
        self.grid.markets = {'BNB/USDT': {'precision': {'amount': 6, 'price': 1}}}
        
        with open('/root/clawd/okx_trading/data/state.json') as f:
            state = json.load(f)
        self.grid.grid_state = {'BNB/USDT': state['grid']['BNB/USDT']}
    
    def test_done_true_no_filled_from_price(self):
        """
        测试: done=true + filled_from_price=none
        预期: 保持done=true，不翻转
        """
        print("\n[测试] done=true + filled_from_price=none")
        
        pending = self.grid.grid_state['BNB/USDT']['pending']
        
        # 621 done=true, 没有filled_from_price
        pending['621.6']['done'] = True
        pending['621.6']['filled_from_price'] = None
        pending['621.6']['side'] = 'buy'
        
        with patch.object(self.grid, 'save_state'):
            self.grid.sync_orders('BNB/USDT')
        
        # 验证: 621 应该保持done=true
        self.assertEqual(pending['621.6']['done'], True)
        print(f"✅ 621: done={pending['621.6']['done']} (保持不变)")
    
    def test_filled_from_price_with_source_done(self):
        """
        测试: done=true + filled_from_price=有 + 来源已成交
        预期: 翻转方向
        """
        print("\n[测试] done=true + filled_from_price=有 + 来源已成交")
        
        pending = self.grid.grid_state['BNB/USDT']['pending']
        
        # 设置: 609 done=true, 621 done=true, filled_from_price=609
        pending['609.7']['done'] = True
        pending['609.7']['filled_from_price'] = '633.6'
        pending['609.7']['side'] = 'buy'
        
        pending['621.6']['done'] = True
        pending['621.6']['filled_from_price'] = '609.7'
        pending['621.6']['side'] = 'buy'
        
        pending['633.6']['done'] = True
        pending['633.6']['filled_from_price'] = '621.6'
        pending['633.6']['side'] = 'sell'
        
        with patch.object(self.grid, 'save_state'):
            self.grid.sync_orders('BNB/USDT')
        
        # 验证: 621 应该翻转（来源609已成交）
        self.assertEqual(pending['621.6']['side'], 'sell')
        self.assertEqual(pending['621.6']['done'], False)
        print(f"✅ 621: side={pending['621.6']['side']}, done={pending['621.6']['done']}")
        
        # 验证: 633 也应该翻转（来源621已成交）
        self.assertEqual(pending['633.6']['side'], 'buy')
        self.assertEqual(pending['633.6']['done'], False)
        print(f"✅ 633: side={pending['633.6']['side']}, done={pending['633.6']['done']}")
    
    def test_update_grid_after_fill(self):
        """
        测试: update_grid_after_fill 挂单逻辑
        预期: 买单成交 -> 在更高价挂卖单
        """
        print("\n[测试] update_grid_after_fill 挂单")
        
        pending = self.grid.grid_state['BNB/USDT']['pending']
        
        # 609买单成交
        filled_orders = [{'price': 609.7, 'side': 'buy', 'price_str': '609.7', 'amount': 0.01}]
        
        with patch.object(self.grid, 'save_state'):
            self.grid.exchange.create_order.return_value = {'id': 'new_order_123'}
            self.grid.update_grid_after_fill('BNB/USDT', filled_orders)
        
        # 验证: 621.6 应该挂卖单
        self.assertEqual(pending['621.6']['side'], 'sell')
        self.assertEqual(pending['621.6']['filled_from_price'], '609.7')
        print(f"✅ 621: side={pending['621.6']['side']}, filled_from={pending['621.6']['filled_from_price']}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
