#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Core 模块单元测试
测试：StateManager、PositionManager
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import pytest
import os
import tempfile
import json


class TestStateManager:
    """StateManager 测试"""
    
    def test_save_load(self):
        """测试保存和加载"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            
            # 设置数据
            sm.set_positions({'BTC/USDT': {'size': 0.5}})
            sm.set_grid({'ETH/USDT': {'status': 'active'}})
            
            # 重新加载
            sm2 = StateManager(data_dir=tmpdir)
            
            assert 'BTC/USDT' in sm2.get_positions()
            assert 'ETH/USDT' in sm2.get_grid()
    
    def test_backup_restore(self):
        """测试备份恢复"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            sm.set_positions({'BTC': {'size': 1}})
            
            # 模拟主文件损坏
            with open(os.path.join(tmpdir, 'state.json'), 'w') as f:
                f.write('invalid json')
            
            # 应该能从备份恢复
            success = sm.load()
            assert success, "应该能从备份恢复"
            assert 'BTC' in sm.get_positions()
    
    def test_clear_all(self):
        """测试清空"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            sm.set_positions({'BTC': {'size': 1}})
            sm.set_grid({'ETH': {'status': 'active'}})
            
            sm.clear_all()
            
            assert len(sm.get_positions()) == 0
            assert len(sm.get_grid()) == 0
    
    def test_version_control(self):
        """测试版本控制"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            
            # 模拟旧版本数据
            with open(os.path.join(tmpdir, 'state.json'), 'w') as f:
                json.dump({'version': '1', 'positions': {}, 'grid': {}}, f)
            
            success = sm.load()
            assert success, "版本不匹配应该也能加载"
    
    def test_none_handling(self):
        """测试 None 值处理"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            
            # 设置 None 值
            sm.set_positions({'BTC': {'size': None}})
            sm.set_grid({'ETH': {'entry_price': None}})
            
            # 应该能正常保存和加载
            sm2 = StateManager(data_dir=tmpdir)
            assert 'BTC' in sm2.get_positions()
    
    def test_get_status(self):
        """测试获取状态"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            sm.set_positions({'BTC': {'size': 1}})
            sm.set_grid({'ETH': {'status': 'active'}})
            
            status = sm.get_status()
            
            assert status['version'] == '2'
            assert status['positions_count'] == 1
            assert status['grid_count'] == 1


class TestPosition:
    """Position 测试"""
    
    def test_position_creation(self):
        """测试创建持仓"""
        from core.position import Position, Batch
        
        pos = Position(
            symbol='BTC/USDT',
            total_amount=0.5,
            avg_price=65000
        )
        
        assert pos.symbol == 'BTC/USDT'
        assert pos.total_amount == 0.5
        assert pos.avg_price == 65000
        assert pos.status == 'open'
    
    def test_position_with_batches(self):
        """测试带批次的持仓"""
        from core.position import Position, Batch
        
        batches = [
            Batch(batch_id=1, amount=0.3, price=65000, cost=19500, timestamp='2024-01-01'),
            Batch(batch_id=2, amount=0.2, price=60000, cost=12000, timestamp='2024-01-02')
        ]
        
        pos = Position(
            symbol='BTC/USDT',
            total_amount=0.5,
            avg_price=63000,
            batches=batches
        )
        
        assert len(pos.batches) == 2
        assert pos.batches[0].batch_id == 1
        assert pos.batches[1].batch_id == 2
    
    def test_position_to_dict(self):
        """测试转换为字典"""
        from core.position import Position
        
        pos = Position(
            symbol='BTC/USDT',
            total_amount=0.5,
            avg_price=65000
        )
        
        data = pos.to_dict()
        
        assert data['symbol'] == 'BTC/USDT'
        assert data['total_amount'] == 0.5
        assert data['avg_price'] == 65000
    
    def test_position_from_dict(self):
        """测试从字典创建"""
        from core.position import Position
        
        data = {
            'symbol': 'ETH/USDT',
            'total_amount': 2,
            'avg_price': 3000,
            'status': 'open'
        }
        
        pos = Position.from_dict(data)
        
        assert pos.symbol == 'ETH/USDT'
        assert pos.total_amount == 2


class TestPositionManager:
    """PositionManager 测试"""
    
    @pytest.fixture
    def mgr(self):
        """创建 PositionManager"""
        from core.position import PositionManager
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state = StateManager(data_dir=tmpdir)
            mgr = PositionManager(data_dir=tmpdir, state_mgr=state)
            yield mgr
    
    def test_add_batch_new_position(self, mgr):
        """测试添加新仓位"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        
        pos = mgr.get_position('BTC/USDT')
        assert pos is not None
        assert pos.total_amount == 0.5
        assert pos.avg_price == 65000
        assert len(pos.batches) == 1
    
    def test_add_batch_existing_position(self, mgr):
        """测试追加现有仓位"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        mgr.add_batch('BTC/USDT', 0.3, 60000, 18000)
        
        pos = mgr.get_position('BTC/USDT')
        assert len(pos.batches) == 2
        assert pos.total_amount == 0.8
    
    def test_has_position(self, mgr):
        """测试是否有持仓"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        
        assert mgr.has_position('BTC/USDT') == True
        assert mgr.has_position('ETH/USDT') == False
    
    def test_get_position(self, mgr):
        """测试获取持仓"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        
        pos = mgr.get_position('BTC/USDT')
        assert pos is not None
        assert pos.symbol == 'BTC/USDT'
    
    def test_close_position(self, mgr):
        """测试平仓 - 持仓应被彻底删除"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        mgr.close_position('BTC/USDT', 'test')
        
        # 平仓后持仓应被彻底删除
        pos = mgr.get_position('BTC/USDT')
        assert pos is None
        # 确认不在 positions 字典中
        assert 'BTC/USDT' not in mgr.positions
    
    def test_calculate_avg_price(self, mgr):
        """测试均价计算"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)  # $32500
        mgr.add_batch('BTC/USDT', 0.5, 60000, 30000)  # $30000
        
        pos = mgr.get_position('BTC/USDT')
        expected_avg = (32500 + 30000) / (0.5 + 0.5)
        assert pos.avg_price == expected_avg
    
    def test_get_all_positions(self, mgr):
        """测试获取所有持仓"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        mgr.add_batch('ETH/USDT', 2, 3000, 6000)
        
        count = sum(1 for p in mgr.positions.values() if p.status == 'open')
        assert count == 2
    
    def test_check_stop_loss(self, mgr):
        """测试止损检查"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        
        # 直接设置止损
        pos = mgr.get_position('BTC/USDT')
        pos.stop_loss = 58000
        mgr.save()
        
        # 价格跌破止损
        triggered = mgr.check_stop_loss(57000)
        assert 'BTC/USDT' in triggered
        
        # 价格没跌破
        triggered = mgr.check_stop_loss(60000)
        assert 'BTC/USDT' not in triggered
    
    def test_check_take_profit(self, mgr):
        """测试止盈检查"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        
        # 直接设置止盈
        pos = mgr.get_position('BTC/USDT')
        pos.take_profit = 75000
        mgr.save()
        
        # 价格达到止盈
        triggered = mgr.check_take_profit(76000)
        assert 'BTC/USDT' in triggered
    
    def test_should_add_batch(self, mgr):
        """测试是否应该加仓判断"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        
        # 价格没跌够
        should_add, reason = mgr.should_add_batch('BTC/USDT', 64900)
        assert should_add == False
    
    def test_get_summary(self, mgr):
        """测试汇总"""
        mgr.add_batch('BTC/USDT', 0.5, 65000, 32500)
        mgr.add_batch('ETH/USDT', 2, 3000, 6000)
        
        summary = mgr.get_summary()
        
        assert summary['open_count'] == 2
        assert summary['total_value'] == (0.5 * 65000) + (2 * 3000)


class TestGridOrder:
    """GridOrder 测试"""
    
    def test_grid_order_creation(self):
        """测试创建网格订单"""
        from core.position import GridOrder
        
        order = GridOrder(
            side='buy',
            price=60000,
            amount=0.01
        )
        
        assert order.side == 'buy'
        assert order.price == 60000
        assert order.status == 'pending'
    
    def test_grid_order_to_dict(self):
        """测试转换为字典"""
        from core.position import GridOrder
        
        order = GridOrder(
            side='sell',
            price=70000,
            amount=0.02,
            order_id='123'
        )
        
        data = order.to_dict()
        
        assert data['side'] == 'sell'
        assert data['price'] == 70000
        assert data['order_id'] == '123'
    
    def test_grid_order_from_dict(self):
        """测试从字典创建"""
        from core.position import GridOrder
        
        data = {
            'side': 'buy',
            'price': 65000,
            'amount': 0.01,
            'order_id': '456',
            'status': 'filled'
        }
        
        order = GridOrder.from_dict(data)
        
        assert order.side == 'buy'
        assert order.status == 'filled'


class TestGridConfig:
    """GridConfig 测试"""
    
    def test_grid_config_creation(self):
        """测试创建网格配置"""
        from core.position import GridConfig
        
        config = GridConfig(
            enabled=True,
            upper_price=70000,
            lower_price=60000
        )
        
        assert config.enabled == True
        assert config.upper_price == 70000
        assert config.lower_price == 60000
    
    def test_grid_config_to_dict(self):
        """测试转换为字典"""
        from core.position import GridConfig
        
        config = GridConfig(
            enabled=True,
            upper_price=70000,
            lower_price=60000
        )
        
        data = config.to_dict()
        
        assert data['enabled'] == True
        assert data['upper_price'] == 70000
    
    def test_grid_config_orders(self):
        """测试网格订单"""
        from core.position import GridConfig, GridOrder
        
        config = GridConfig(
            enabled=True,
            upper_price=70000,
            lower_price=60000
        )
        
        order = GridOrder(side='buy', price=62000, amount=0.01)
        config.buy_orders.append(order)
        
        assert len(config.buy_orders) == 1


class TestRoundFunctions:
    """精度函数测试"""
    
    def test_round_price(self):
        """测试价格四舍五入"""
        from core.position import round_price
        
        assert round_price(65000.123456, 2) == '65000.12'
        assert round_price(65000.125, 2) == '65000.13'
        assert round_price(0.00000123, 8) == '0.00000123'
    
    def test_parse_price(self):
        """测试价格解析"""
        from core.position import parse_price
        
        assert parse_price('65000.12') == 65000.12
        assert parse_price('0.000001') == 0.000001


class TestEdgeCases:
    """边界条件测试"""
    
    def test_zero_price(self):
        """测试零价格"""
        from core.position import round_price
        
        result = round_price(0, 2)
        assert result == '0.00'
    
    def test_empty_positions(self):
        """测试空持仓"""
        from core.position import PositionManager
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state = StateManager(data_dir=tmpdir)
            mgr = PositionManager(data_dir=tmpdir, state_mgr=state)
            
            # 获取不存在的持仓
            pos = mgr.get_position('NOTEXIST')
            assert pos is None
    
    def test_malformed_json(self):
        """测试损坏的 JSON"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # 写入损坏的 JSON
            with open(os.path.join(tmpdir, 'state.json'), 'w') as f:
                f.write('{ invalid json }')
            
            # 应该能处理
            try:
                sm = StateManager(data_dir=tmpdir)
                # 如果有备份，应该能恢复
                if os.path.exists(os.path.join(tmpdir, 'state.json.bak')):
                    sm.load()
            except Exception:
                pass  # 预期可能报错


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
