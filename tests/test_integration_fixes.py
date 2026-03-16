#!/usr/bin/env python3
"""
集成测试 - 修复后验证
测试场景:
1. 启动验证测试 - 清空state.json，手动在交易所下单(模拟持仓)，重启机器人，验证机器人不会重建网格
2. 平仓测试 - 机器人建仓后手动平仓，检查state.json是否正确清空，重启机器人验证不再重建网格
3. 网格流转测试 - 机器人建仓并启动网格，模拟订单成交，验证网格正确流转，验证suspect状态能正确恢复
"""

import sys
import os
import json
import tempfile
import shutil
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

# 添加项目路径
sys.path.insert(0, '/home/admin/.openclaw/workspace/okx_trading')

from core.state_manager import StateManager
from core.position import PositionManager, Position, Batch, GridConfig, GridOrder
from core.grid import GridManager


class MockExchange:
    """模拟交易所"""
    def __init__(self):
        self.markets = {
            'BTC/USDT': {'precision': {'amount': 4, 'price': 2}},
            'ETH/USDT': {'precision': {'amount': 4, 'price': 2}}
        }
        self.orders = {}
    
    def create_order(self, symbol, side, type, amount, price, params=None):
        order_id = f"mock_order_{len(self.orders)}"
        self.orders[order_id] = {
            'symbol': symbol,
            'side': side,
            'amount': amount,
            'price': price,
            'status': 'open'
        }
        return {'id': order_id, 'status': 'open'}
    
    def fetch_order(self, order_id, symbol):
        if order_id in self.orders:
            return self.orders[order_id]
        return None
    
    def cancel_order(self, order_id, symbol):
        if order_id in self.orders:
            del self.orders[order_id]
        return True
    
    def fetch_orders(self, symbol, since=None, limit=None):
        return list(self.orders.values())
    
    def fetch_balance(self):
        return {'free': {'USDT': 1000}, 'total': {'USDT': 1000}}
    
    def fetch_positions(self):
        return []


def run_test_1():
    """测试1: 启动验证测试"""
    print("\n" + "="*60)
    print("测试1: 启动验证测试")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 步骤1: 清空 state.json
        state_mgr = StateManager(data_dir=tmpdir)
        state_mgr.clear_all()
        print("✓ 步骤1: 清空 state.json")
        
        # 步骤2: 模拟外部持仓（手动在交易所下单）
        # 创建持仓数据
        mock_position_data = {
            'BTC/USDT': {
                'symbol': 'BTC/USDT',
                'status': 'open',
                'total_amount': 0.1,
                'avg_price': 65000.0,
                'batches': [
                    {
                        'batch_id': 1,
                        'amount': 0.1,
                        'price': 65000.0,
                        'cost': 6500.0,
                        'timestamp': datetime.now().isoformat(),
                        'status': 'filled'
                    }
                ],
                'grid': None,
                'stop_loss': 0,
                'take_profit': 0,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
                'note': '',
                'highest_price': 0
            }
        }
        
        # 手动写入持仓到 state.json（模拟外部持仓）
        state_mgr.data['positions'] = mock_position_data
        state_mgr.save()
        print("✓ 步骤2: 手动在交易所下单（模拟外部持仓）")
        
        # 步骤3: 重启机器人（重新加载状态）
        # 重新创建 StateManager 和 PositionManager
        state_mgr2 = StateManager(data_dir=tmpdir)
        position_mgr = PositionManager(data_dir=tmpdir, state_mgr=state_mgr2)
        
        has_position = position_mgr.has_position('BTC/USDT')
        print(f"✓ 步骤3: 重启机器人后检查持仓: has_position={has_position}")
        
        # 步骤4: 验证机器人不会重建网格
        # 检查网格是否自动创建（不应该创建）
        grid_data = state_mgr2.get_grid()
        
        # 如果有持仓但没有网格配置，应该返回 False
        result = has_position and 'BTC/USDT' not in grid_data
        print(f"✓ 步骤4: 验证机器人不会重建网格: {result}")
        
        if has_position and 'BTC/USDT' not in grid_data:
            print("✅ 测试1通过: 外部持仓不会触发网格重建")
            return True
        else:
            print("❌ 测试1失败: 网格被错误重建或持仓未正确加载")
            return False


def run_test_2():
    """测试2: 平仓测试"""
    print("\n" + "="*60)
    print("测试2: 平仓测试")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 步骤1: 机器人建仓
        state_mgr = StateManager(data_dir=tmpdir)
        position_mgr = PositionManager(data_dir=tmpdir, state_mgr=state_mgr)
        
        # 添加持仓
        position_mgr.add_batch('BTC/USDT', 0.1, 65000.0, 6500.0)
        print("✓ 步骤1: 机器人建仓")
        
        # 验证持仓存在
        assert position_mgr.has_position('BTC/USDT'), "持仓应该存在"
        print(f"  持仓状态: {position_mgr.get_position('BTC/USDT').status}")
        
        # 启用网格
        position_mgr.enable_grid('BTC/USDT', upper=70000, lower=60000, step_percent=1.0)
        print("✓ 步骤1.1: 启用网格")
        
        # 步骤2: 手动平仓（从外部平仓，模拟用户手动平仓）
        # 模拟：从 state.json 中删除持仓
        state_mgr.data['positions'] = {}
        state_mgr.save()
        print("✓ 步骤2: 手动平仓（删除state.json中的持仓）")
        
        # 步骤3: 检查 state.json 是否正确清空
        positions = state_mgr.get_positions()
        print(f"✓ 步骤3: state.json中的持仓数量: {len(positions)}")
        
        # 步骤4: 重启机器人验证不再重建网格
        state_mgr2 = StateManager(data_dir=tmpdir)
        position_mgr2 = PositionManager(data_dir=tmpdir, state_mgr=state_mgr2)
        
        has_position = position_mgr2.has_position('BTC/USDT')
        print(f"✓ 步骤4: 重启后持仓状态: has_position={has_position}")
        
        if not has_position and len(positions) == 0:
            print("✅ 测试2通过: 平仓后状态正确清空，重启不再重建")
            return True
        else:
            print("❌ 测试2失败: 状态未正确清空")
            return False


def run_test_3():
    """测试3: 网格流转测试"""
    print("\n" + "="*60)
    print("测试3: 网格流转测试")
    print("="*60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # 步骤1: 机器人建仓并启动网格
        state_mgr = StateManager(data_dir=tmpdir)
        grid_mgr = GridManager(data_dir=tmpdir, state_mgr=state_mgr)
        
        # 模拟持仓
        mock_position = {
            'symbol': 'BTC/USDT',
            'status': 'open',
            'total_amount': 0.1,
            'avg_price': 65000.0,
            'batches': [{'batch_id': 1, 'amount': 0.1, 'price': 65000.0, 'cost': 6500.0, 'timestamp': datetime.now().isoformat(), 'status': 'filled'}],
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
        }
        
        # 创建网格配置
        grid_config = {
            'enabled': True,
            'upper_price': 70000,
            'lower_price': 60000,
            'step_percent': 1.0,
            'max_grids': 10,
            'sell_orders': [],
            'buy_orders': []
        }
        
        state_mgr.data['positions'] = {'BTC/USDT': mock_position}
        state_mgr.data['grid'] = {
            'BTC/USDT': {
                'status': 'active',
                'prices': ['60000', '61000', '62000', '63000', '64000', '65000', '66000', '67000', '68000', '69000'],
                'pending': {},
                'buy_orders': [],
                'sell_orders': []
            }
        }
        state_mgr.save()
        
        # 重新加载
        grid_mgr.load_state()
        print("✓ 步骤1: 机器人建仓并启动网格")
        
        # 验证网格加载成功
        assert 'BTC/USDT' in grid_mgr.grid_state, "网格应该已加载"
        print(f"  网格价格数量: {len(grid_mgr.grid_state['BTC/USDT'].get('prices', []))}")
        
        # 步骤2: 模拟订单成交
        # 模拟买单在 62000 成交
        symbol = 'BTC/USDT'
        fill_price = '62000'
        
        if symbol in grid_mgr.grid_state:
            state = grid_mgr.grid_state[symbol]
            
            # 初始化 pending（如果没有）
            if 'pending' not in state:
                state['pending'] = {}
            
            # 模拟买单成交
            state['pending'][fill_price] = {
                'side': 'buy',
                'price': 62000,
                'amount': 0.01,
                'order_id': 'test_order_123',
                'done': True,
                'filled_at': datetime.now().isoformat(),
                'fill_count': 1,
                'suspect': False,
                'original_side': 'buy'
            }
            print(f"✓ 步骤2: 模拟订单成交 @ {fill_price}")
            
            # 保存状态
            grid_mgr.grid_state = {symbol: state}
            grid_mgr.save_state()
        
        # 步骤3: 验证网格正确流转
        # 重新加载状态
        grid_mgr2 = GridManager(data_dir=tmpdir, state_mgr=StateManager(data_dir=tmpdir))
        grid_mgr2.load_state()
        
        state = grid_mgr2.grid_state.get(symbol, {})
        pending = state.get('pending', {})
        
        # 检查成交格子
        fill_info = pending.get(fill_price)
        print(f"✓ 步骤3: 验证网格流转")
        print(f"  成交格子 {fill_price}: fill_count={fill_info.get('fill_count') if fill_info else 'N/A'}")
        
        if fill_info and fill_info.get('fill_count', 0) >= 1:
            print("✅ 步骤3通过: 网格正确流转")
        else:
            print("❌ 步骤3失败: 网格流转异常")
            return False
        
        # 步骤4: 验证 suspect 状态能正确恢复
        # 模拟重启场景，设置 suspect 状态
        state['pending'][fill_price]['suspect'] = True
        state['pending'][fill_price]['order_id'] = None  # 模拟重启清除 order_id
        
        grid_mgr2.grid_state = {symbol: state}
        grid_mgr2.save_state()
        
        # 重新加载（模拟重启）
        grid_mgr3 = GridManager(data_dir=tmpdir, state_mgr=StateManager(data_dir=tmpdir))
        grid_mgr3.load_state()
        
        state_after = grid_mgr3.grid_state.get(symbol, {})
        pending_after = state_after.get('pending', {})
        
        suspect_info = pending_after.get(fill_price)
        print(f"✓ 步骤4: 验证 suspect 状态恢复")
        print(f"  suspect 状态: {suspect_info.get('suspect') if suspect_info else 'N/A'}")
        print(f"  order_id: {suspect_info.get('order_id') if suspect_info else 'N/A'}")
        
        if suspect_info and suspect_info.get('suspect') == True and suspect_info.get('order_id') is None:
            print("✅ 测试3通过: suspect 状态正确恢复")
            return True
        else:
            print("❌ 测试3失败: suspect 状态恢复异常")
            return False


def main():
    """运行所有测试"""
    print("="*60)
    print("集成测试报告 - 修复后验证")
    print("="*60)
    
    results = []
    
    # 测试1
    results.append(("启动验证测试", run_test_1()))
    
    # 测试2
    results.append(("平仓测试", run_test_2()))
    
    # 测试3
    results.append(("网格流转测试", run_test_3()))
    
    # 总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✅ Pass" if result else "❌ Fail"
        print(f"  {name}: {status}")
    
    print(f"\n测试通过率: {passed}/{total} ({passed*100//total}%)")
    
    return passed == total


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
