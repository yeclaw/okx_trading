#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OKX 网格订单状态检测测试
测试：
1. OKX API 返回的 state 字段（"2" = filled）能否被正确转换为 ccxt 风格的 status
2. check_order_status 函数能否正确检测到已成交的订单
3. force_reconcile 和 reconcile_orders 函数
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import pytest
import json
import tempfile
import os
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, List

from core.grid import GridManager
from core.state_manager import StateManager


# ============================================================================
# Mock 数据：模拟 OKX API 返回格式
# ============================================================================

# OKX API 订单 state 字段含义：
# 0: pending (已下单等待成交)
# 1: open (部分成交)
# 2: filled (完全成交)
# 3: canceled (撤单成功)
# 4: partially_filled (部分成交)
# 5: canceling (撤单中)
# 6: failed (下单失败)

OKX_ORDER_FILLED = {
    'ordId': '123456',
    'state': '2',  # OKX: 2 = filled
    'accFillSz': '0.01',
    'sz': '0.01',
    'side': 'buy',
    'px': '50000',
    'symbol': 'BTC/USDT:USDT'
}

OKX_ORDER_OPEN = {
    'ordId': '123457',
    'state': '1',  # OKX: 1 = open
    'accFillSz': '0',
    'sz': '0.01',
    'side': 'buy',
    'px': '49000',
    'symbol': 'BTC/USDT:USDT'
}

OKX_ORDER_CANCELED = {
    'ordId': '123458',
    'state': '3',  # OKX: 3 = canceled
    'accFillSz': '0',
    'sz': '0.01',
    'side': 'sell',
    'px': '51000',
    'symbol': 'BTC/USDT:USDT'
}

OKX_ORDER_PARTIAL = {
    'ordId': '123459',
    'state': '4',  # OKX: 4 = partially filled
    'accFillSz': '0.005',
    'sz': '0.01',
    'side': 'buy',
    'px': '48000',
    'symbol': 'BTC/USDT:USDT'
}

OKX_ORDER_CLOSED = {
    'ordId': '123460',
    'state': 'closed',  # ccxt 可能返回 closed
    'accFillSz': '0.01',
    'sz': '0.01',
    'side': 'sell',
    'px': '52000',
    'symbol': 'BTC/USDT:USDT'
}


def create_mock_exchange(order_responses: Dict[str, Dict]):
    """创建模拟的交易所对象
    
    Args:
        order_responses: order_id -> OKX API 响应格式的映射
    """
    mock_exchange = Mock()
    mock_exchange.markets = {
        'BTC/USDT:USDT': {
            'precision': {'amount': 4, 'price': 2},
            'limits': {'amount': {'min': 0.0001}}
        }
    }
    
    def mock_fetch_order(order_id, symbol):
        """模拟 fetch_order 返回 OKX 格式的数据"""
        if order_id in order_responses:
            # 使用 ccxt 解析
            okx = __import__('ccxt').okx()
            return okx.parse_order(order_responses[order_id])
        raise Exception(f"Order {order_id} not found")
    
    mock_exchange.fetch_order = mock_fetch_order
    return mock_exchange


class TestOKXStateMapping:
    """测试 OKX state 字段到 ccxt status 的映射"""
    
    def test_okx_state_2_is_filled(self):
        """测试：OKX state="2" 应该被识别为 filled"""
        import ccxt
        
        okx = ccxt.okx()
        parsed = okx.parse_order(OKX_ORDER_FILLED)
        
        # 方法1: 检查原始 state 字段
        assert parsed['info']['state'] == '2', "原始 state 应该是 '2'"
        
        # 方法2: 检查 ccxt 解析后的 status
        # 注意：ccxt 4.x 版本可能直接返回原始值，需要代码中兼容处理
        status = parsed.get('status')
        
        # 代码应该能处理 '2' 或 'filled' 两种情况
        assert status in ['2', 'filled', 'closed'], f"status 应该是 '2'/'filled'/'closed'，实际是 {status}"
        
        # 验证成交数量
        assert parsed.get('filled') == 0.01, "成交数量应该是 0.01"
    
    def test_okx_state_1_is_open(self):
        """测试：OKX state="1" 应该被识别为 open"""
        import ccxt
        
        okx = ccxt.okx()
        parsed = okx.parse_order(OKX_ORDER_OPEN)
        
        assert parsed['info']['state'] == '1'
        status = parsed.get('status')
        assert status in ['1', 'open'], f"status 应该是 '1'/'open'，实际是 {status}"
    
    def test_okx_state_3_is_canceled(self):
        """测试：OKX state="3" 应该被识别为 canceled"""
        import ccxt
        
        okx = ccxt.okx()
        parsed = okx.parse_order(OKX_ORDER_CANCELED)
        
        assert parsed['info']['state'] == '3'
        status = parsed.get('status')
        assert status in ['3', 'canceled', 'cancelled'], f"status 应该是 '3'/'canceled'，实际是 {status}"
    
    def test_okx_state_4_is_partially_filled(self):
        """测试：OKX state="4" 应该被识别为 partially filled"""
        import ccxt
        
        okx = ccxt.okx()
        parsed = okx.parse_order(OKX_ORDER_PARTIAL)
        
        assert parsed['info']['state'] == '4'
        status = parsed.get('status')
        # 部分成交
        assert parsed.get('filled') == 0.005, "部分成交数量应该是 0.005"


class TestCheckOrderStatus:
    """测试 check_order_status 函数"""
    
    @pytest.fixture
    def grid_manager(self, tmp_path):
        """创建测试用的 GridManager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(data_dir=tmpdir)
            gm = GridManager(budget=100, data_dir=tmpdir, state_mgr=state_mgr)
            
            # 初始化一个测试网格
            symbol = 'BTC/USDT:USDT'
            gm.grid_state = {
                symbol: {
                    'status': 'active',
                    'prices': ['48000', '49000', '50000', '51000', '52000'],
                    'pending': {
                        '50000': {'order_id': '123456', 'side': 'buy', 'done': False},
                        '51000': {'order_id': '123457', 'side': 'sell', 'done': False},
                    },
                    'amount_per_trade': 10.0,
                }
            }
            yield gm
    
    def test_check_order_status_filled(self, grid_manager):
        """测试：检测到已成交订单 (state="2")"""
        # Mock exchange 返回已成交订单
        mock_exchange = create_mock_exchange({
            '123456': OKX_ORDER_FILLED,  # filled
            '123457': OKX_ORDER_OPEN,    # open
        })
        grid_manager.exchange = mock_exchange
        
        # 执行检查
        filled_orders = grid_manager.check_order_status('BTC/USDT:USDT')
        
        # 验证：应该返回 1 个成交订单
        assert len(filled_orders) == 1, f"应该返回 1 个成交订单，实际返回 {len(filled_orders)}"
        
        # 验证订单详情
        filled = filled_orders[0]
        assert filled['price'] == 50000, f"成交价格应该是 50000，实际是 {filled['price']}"
        assert filled['side'] == 'buy', f"成交方向应该是 buy，实际是 {filled['side']}"
        assert filled['amount'] == 0.01, f"成交数量应该是 0.01，实际是 {filled['amount']}"
        
        # 验证 pending 状态已更新
        assert grid_manager.grid_state['BTC/USDT:USDT']['pending']['50000']['done'] is True
    
    def test_check_order_status_mixed(self, grid_manager):
        """测试：混合状态的订单"""
        # Mock exchange 返回混合状态
        mock_exchange = create_mock_exchange({
            '123456': OKX_ORDER_FILLED,    # filled
            '123457': OKX_ORDER_CANCELED,  # canceled
        })
        grid_manager.exchange = mock_exchange
        
        # 执行检查
        filled_orders = grid_manager.check_order_status('BTC/USDT:USDT')
        
        # 验证：应该只返回 filled 订单
        assert len(filled_orders) == 1, f"应该返回 1 个成交订单，实际返回 {len(filled_orders)}"
        assert filled_orders[0]['price'] == 50000
        
        # 验证被取消的订单应该被清理
        # canceled 订单的 order_id 会被清空（等待重新挂单）
    
    def test_check_order_status_no_pending(self, tmp_path):
        """测试：无 pending 订单时返回空列表"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(data_dir=tmpdir)
            gm = GridManager(budget=100, data_dir=tmpdir, state_mgr=state_mgr)
            
            # 无网格状态
            filled_orders = gm.check_order_status('BTC/USDT:USDT')
            assert filled_orders == [], "无网格状态时应返回空列表"
    
    def test_check_order_status_with_closed(self, grid_manager):
        """测试：检测到 closed 状态订单（可能部分成交）"""
        # Mock exchange 返回 closed 状态订单（有成交）
        okx_order_closed_with_fill = {
            'ordId': '123460',
            'state': 'closed',
            'accFillSz': '0.01',
            'sz': '0.01',
            'side': 'sell',
            'px': '52000',
            'symbol': 'BTC/USDT:USDT'
        }
        mock_exchange = create_mock_exchange({
            '123460': okx_order_closed_with_fill,
        })
        grid_manager.exchange = mock_exchange
        
        # 添加一个 closed 状态的订单
        grid_manager.grid_state['BTC/USDT:USDT']['pending']['52000'] = {
            'order_id': '123460', 'side': 'sell', 'done': False
        }
        
        # 执行检查
        filled_orders = grid_manager.check_order_status('BTC/USDT:USDT')
        
        # 验证：closed 状态有成交也应该被识别
        assert len(filled_orders) >= 1, "closed 状态有成交应该被识别"


class TestForceReconcile:
    """测试 force_reconcile 函数"""
    
    @pytest.fixture
    def grid_manager(self, tmp_path):
        """创建测试用的 GridManager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(data_dir=tmpdir)
            gm = GridManager(budget=100, data_dir=tmpdir, state_mgr=state_mgr)
            
            # 初始化测试网格
            symbol = 'BTC/USDT:USDT'
            gm.grid_state = {
                symbol: {
                    'status': 'active',
                    'prices': ['48000', '49000', '50000', '51000', '52000'],
                    'pending': {
                        '50000': {'order_id': '999001', 'side': 'buy', 'done': False},
                        '51000': {'order_id': '999002', 'side': 'sell', 'done': False},
                    },
                    'amount_per_trade': 10.0,
                }
            }
            yield gm
    
    def test_force_reconcile_filled(self, grid_manager):
        """测试：强制对账检测到成交"""
        # Mock exchange
        mock_exchange = create_mock_exchange({
            '999001': OKX_ORDER_FILLED,  # filled
            '999002': OKX_ORDER_OPEN,   # open
        })
        grid_manager.exchange = mock_exchange
        
        # 执行强制对账
        filled = grid_manager.force_reconcile('BTC/USDT:USDT')
        
        # 验证
        assert len(filled) == 1, f"应该返回 1 个成交订单，实际返回 {len(filled)}"
        assert filled[0]['price'] == 50000
    
    def test_force_reconcile_all_filled(self, grid_manager):
        """测试：所有订单都已成交"""
        mock_exchange = create_mock_exchange({
            '999001': OKX_ORDER_FILLED,
            '999002': OKX_ORDER_FILLED,
        })
        grid_manager.exchange = mock_exchange
        
        filled = grid_manager.force_reconcile('BTC/USDT:USDT')
        
        assert len(filled) == 2, "两个订单都成交应该返回 2 个"
    
    def test_force_reconcile_no_exchange(self, grid_manager):
        """测试：无 exchange 时跳过对账"""
        grid_manager.exchange = None
        
        filled = grid_manager.force_reconcile('BTC/USDT:USDT')
        
        # 无 exchange 时应返回空列表
        assert filled == [], "无 exchange 时应返回空列表"


class TestReconcileOrders:
    """测试 reconcile_orders 函数（启动对账）"""
    
    @pytest.fixture
    def grid_manager(self, tmp_path):
        """创建测试用的 GridManager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(data_dir=tmpdir)
            gm = GridManager(budget=100, data_dir=tmpdir, state_mgr=state_mgr)
            
            # 初始化测试网格
            symbol = 'BTC/USDT:USDT'
            gm.grid_state = {
                symbol: {
                    'status': 'active',
                    'prices': ['48000', '49000', '50000', '51000', '52000'],
                    'pending': {
                        '48000': {'order_id': '888001', 'side': 'buy', 'done': False},
                        '49000': {'order_id': '888002', 'side': 'buy', 'done': False},
                        '50000': {'order_id': '888003', 'side': 'sell', 'done': False},
                    },
                    'amount_per_trade': 10.0,
                }
            }
            yield gm
    
    def test_reconcile_orders_offline_filled(self, grid_manager):
        """测试：对账检测到离线期间的成交"""
        # 模拟离线期间订单已成交
        mock_exchange = create_mock_exchange({
            '888001': OKX_ORDER_FILLED,   # filled - 离线成交
            '888002': OKX_ORDER_CANCELED, # canceled - 被撤单
            '888003': OKX_ORDER_OPEN,     # open - 还在挂单
        })
        grid_manager.exchange = mock_exchange
        
        # 执行对账
        filled = grid_manager.reconcile_orders('BTC/USDT:USDT')
        
        # 验证：应该捕获离线成交
        assert len(filled) >= 1, f"应该返回至少 1 个成交订单，实际返回 {len(filled)}"
        
        # 验证 filled 订单的格式
        for order in filled:
            assert 'price' in order, "订单应包含 price"
            assert 'side' in order, "订单应包含 side"
            assert 'amount' in order, "订单应包含 amount"
            assert 'price_str' in order, "订单应包含 price_str"
    
    def test_reconcile_orders_partial_fill(self, grid_manager):
        """测试：对账检测到部分成交"""
        mock_exchange = create_mock_exchange({
            '888001': OKX_ORDER_PARTIAL,  # partially filled
        })
        grid_manager.exchange = mock_exchange
        
        filled = grid_manager.reconcile_orders('BTC/USDT:USDT')
        
        # 部分成交也应该被识别
        assert len(filled) >= 1
        assert filled[0]['amount'] == 0.005
    
    def test_reconcile_orders_all_open(self, grid_manager):
        """测试：所有订单都是 open 状态"""
        mock_exchange = create_mock_exchange({
            '888001': OKX_ORDER_OPEN,
            '888002': OKX_ORDER_OPEN,
            '888003': OKX_ORDER_OPEN,
        })
        grid_manager.exchange = mock_exchange
        
        filled = grid_manager.reconcile_orders('BTC/USDT:USDT')
        
        # 都是 open 应该没有成交
        assert filled == [], "都是 open 时应返回空列表"
    
    def test_reconcile_orders_no_state(self, tmp_path):
        """测试：无网格状态时返回空"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(data_dir=tmpdir)
            gm = GridManager(budget=100, data_dir=tmpdir, state_mgr=state_mgr)
            
            filled = gm.reconcile_orders('BTC/USDT:USDT')
            
            assert filled == [], "无网格状态时应返回空列表"


class TestGridOrderStatusIntegration:
    """集成测试：完整的状态检测流程"""
    
    def test_full_workflow(self, tmp_path):
        """测试：完整的订单状态检测工作流"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(data_dir=tmpdir)
            gm = GridManager(budget=100, data_dir=tmpdir, state_mgr=state_mgr)
            
            symbol = 'BTC/USDT:USDT'
            
            # 1. 初始化网格
            gm.grid_state = {
                symbol: {
                    'status': 'active',
                    'prices': ['48000', '49000', '50000'],
                    'pending': {
                        '48000': {'order_id': '111001', 'side': 'buy', 'done': False},
                        '49000': {'order_id': '111002', 'side': 'buy', 'done': False},
                        '50000': {'order_id': '111003', 'side': 'sell', 'done': False},
                    },
                    'amount_per_trade': 10.0,
                    'position_size': 0,
                    'entry_price': 0,
                    'realized_pnl': 0,
                }
            }
            
            # 2. Mock: 订单 111001 成交，111002/111003 还在挂单
            mock_exchange = create_mock_exchange({
                '111001': OKX_ORDER_FILLED,
                '111002': OKX_ORDER_OPEN,
                '111003': OKX_ORDER_OPEN,
            })
            gm.exchange = mock_exchange
            
            # 3. 检查订单状态
            filled = gm.check_order_status(symbol)
            
            # 4. 验证成交被正确识别
            assert len(filled) == 1
            assert filled[0]['price'] == 48000
            assert filled[0]['side'] == 'buy'
            
            # 5. 验证状态已更新
            assert gm.grid_state[symbol]['pending']['48000']['done'] is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
