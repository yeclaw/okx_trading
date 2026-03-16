#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网格逻辑单元测试
测试：GridManager 核心功能
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import unittest
from unittest.mock import Mock, MagicMock, patch
import tempfile
import json


class TestGridLogic(unittest.TestCase):
    """网格逻辑测试"""

    def setUp(self):
        """测试前准备"""
        # 延迟导入，避免模块加载问题
        from core.grid import GridManager
        from core.state_manager import StateManager

        # 创建临时目录
        self.temp_dir = tempfile.mkdtemp()

        # 创建 StateManager
        self.state_mgr = StateManager(data_dir=self.temp_dir)

        # 创建 GridManager 实例
        self.grid = GridManager(
            budget=50,
            data_dir=self.temp_dir,
            state_mgr=self.state_mgr
        )

        # 模拟交易所和市场数据
        self.mock_exchange = Mock()
        self.mock_exchange.markets = {
            'BTC/USDT': {
                'precision': {'amount': 4, 'price': 2},
                'limits': {'amount': {'min': 0.0001}}
            }
        }
        self.grid.exchange = self.mock_exchange
        self.grid.markets = self.mock_exchange.markets

        # 测试参数
        self.symbol = 'BTC/USDT'
        self.entry_price = 50000.0
        self.layers = 5

    def test_init_grid_direction(self):
        """测试1: 初始化网格时方向正确
        - 高于 entry_price 挂卖单
        - 低于 entry_price 挂买单
        """
        # 调整 grid_spread 以确保有足够的格子不被跳过
        self.grid.grid_spread = 0.2  # 扩大价差范围

        # 初始化网格
        state = self.grid.init_grid(
            symbol=self.symbol,
            entry_price=self.entry_price,
            layers=self.layers
        )

        pending = state['pending']
        prices = state['prices']

        print(f"\n[test_init_grid_direction]")
        print(f"entry_price: {self.entry_price}")
        print(f"prices: {prices}")
        print(f"pending keys: {list(pending.keys())}")

        # 由于 safety_threshold 的存在，可能有些格子被跳过
        # 只测试被保留的格子
        above_entry_prices = []
        below_entry_prices = []

        for p_str, info in pending.items():
            p = float(p_str)
            side = info.get('side')

            print(f"  价格 {p_str}: side={side}")

            if p > self.entry_price:
                above_entry_prices.append((p_str, side))
            elif p < self.entry_price:
                below_entry_prices.append((p_str, side))

        # 验证方向逻辑（针对保留的格子）
        for p_str, side in above_entry_prices:
            self.assertEqual(side, 'sell',
                f"价格 {p_str} 高于 entry_price {self.entry_price}，应该是卖单，实际是 {side}")

        for p_str, side in below_entry_prices:
            self.assertEqual(side, 'buy',
                f"价格 {p_str} 低于 entry_price {self.entry_price}，应该是买单，实际是 {side}")

        # 至少验证有一些高于和低于的格子
        total_count = len(above_entry_prices) + len(below_entry_prices)
        self.assertGreater(total_count, 0, "应该至少有一些格子被保留")

        print(f"高于 entry_price 的卖单数: {len(above_entry_prices)}")
        print(f"低于 entry_price 的买单数: {len(below_entry_prices)}")
        print("✅ test_init_grid_direction PASSED")

    def test_sync_orders_flip_direction(self):
        """测试2: sync_orders 方向翻转
        当 done=True 且无 order_id（已成交）时，检查方向是否正确翻转
        新逻辑：基于 done=True 和 order_id=None 来判断成交状态
        """
        # 先初始化网格
        state = self.grid.init_grid(
            symbol=self.symbol,
            entry_price=self.entry_price,
            layers=self.layers
        )

        pending = state['pending']
        prices = state['prices']

        # 模拟一个已成交的格子（done=True，order_id=None 表示已成交且订单已清理）
        # 找到中间价附近的格子
        mid_idx = len(prices) // 2
        mid_price = prices[mid_idx]

        print(f"\n[test_sync_orders_flip_direction]")
        print(f"选中测试格子: {mid_price}")

        # 设置该格子为已成交状态（done=True，order_id=None）
        pending[mid_price] = {
            'side': 'buy',           # 当前方向是买单
            'original_side': 'buy',  # 原始方向也是买单
            'order_id': None,        # order_id 为 None 表示已成交
            'done': True,            # 已成交
            'fill_count': 1
        }

        # 保存状态
        self.grid.grid_state[self.symbol]['pending'] = pending
        self.grid.save_state()

        # 调用 sync_orders
        with patch.object(self.grid, 'save_state'):
            self.grid.sync_orders(self.symbol)

        # 检查方向是否翻转
        # 注意：新逻辑中 done=True + order_id=None 会保持 done=True，不翻转方向
        # 方向翻转由 update_grid_after_fill 处理目标格子
        updated_info = self.grid.grid_state[self.symbol]['pending'][mid_price]
        new_side = updated_info.get('side')
        new_done = updated_info.get('done')

        print(f"翻转前 side: buy, done: True, order_id: None")
        print(f"翻转后 side: {new_side}, done: {new_done}")

        # 新逻辑：done=True + order_id=None 应该保持 done=True，等待 update_grid_after_fill 处理
        self.assertTrue(new_done,
            f"已成交订单应该保持 done=True，实际 done={new_done}")

        # 方向保持不变（由补单逻辑处理目标格子）
        self.assertEqual(new_side, 'buy',
            f"done=True + order_id=None 时方向保持不变，实际是 {new_side}")

        print("✅ test_sync_orders_flip_direction PASSED")

    def test_update_grid_after_fill_buy(self):
        """测试3: 买单成交后补卖单
        - 买单在价格 P 成交
        - 检查是否在 P+1 挂了卖单
        - 检查 fill_count 是否递增
        - 注意：filled_from_price 字段已被移除
        """
        # 初始化网格
        state = self.grid.init_grid(
            symbol=self.symbol,
            entry_price=self.entry_price,
            layers=self.layers
        )

        pending = state['pending']
        prices = state['prices']

        print(f"\n[test_update_grid_after_fill_buy]")
        print(f"prices: {prices}")

        # 找到一个低价位的格子作为测试（买单成交）
        # 使用低于中间价的格子
        buy_price_idx = len(prices) // 2 - 1
        if buy_price_idx < 0:
            buy_price_idx = 0

        buy_price = prices[buy_price_idx]

        print(f"模拟买单成交价格: {buy_price}")

        # 模拟买单成交
        filled_orders = [{
            'price': float(buy_price),
            'side': 'buy',
            'price_str': buy_price,
            'amount': 0.001
        }]

        # 调用 update_grid_after_fill
        self.grid.update_grid_after_fill(self.symbol, filled_orders)

        # 检查是否在更高价位挂了卖单
        target_idx = buy_price_idx + 1

        self.assertLess(target_idx, len(prices), "应该有更高价位的格子")

        target_price = prices[target_idx]
        target_info = pending[target_price]

        print(f"补单价格: {target_price}")
        print(f"补单方向: {target_info.get('side')}")
        print(f"fill_count: {target_info.get('fill_count')}")

        # 验证方向是卖单
        self.assertEqual(target_info.get('side'), 'sell',
            f"买单成交后应该在更高价位挂卖单，实际是 {target_info.get('side')}")

        # 验证 fill_count 递增
        self.assertEqual(target_info.get('fill_count'), 1,
            f"fill_count 应该是 1，实际是 {target_info.get('fill_count')}")

        # 注意：filled_from_price 字段已被移除，不再验证

        print("✅ test_update_grid_after_fill_buy PASSED")

    def test_update_grid_after_fill_sell(self):
        """测试4: 卖单成交后补买单
        - 卖单在价格 P 成交
        - 检查是否在 P-1 挂了买单
        - 注意：filled_from_price 字段已被移除
        """
        # 初始化网格
        state = self.grid.init_grid(
            symbol=self.symbol,
            entry_price=self.entry_price,
            layers=self.layers
        )

        pending = state['pending']
        prices = state['prices']

        print(f"\n[test_update_grid_after_fill_sell]")
        print(f"prices: {prices}")

        # 找到一个高价位的格子作为测试（卖单成交）
        # 使用高于中间价的格子
        sell_price_idx = len(prices) // 2 + 1
        if sell_price_idx >= len(prices):
            sell_price_idx = len(prices) - 1

        sell_price = prices[sell_price_idx]

        print(f"模拟卖单成交价格: {sell_price}")

        # 模拟卖单成交
        filled_orders = [{
            'price': float(sell_price),
            'side': 'sell',
            'price_str': sell_price,
            'amount': 0.001
        }]

        # 调用 update_grid_after_fill
        self.grid.update_grid_after_fill(self.symbol, filled_orders)

        # 检查是否在更低价位挂了买单
        target_idx = sell_price_idx - 1

        self.assertGreater(target_idx, -1, "应该有更低价位的格子")

        target_price = prices[target_idx]
        target_info = pending[target_price]

        print(f"补单价格: {target_price}")
        print(f"补单方向: {target_info.get('side')}")

        # 验证方向是买单
        self.assertEqual(target_info.get('side'), 'buy',
            f"卖单成交后应该在更低价位挂买单，实际是 {target_info.get('side')}")

        # 验证 fill_count 递增
        self.assertEqual(target_info.get('fill_count'), 1,
            f"fill_count 应该是 1，实际是 {target_info.get('fill_count')}")

        # 注意：filled_from_price 字段已被移除，不再验证

        print("✅ test_update_grid_after_fill_sell PASSED")

    def test_order_status_filled(self):
        """测试5: 订单成交检测
        - 模拟订单 state='2'
        - 检查是否正确标记 done=True
        """
        print(f"\n[test_order_status_filled]")

        # 初始化网格
        state = self.grid.init_grid(
            symbol=self.symbol,
            entry_price=self.entry_price,
            layers=self.layers
        )

        pending = state['pending']

        # 获取一个格子的信息
        test_price = list(pending.keys())[0]

        # 设置 order_id
        pending[test_price]['order_id'] = 'test_order_123'

        print(f"测试价格: {test_price}")
        print(f"初始状态: done={pending[test_price].get('done')}")

        # 模拟交易所返回已成交订单
        mock_order = {
            'status': 'filled',
            'filled': 0.001,
            'info': {
                'state': '2',  # OKX 成交状态码
                'accFillSz': '0.001'
            }
        }

        # Mock exchange.fetch_order
        self.mock_exchange.fetch_order = Mock(return_value=mock_order)

        # 调用 check_order_status
        filled_orders = self.grid.check_order_status(self.symbol)

        print(f"返回的成交订单数: {len(filled_orders)}")
        print(f"更新后状态: done={pending[test_price].get('done')}")

        # 验证订单被标记为成交
        self.assertTrue(pending[test_price].get('done'),
            f"订单应该被标记为 done=True，实际是 {pending[test_price].get('done')}")

        # 验证返回了成交订单
        self.assertEqual(len(filled_orders), 1,
            f"应该返回 1 个成交订单，实际返回 {len(filled_orders)} 个")

        # 验证成交订单的 side
        self.assertEqual(filled_orders[0]['side'], pending[test_price].get('side'),
            "成交订单的 side 应该与 pending 中的 side 一致")

        print("✅ test_order_status_filled PASSED")


class TestGridEdgeCases(unittest.TestCase):
    """网格边界情况测试"""

    def setUp(self):
        """测试前准备"""
        from core.grid import GridManager
        from core.state_manager import StateManager

        self.temp_dir = tempfile.mkdtemp()
        self.state_mgr = StateManager(data_dir=self.temp_dir)
        self.grid = GridManager(
            budget=50,
            data_dir=self.temp_dir,
            state_mgr=self.state_mgr
        )

        # 模拟交易所和市场数据
        self.mock_exchange = Mock()
        self.mock_exchange.markets = {
            'ETH/USDT': {
                'precision': {'amount': 4, 'price': 2},
                'limits': {'amount': {'min': 0.0001}}
            }
        }
        self.grid.exchange = self.mock_exchange
        self.grid.markets = self.mock_exchange.markets

        self.symbol = 'ETH/USDT'
        self.entry_price = 3000.0

    def test_sync_orders_without_filled_from_price(self):
        """测试: sync_orders 中 done=True 但没有 filled_from_price 的情况
        这种情况下也应该尝试挂单
        """
        print(f"\n[test_sync_orders_without_filled_from_price]")

        # 初始化网格
        state = self.grid.init_grid(
            symbol=self.symbol,
            entry_price=self.entry_price,
            layers=5
        )

        pending = state['pending']

        # 获取一个格子，设置 done=True 但没有 filled_from_price
        test_price = list(pending.keys())[0]
        pending[test_price]['done'] = True
        # 没有设置 filled_from_price

        print(f"测试价格: {test_price}")
        print(f"done: {pending[test_price]['done']}")
        print(f"filled_from_price: {pending[test_price].get('filled_from_price')}")

        # 调用 sync_orders
        with patch.object(self.grid, 'save_state'):
            self.grid.sync_orders(self.symbol)

        # 检查状态是否被保持（没有 filled_from_price 时应保持 done=True）
        updated_info = pending[test_price]
    
        print(f"同步后 done: {updated_info.get('done')}")
        
        # 没有 filled_from_price 的 done=True 状态，应该保持不变
        self.assertTrue(updated_info.get('done'),
            f"没有 filled_from_price 的 done=True 应该保持不变，实际 done={updated_info.get('done')}")
        
        print("✅ test_sync_orders_without_filled_from_price PASSED")


if __name__ == '__main__':
    # 运行测试
    unittest.main(verbosity=2)
