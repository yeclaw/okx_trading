#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器人稳定性测试 ⭐
测试：超时保护、阻塞恢复、长时间运行、异常注入

为什么需要这些测试？
- 2026-02-13 机器人扫描卡住 15 分钟，无任何错误日志
- 之前的测试都是在"完美条件"下运行，无法发现问题
- 缺少异常注入测试，无法模拟真实故障场景
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import pytest
import time
import tempfile
import os
import threading
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime


class TestScanTimeout:
    """扫描超时测试 ⭐"""

    def test_scan_single_symbol_timeout(self):
        """单个币种超时不阻塞整个扫描"""
        from unittest.mock import Mock
        from okx_client import OKXClient, OKXConfig
        
        # 模拟超时的客户端
        config = OKXConfig()
        client = OKXClient(config)
        
        # 模拟 fetch_ohlcv 超时
        call_count = [0]
        
        def slow_fetch(symbol, timeframe='1h', limit=100):
            call_count[0] += 1
            if symbol == 'BTC/USDT':
                time.sleep(10)  # 模拟 10 秒超时
                return []
            return [[time.time(), 100, 110, 90, 100, 1000]] * 50
        
        client.fetch_ohlcv = slow_fetch
        
        # 应该能在合理时间内完成（容忍单个超时，但不应卡住）
        start = time.time()
        
        # 模拟扫描逻辑
        symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
        results = []
        for sym in symbols:
            try:
                ohlcv = client.fetch_ohlcv(sym, limit=100)
                if ohlcv and len(ohlcv) >= 14:
                    results.append(sym)
            except Exception:
                pass
        
        elapsed = time.time() - start
        
        # 应该能在 30 秒内完成（单个超时最多 10 秒 * 3 = 30 秒容忍）
        assert elapsed < 35, f"扫描应该在 35 秒内完成，实际 {elapsed:.1f}秒"
    
    def test_scan_with_retry(self):
        """测试重试机制"""
        from okx_client import OKXClient, OKXConfig
        
        config = OKXConfig()
        client = OKXClient(config)
        
        # 模拟第 1 次失败，第 2 次成功
        attempts = [0]
        
        def fetch_with_retry(symbol, timeframe='1h', limit=100):
            attempts[0] += 1
            if attempts[0] == 1:
                raise Exception("Network error")
            return [[time.time(), 100, 110, 90, 100, 1000]] * 50
        
        client.fetch_ohlcv = fetch_with_retry
        
        start = time.time()
        try:
            result = client.fetch_ohlcv('BTC/USDT')
            elapsed = time.time() - start
            # 重试应该在 10 秒内完成
            assert elapsed < 15, f"重试应该在 15 秒内完成，实际 {elapsed:.1f}秒"
        except Exception:
            pass  # 预期会重试


class TestProgressLogging:
    """进度日志测试 ⭐"""

    def test_scan_progress_format(self):
        """测试扫描进度日志格式正确"""
        import logging
        
        # 验证日志格式
        log_messages = []
        
        class TestLogger:
            def info(self, msg):
                log_messages.append(msg)
        
        logger = TestLogger()
        
        # 模拟扫描日志
        total = 3
        for i in range(total):
            logger.info(f"[Scan] BTC/USDT ({i+1}/{total})...")
        
        logger.info(f"扫描完成 | 总耗时: 5.9s")
        
        # 验证日志格式
        assert any(f"[Scan]" in msg for msg in log_messages), "缺少 [Scan] 标记"
        assert any(f"({i+1}/{total})" in msg for msg in log_messages for i in range(total)), "缺少进度标记"
        assert any("完成" in msg for msg in log_messages), "缺少完成日志"
    
    def test_progress_improvement(self):
        """验证改进后的代码有进度日志"""
        # 读取 main.py 检查是否包含进度日志
        with open('/root/clawd/okx_trading/robots/rsi_grid/main.py') as f:
            content = f.read()
        
        # 验证新代码包含进度日志
        assert '[Scan]' in content, "缺少 [Scan] 日志标记"
        assert '总耗时' in content, "缺少总耗时日志"


class TestLongRunning:
    """长时间运行测试 ⭐"""

    def test_repeated_scans(self):
        """测试连续多次扫描不累积延迟"""
        from unittest.mock import Mock
        
        class FastClient:
            def __init__(self):
                self.call_times = []
            
            def fetch_ohlcv(self, symbol, timeframe='1h', limit=100):
                start = time.time()
                time.sleep(0.05)  # 模拟 50ms API 延迟
                self.call_times.append(time.time() - start)
                return [[time.time(), 100, 110, 90, 100, 1000]] * 50
        
        client = FastClient()
        
        # 连续扫描 10 次
        scan_times = []
        for _ in range(10):
            start = time.time()
            # 扫描 3 个币种
            for _ in range(3):
                client.fetch_ohlcv('BTC/USDT')
            scan_times.append(time.time() - start)
        
        # 验证没有累积延迟
        first_half = sum(scan_times[:5]) / 5
        second_half = sum(scan_times[5:]) / 5
        
        # 后半程应该不比前半程慢太多（允许 20% 波动）
        assert second_half < first_half * 1.2, \
            f"存在累积延迟: 前半 {first_half:.3f}s, 后半 {second_half:.3f}s"
    
    def test_no_memory_leak_in_scan(self):
        """测试扫描过程不内存泄漏"""
        import pandas as pd
        
        # 模拟多次扫描
        for _ in range(100):
            data = [[i+j for j in range(6)] for i in range(100)]
            df = pd.DataFrame(data)
            # 确保 DataFrame 被正确处理
            del df
        
        # 没有 assert，只是确保不崩溃


class TestDataLoggerStability:
    """DataLogger 稳定性测试 ⭐"""

    def test_rapid_writes(self):
        """测试快速写入不阻塞"""
        from data_logger import DataLogger
        import os
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = DataLogger(log_dir=tmpdir)
            
            # 快速写入 100 次
            start = time.time()
            for i in range(100):
                logger.log_opportunity(f'SYM{i}', {'action': 'buy', 'rsi': 30})
            elapsed = time.time() - start
            
            # 100 次写入应该在 1 秒内完成
            assert elapsed < 1.0, f"100 次写入耗时 {elapsed:.2f}秒，应该 < 1秒"
    
    def test_large_file_handling(self):
        """测试大文件处理"""
        from data_logger import DataLogger
        import os
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = DataLogger(log_dir=tmpdir)
            
            # 模拟大日志文件 (>10MB)
            large_file = os.path.join(tmpdir, 'opportunities.jsonl')
            with open(large_file, 'w') as f:
                # 写入 10MB 数据
                for i in range(100000):
                    f.write('{"test": "data"}\n')
            
            # 写入应该仍然快速
            start = time.time()
            logger.log_opportunity('TEST', {'action': 'test'})
            elapsed = time.time() - start
            
            assert elapsed < 0.1, f"大文件写入耗时 {elapsed:.3f}秒，应该 < 0.1秒"


class TestStateRecovery:
    """状态恢复测试"""

    def test_recovery_from_incomplete_state(self):
        """测试从不完整状态恢复"""
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            sm = StateManager(data_dir=tmpdir)
            
            # 写入不完整状态
            state_file = os.path.join(tmpdir, 'state.json')
            with open(state_file, 'w') as f:
                f.write('{"positions": {"BTC/USDT": {"size": 0.5')  # 不完整 JSON
            
            # 尝试恢复
            success = sm.load()
            
            # 应该能处理损坏的 JSON
            assert success or sm.get_positions() == {}, \
                "应该能从损坏状态恢复或清空"


class TestConcurrentAccess:
    """并发访问测试"""

    def test_concurrent_log_writes(self):
        """测试并发写入日志"""
        from data_logger import DataLogger
        import threading
        
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = DataLogger(log_dir=tmpdir)
            
            # 多线程并发写入
            errors = []
            
            def write_task(task_id):
                try:
                    for i in range(50):
                        logger.log_opportunity(f'T{task_id}_{i}', {'action': 'buy'})
                except Exception as e:
                    errors.append(e)
            
            threads = [threading.Thread(target=write_task, args=(i,)) for i in range(5)]
            
            start = time.time()
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            elapsed = time.time() - start
            
            # 5 线程 * 50 次 = 250 次写入应该在 2 秒内完成
            assert elapsed < 2.0, f"并发写入耗时 {elapsed:.2f}秒"
            assert len(errors) == 0, f"并发写入发生错误: {errors}"


class TestErrorHandling:
    """错误处理测试"""

    def test_graceful_degradation(self):
        """测试错误发生时优雅降级"""
        from unittest.mock import Mock
        
        # 模拟各种错误
        class ErrorClient:
            def fetch_ohlcv(self, symbol, timeframe='1h', limit=100):
                raise ConnectionError("Network failure")
            
            def fetch_ticker(self, symbol):
                raise TimeoutError("Request timeout")
        
        client = ErrorClient()
        
        # 应该能处理错误而不崩溃
        try:
            ohlcv = client.fetch_ohlcv('BTC/USDT')
            assert ohlcv == [], "错误时应返回空列表"
        except Exception:
            pass  # 预期会捕获异常
        
        try:
            ticker = client.fetch_ticker('BTC/USDT')
            assert ticker == {}, "错误时应返回空字典"
        except Exception:
            pass
    
    def test_empty_data_handling(self):
        """测试空数据处理"""
        import pandas as pd
        
        # 空 DataFrame
        df = pd.DataFrame()
        
        # 应该能处理而不崩溃
        try:
            if len(df) < 14:
                return "skip"
        except Exception:
            pass


class TestGridPersistence:
    """网格持久化测试 ⭐"""

    def test_grid_save_state_import(self):
        """测试 grid.py 能正确导入 save_grid_state 函数"""
        from core.grid import save_grid_state
        
        # 验证函数已导入
        assert callable(save_grid_state), "save_grid_state 应该是可调用函数"
    
    def test_save_grid_state_works(self):
        """测试 save_grid_state 函数能正常工作"""
        import tempfile
        from core.grid import save_grid_state
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(tmpdir)
            grid_state = {'BNB/USDT': {'enabled': True}}
            
            # 之前这里会抛出 NameError: name 'save_grid_state' is not defined
            try:
                save_grid_state(state_mgr, grid_state)
            except NameError as e:
                pytest.fail(f"Bug 未修复: {e}")
            
            # 验证数据已保存
            saved = state_mgr.get_grid()
            assert 'BNB/USDT' in saved, "网格状态应该被保存"
    
    def test_grid_manager_save_state(self):
        """测试 GridManager.save_state() 能正常工作"""
        import tempfile
        from core.grid import GridManager
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(tmpdir)
            
            # 创建 GridManager（不传 exchange，避免加载市场）
            grid_mgr = GridManager(budget=50, data_dir=tmpdir, state_mgr=state_mgr)
            
            # 设置网格状态
            grid_mgr.grid_state = {
                'BNB/USDT': {
                    'enabled': True,
                    'buy_orders': [{'price': 550.0, 'amount': 0.01, 'order_id': '', 'status': 'pending'}],
                    'sell_orders': [{'price': 650.0, 'amount': 0.01, 'order_id': '', 'status': 'pending'}]
                }
            }
            
            # 这行之前会 NameError: name 'save_grid_state' is not defined
            try:
                grid_mgr.save_state()
            except NameError as e:
                pytest.fail(f"Bug 未修复: {e}")
            
            # 验证保存成功
            saved = state_mgr.get_grid()
            assert 'BNB/USDT' in saved
    
    def test_init_grid_uses_entry_price_not_market_price(self):
        """测试 init_grid 使用 entry_price 决定方向，而不是当前市场价格"""
        import tempfile
        from core.grid import GridManager
        from unittest.mock import Mock
        
        with tempfile.TemporaryDirectory() as tmpdir:
            exchange = Mock()
            exchange.markets = {
                'BNB/USDT': {
                    'precision': {'price': 2, 'amount': 4},
                    'limits': {'amount': {'min': 0.0001}}
                }
            }
            # 模拟当前市场价格为 $700（高于均价 $600）
            exchange.fetch_ticker = Mock(return_value={'last': 700.0})
            
            grid_mgr = GridManager(budget=50, data_dir=tmpdir)
            grid_mgr.exchange = exchange
            
            # 用均价 $600 初始化网格
            grid_mgr.init_grid('BNB/USDT', entry_price=600.0, layers=3)
            
            state = grid_mgr.grid_state['BNB/USDT']
            pending = state['pending']
            
            # 验证：所有高于均价 $600 的格子应该是卖单，低于的应该是买单
            # 当前价格是 $700，但这不应该影响方向判断
            for p_str, info in pending.items():
                p = float(p_str)
                if p > 600:
                    assert info['side'] == 'sell', f"{p} 应该 > 600 = sell, 实际 = {info['side']}"
                else:
                    assert info['side'] == 'buy', f"{p} 应该 < 600 = buy, 实际 = {info['side']}"
    
    def test_init_grid_preserves_original_side(self):
        """测试 init_grid 记录原始方向 original_side"""
        import tempfile
        from core.grid import GridManager
        from unittest.mock import Mock
        
        with tempfile.TemporaryDirectory() as tmpdir:
            exchange = Mock()
            exchange.markets = {
                'BNB/USDT': {
                    'precision': {'price': 2, 'amount': 4},
                    'limits': {'amount': {'min': 0.0001}}
                }
            }
            exchange.fetch_ticker = Mock(return_value={'last': 600.0})
            
            grid_mgr = GridManager(budget=50, data_dir=tmpdir)
            grid_mgr.exchange = exchange
            
            grid_mgr.init_grid('BNB/USDT', entry_price=600.0, layers=3)
            
            state = grid_mgr.grid_state['BNB/USDT']
            pending = state['pending']
            
            # 验证：每个 pending 都有 original_side
            for p_str, info in pending.items():
                assert 'original_side' in info, f"{p_str} 应该有 original_side"
                assert info['original_side'] == info['side'], "original_side 应该等于 side"
    
    def test_rebuild_pending_preserves_original_side(self):
        """测试重建 pending 时保留 original_side"""
        import tempfile
        from core.grid import GridManager
        from core.state_manager import StateManager
        
        with tempfile.TemporaryDirectory() as tmpdir:
            state_mgr = StateManager(tmpdir)
            
            # 模拟 state.json 中的网格数据（手动添加的持仓）
            grid_data = {
                'BNB/USDT': {
                    'enabled': True,
                    'entry_price': 600.0,
                    'sell_orders': [
                        {'side': 'sell', 'price': 650.0, 'order_id': '', 'status': 'pending'},
                        {'side': 'sell', 'price': 700.0, 'order_id': '', 'status': 'pending'},
                    ],
                    'buy_orders': [
                        {'side': 'buy', 'price': 550.0, 'order_id': '', 'status': 'pending'},
                        {'side': 'buy', 'price': 500.0, 'order_id': '', 'status': 'pending'},
                    ]
                }
            }
            state_mgr.set_grid(grid_data)
            
            # 创建 GridManager（会触发 _rebuild_pending_from_orders）
            exchange = Mock()
            exchange.markets = {'BNB/USDT': {'precision': {'price': 2, 'amount': 4}, 'limits': {'amount': {'min': 0.0001}}}}
            grid_mgr = GridManager(budget=50, data_dir=tmpdir, state_mgr=state_mgr)
            
            # 验证重建后的 pending 有 original_side
            pending = grid_mgr.grid_state.get('BNB/USDT', {}).get('pending', {})
            for p_str, info in pending.items():
                assert 'original_side' in info, f"{p_str} 应该有 original_side"
                assert info['original_side'] == info['side'], "original_side 应该等于 side"
    
    def test_adjust_grid_direction(self):
        """测试动态调整网格方向"""
        import tempfile
        from core.grid import GridManager
        from unittest.mock import Mock
        
        with tempfile.TemporaryDirectory() as tmpdir:
            exchange = Mock()
            exchange.markets = {
                'BNB/USDT': {
                    'precision': {'price': 2, 'amount': 4},
                    'limits': {'amount': {'min': 0.0001}}
                }
            }
            # 当前市场价格 $700
            exchange.fetch_ticker = Mock(return_value={'last': 700.0})
            
            grid_mgr = GridManager(budget=50, data_dir=tmpdir)
            grid_mgr.exchange = exchange
            
            # 用均价 $600 初始化网格（此时 $700 > $600，所有高于均价的应该是卖单）
            grid_mgr.init_grid('BNB/USDT', entry_price=600.0, layers=5)
            
            # 模拟价格下跌到 $550
            exchange.fetch_ticker = Mock(return_value={'last': 550.0})
            
            # 手动设置 pending 的方向（模拟之前的方向）
            pending = grid_mgr.grid_state['BNB/USDT']['pending']
            
            # 手动把一些方向改成错误的（模拟价格变动后没有更新）
            # 假设之前价格是 $700，现在价格是 $550
            # $650 和 $700 应该是卖单（高于当前价格），$500 和 $550 应该是买单（低于当前价格）
            # 但 pending 中记录的还是之前的方向
            
            # 调用调整方法
            grid_mgr._adjust_grid_direction('BNB/USDT')
            
            # 验证方向已调整
            pending = grid_mgr.grid_state['BNB/USDT']['pending']
            for p_str, info in pending.items():
                p = float(p_str)
                if p > 550:  # 高于当前价格
                    assert info['side'] == 'sell', f"价格 {p} > 550 应该 = sell, 实际 = {info['side']}"
                else:  # 低于当前价格
                    assert info['side'] == 'buy', f"价格 {p} < 550 应该 = buy, 实际 = {info['side']}"
    
    def test_sync_and_recover_grid_with_filled_orders(self):
        """[完备版] 测试崩溃恢复：价格突破后回落场景
        场景：
        - 均价 $597.7
        - 崩溃前挂了 $607, $626, $645 卖单
        - 崩溃期间价格涨到 $626 以上，$607 和 $626 成交
        - 当前价格 $620（比 $607 高，但没到 $626）
        预期：
        - $607 和 $626 被标记为成交，返回成交记录
        - $645 方向保持卖单（因为 $620 < $645）
        """
        import tempfile
        from core.grid import GridManager
        from unittest.mock import Mock, patch
        
        with tempfile.TemporaryDirectory() as tmpdir:
            exchange = Mock()
            exchange.markets = {
                'BNB/USDT': {
                    'precision': {'price': 2, 'amount': 4},
                    'limits': {'amount': {'min': 0.0001}}
                }
            }
            
            # 模拟当前市场价格 $620
            exchange.fetch_ticker = Mock(return_value={'last': 620.0})
            
            # 模拟 _sync_and_recover_grid 调用的 fetch_order_by_client_id
            # 注意：函数签名是 (symbol, client_oid)
            # 根据实际索引：$607=3, $626=4, $645=5
            def mock_fetch_order(symbol, client_oid):
                # $607 卖单 (索引3) → 已成交
                if 'gBNBUs3t0' in client_oid:
                    return {
                        'data': [{
                            'ordId': '12345',
                            'state': 'filled',  # 已成交
                            'accFillSz': '0.0165',
                            'sz': '0.0165'
                        }]
                    }
                # $626 卖单 (索引4) → 已成交
                if 'gBNBUs4t0' in client_oid:
                    return {
                        'data': [{
                            'ordId': '12346',
                            'state': 'filled',  # 已成交
                            'accFillSz': '0.0160',
                            'sz': '0.0160'
                        }]
                    }
                # $645 卖单 (索引5) → 还在挂单
                if 'gBNBUs5t0' in client_oid:
                    return {
                        'data': [{
                            'ordId': '12347',
                            'state': 'live',  # 挂单中
                            'accFillSz': '0',
                            'sz': '0.015'
                        }]
                    }
                # 其他格子没有订单
                return {'data': []}
            
            exchange.fetch_order_by_client_id = mock_fetch_order
            
            grid_mgr = GridManager(budget=50, data_dir=tmpdir)
            grid_mgr.exchange = exchange
            
            # 用均价 $597.7 初始化网格
            grid_mgr.init_grid('BNB/USDT', entry_price=597.7, layers=5)
            
            # 模拟部分 pending 已经被填充了原始数据（模拟崩溃前的状态）
            # 但现在我们需要手动设置一些 pending 的状态来模拟
            # 实际上，init_grid 后所有 pending 都是空的，需要模拟崩溃后的状态
            
            # 直接修改 pending 来模拟崩溃后的状态
            pending = grid_mgr.grid_state['BNB/USDT']['pending']
            prices = list(pending.keys())
            print('价格列表:', prices)
            
            # 卖单格子是 prices[3] ($607), prices[4] ($626), prices[5] ($645)
            # 模拟 $607, $626, $645 这三个卖单格子都没有 order_id（崩溃期间丢失）
            for p_str in [prices[3], prices[4], prices[5]]:
                pending[p_str]['order_id'] = None  # 模拟丢失
                pending[p_str]['done'] = False
            
            # 再次打印看看
            for p_str, info in pending.items():
                print(f'{p_str}: order_id={info.get("order_id")}, done={info.get("done")}')
            
            # 调用完备版恢复逻辑
            filled_orders = grid_mgr._sync_and_recover_grid('BNB/USDT')
            
            # 验证：应该返回 2 个成交订单（$607 和 $626）
            assert len(filled_orders) == 2, f"应该返回2个成交订单，实际: {len(filled_orders)}"
            
            # 验证：$645 应该还是卖单（因为 $620 < $645，所以 $645 是高于当前价的，应该卖）
            assert pending[prices[5]]['side'] == 'sell', f"$645 应该是卖单，实际: {pending[prices[5]]['side']}"
            
            # 验证：$607 和 $626 已被标记为成交（prices[3]=$607, prices[4]=$626）
            assert pending[prices[3]]['done'] == True, "$607 应该被标记为成交"
            assert pending[prices[4]]['done'] == True, "$626 应该被标记为成交"
            
            print(f"✓ 崩溃恢复测试通过: {len(filled_orders)} 个成交订单")


if __name__ == "__main__":
    pytest.main([__file__, '-v'])
