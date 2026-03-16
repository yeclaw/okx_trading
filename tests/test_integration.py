#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 策略集成测试
测试：OKX API 调用、状态持久化、资金同步
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import pytest
import os
import tempfile
from unittest.mock import Mock, patch
from datetime import datetime


class TestStateManager:
    """状态管理集成测试"""
    
    def test_save_load_positions(self):
        """测试持仓保存和加载"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            
            # 设置持仓
            sm.set_positions({
                'BTC/USDT': {'size': 0.5, 'entry_price': 65000}
            })
            
            # 重新加载
            sm2 = StateManager(data_dir=tmpdir)
            
            assert 'BTC/USDT' in sm2.get_positions()
            assert sm2.get_positions()['BTC/USDT']['size'] == 0.5
    
    def test_save_load_grid(self):
        """测试网格保存和加载"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            
            # 设置网格
            sm.set_grid({
                'ETH/USDT': {'status': 'active', 'layers': 5}
            })
            
            # 重新加载
            sm2 = StateManager(data_dir=tmpdir)
            
            assert 'ETH/USDT' in sm2.get_grid()
            assert sm2.get_grid()['ETH/USDT']['status'] == 'active'
    
    def test_backup_restore(self):
        """测试备份恢复"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            sm.set_positions({'BTC': {'size': 1}})
            
            # 模拟主文件损坏
            with open(os.path.join(tmpdir, 'state.json'), 'w') as f:
                f.write('corrupt json')
            
            # 应该能从备份恢复
            success = sm.load()
            assert success, "应该能从备份恢复"
            assert 'BTC' in sm.get_positions()
    
    def test_clear_all(self):
        """测试清空状态"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            sm.set_positions({'BTC': {'size': 1}})
            sm.set_grid({'ETH': {'status': 'active'}})
            
            sm.clear_all()
            
            assert len(sm.get_positions()) == 0
            assert len(sm.get_grid()) == 0


class TestOKXClient:
    """OKX 客户端集成测试"""
    
    @pytest.fixture
    def client(self):
        """获取 API 客户端"""
        from okx_client import get_client
        return get_client()
    
    def test_fetch_balance(self, client):
        """测试获取余额"""
        balance = client.fetch_balance()
        
        assert balance is not None
        assert 'code' in balance
        assert balance['code'] == '0', f"API 返回错误: {balance.get('msg')}"
    
    def test_fetch_ticker(self, client):
        """测试获取行情"""
        ticker = client.fetch_ticker('BTC/USDT')
        
        assert ticker is not None
        assert 'last' in ticker
        assert ticker['last'] > 0, "价格应该大于 0"
        assert ticker['last'] < 1000000, "价格应该合理"
    
    def test_fetch_ohlcv(self, client):
        """测试获取 K 线"""
        ohlcv = client.fetch_ohlcv('BTC/USDT', limit=100)
        
        assert ohlcv is not None
        assert len(ohlcv) >= 50, "应该返回至少 50 根 K 线"
        assert len(ohlcv[0]) == 6, "K 线应该有 6 个字段"
    
    def test_fetch_positions(self, client):
        """测试获取持仓"""
        positions = client.fetch_positions()
        
        assert positions is not None
        assert isinstance(positions, list)


class TestMarketData:
    """市场数据测试"""
    
    @pytest.fixture
    def client(self):
        from okx_client import get_client
        return get_client()
    
    def test_multi_symbol_data(self, client):
        """测试多币种数据获取"""
        from config import RSI_SYMBOLS
        
        results = []
        for symbol in RSI_SYMBOLS[:3]:  # 只测前 3 个
            try:
                ticker = client.fetch_ticker(symbol)
                if ticker and ticker.get('last', 0) > 0:
                    results.append({
                        'symbol': symbol,
                        'price': ticker['last']
                    })
            except Exception as e:
                pytest.skip(f"获取 {symbol} 失败: {e}")
        
        assert len(results) >= 2, "应该能获取至少 2 个币种数据"
    
    def test_price_validity(self, client):
        """测试价格有效性"""
        ticker = client.fetch_ticker('BTC/USDT')
        
        price = ticker['last']
        assert price > 0, f"价格应该 > 0: {price}"
        assert price < 1000000, f"价格应该 < 1000000: {price}"
    
    def test_ohlcv_structure(self, client):
        """测试 K 线数据结构"""
        ohlcv = client.fetch_ohlcv('BTC/USDT', limit=10)
        
        # 检查字段
        for candle in ohlcv:
            assert len(candle) == 6, f"K 线应该有 6 个字段: {candle}"
            assert candle[0] > 1700000000, "时间戳应该合理"
            assert candle[1] > 0, "开盘价应该 > 0"
            assert candle[4] > 0, "收盘价应该 > 0"


class TestConfig:
    """配置测试"""
    
    def test_config_secrets_exists(self):
        """测试敏感配置存在"""
        try:
            from config_secrets import OKX_CONFIG
            assert 'api_key' in OKX_CONFIG
            assert 'api_secret' in OKX_CONFIG
            assert 'passphrase' in OKX_CONFIG
        except ImportError:
            pytest.skip("config_secrets.py 不存在")
    
    def test_config_params(self):
        """测试配置参数"""
        from config import TRADING_CONFIG, RUN_CONFIG
        
        # TRADING_CONFIG
        assert 'first_batch' in TRADING_CONFIG
        assert 'grid_budget' in TRADING_CONFIG
        assert TRADING_CONFIG['first_batch'] > 0
        assert TRADING_CONFIG['grid_budget'] > 0
        
        # RUN_CONFIG
        assert 'check_interval' in RUN_CONFIG
        assert 'scan_interval' in RUN_CONFIG
        assert RUN_CONFIG['check_interval'] > 0
        assert RUN_CONFIG['scan_interval'] > 0


class TestDataLogger:
    """数据记录器测试"""
    
    def test_log_opportunity(self):
        """测试记录机会"""
        from data_logger import DataLogger
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = DataLogger(log_dir=tmpdir)
            
            # 记录机会
            logger.log_opportunity('BTC/USDT', {
                'action': 'buy',
                'rsi': 25,
                'confidence': 0.6
            })
            
            # 检查文件存在
            assert os.path.exists(os.path.join(tmpdir, 'opportunities.jsonl'))


class TestCircuitBreaker:
    """熔断器测试"""
    
    def test_circuit_breaker_states(self):
        """测试熔断器状态"""
        from main import CircuitBreaker
        
        cb = CircuitBreaker()
        
        # 初始状态
        assert cb.state == 'CLOSED'
        assert cb.can_proceed() == True
        
        # 记录失败
        for _ in range(4):
            cb.record_failure()
        
        # 应该还是 CLOSED
        assert cb.state == 'CLOSED'
        
        # 第 5 次失败
        cb.record_failure()
        assert cb.state == 'OPEN'
        assert cb.can_proceed() == False


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
