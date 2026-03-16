#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OKX fetch_positions() 测试
测试 OKXClient.fetch_positions() 方法的正确性
"""

import sys
sys.path.insert(0, '/root/clawd/okx_trading')

import pytest
from unittest.mock import Mock, patch, MagicMock
from okx_client import OKXClient, OKXConfig


class TestFetchPositions:
    """fetch_positions() 单元测试"""

    def _create_mock_response(self, data):
        """创建模拟的 API 响应"""
        mock_response = Mock()
        mock_response.json.return_value = data
        return mock_response

    @patch('okx_client.requests.Session')
    def test_fetch_positions_empty(self, mock_session_class):
        """测试无持仓时返回空列表"""
        # Mock API 返回空 details
        mock_response = {
            'code': '0',
            'data': [{
                'balance': '0',
                'details': []
            }]
        }
        
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        
        # Mock get 方法返回模拟响应
        mock_session.get.return_value.json.return_value = mock_response
        mock_session.get.return_value.status_code = 200
        
        client = OKXClient()
        client._session = mock_session
        
        result = client.fetch_positions()
        
        assert isinstance(result, list)
        assert len(result) == 0

    @patch('okx_client.requests.Session')
    def test_fetch_positions_single(self, mock_session_class):
        """测试单币种持仓解析"""
        mock_response = {
            'code': '0',
            'data': [{
                'balance': '1.5',
                'details': [
                    {
                        'ccy': 'BTC',
                        'eq': '1.5',
                        'availBal': '1.5',
                        'frozenBal': '0'
                    }
                ]
            }]
        }
        
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value.json.return_value = mock_response
        mock_session.get.return_value.status_code = 200
        
        client = OKXClient()
        client._session = mock_session
        
        result = client.fetch_positions()
        
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]['symbol'] == 'BTC'
        assert result[0]['available'] == '1.5'
        assert result[0]['frozen'] == '0'
        assert result[0]['total'] == '1.5'

    @patch('okx_client.requests.Session')
    def test_fetch_positions_multiple(self, mock_session_class):
        """测试多币种持仓解析"""
        mock_response = {
            'code': '0',
            'data': [{
                'balance': '10.5',
                'details': [
                    {
                        'ccy': 'BTC',
                        'eq': '0.5',
                        'availBal': '0.5',
                        'frozenBal': '0'
                    },
                    {
                        'ccy': 'ETH',
                        'eq': '10',
                        'availBal': '8',
                        'frozenBal': '2'
                    },
                    {
                        'ccy': 'USDT',
                        'eq': '1000',
                        'availBal': '1000',
                        'frozenBal': '0'
                    }
                ]
            }]
        }
        
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value.json.return_value = mock_response
        mock_session.get.return_value.status_code = 200
        
        client = OKXClient()
        client._session = mock_session
        
        result = client.fetch_positions()
        
        assert isinstance(result, list)
        assert len(result) == 3
        
        symbols = [p['symbol'] for p in result]
        assert 'BTC' in symbols
        assert 'ETH' in symbols
        assert 'USDT' in symbols
        
        # 验证 ETH 有冻结余额
        eth_pos = next(p for p in result if p['symbol'] == 'ETH')
        assert eth_pos['frozen'] == '2'

    @patch('okx_client.requests.Session')
    def test_fetch_positions_filters_zero(self, mock_session_class):
        """测试过滤零余额"""
        mock_response = {
            'code': '0',
            'data': [{
                'balance': '10',
                'details': [
                    {
                        'ccy': 'BTC',
                        'eq': '0.5',
                        'availBal': '0.5',
                        'frozenBal': '0'
                    },
                    {
                        'ccy': 'ETH',
                        'eq': '0',  # 零余额
                        'availBal': '0',
                        'frozenBal': '0'
                    },
                    {
                        'ccy': 'USDT',
                        'eq': '9.5',
                        'availBal': '9.5',
                        'frozenBal': '0'
                    }
                ]
            }]
        }
        
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value.json.return_value = mock_response
        mock_session.get.return_value.status_code = 200
        
        client = OKXClient()
        client._session = mock_session
        
        result = client.fetch_positions()
        
        # 应该只有 BTC 和 USDT，ETH 被过滤
        assert len(result) == 2
        symbols = [p['symbol'] for p in result]
        assert 'BTC' in symbols
        assert 'USDT' in symbols
        assert 'ETH' not in symbols

    @patch('okx_client.requests.Session')
    def test_fetch_positions_api_error(self, mock_session_class):
        """测试 API 返回错误码"""
        mock_response = {
            'code': '50001',
            'msg': 'Internal error'
        }
        
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value.json.return_value = mock_response
        mock_session.get.return_value.status_code = 200
        
        client = OKXClient()
        client._session = mock_session
        
        result = client.fetch_positions()
        
        assert isinstance(result, list)
        assert len(result) == 0

    @patch('okx_client.requests.Session')
    def test_fetch_positions_no_data(self, mock_session_class):
        """测试 API 返回空数据"""
        mock_response = {
            'code': '0',
            'data': []
        }
        
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value.json.return_value = mock_response
        mock_session.get.return_value.status_code = 200
        
        client = OKXClient()
        client._session = mock_session
        
        result = client.fetch_positions()
        
        assert isinstance(result, list)
        assert len(result) == 0

    @patch('okx_client.requests.Session')
    def test_fetch_positions_missing_keys(self, mock_session_class):
        """测试数据缺少必要字段"""
        mock_response = {
            'code': '0',
            'data': [{
                'balance': '1',
                'details': [
                    {
                        'ccy': 'BTC'
                        # 缺少 bal, availBal, frozenBal
                    }
                ]
            }]
        }
        
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session
        mock_session.get.return_value.json.return_value = mock_response
        mock_session.get.return_value.status_code = 200
        
        client = OKXClient()
        client._session = mock_session
        
        # 应该不抛出异常，返回空列表或跳过该记录
        result = client.fetch_positions()
        
        # 由于 bal 缺失，total 为 0，应该被过滤掉
        assert isinstance(result, list)


class TestFetchPositionsIntegration:
    """fetch_positions() 集成测试 - 调用真实 API"""

    @pytest.mark.integration
    def test_fetch_positions_integration(self):
        """集成测试 - 调用真实 API"""
        from okx_client import get_client, reset_client
        
        # 重置客户端以确保使用最新配置
        reset_client()
        client = get_client()
        
        positions = client.fetch_positions()
        
        # 验证返回类型
        assert isinstance(positions, list), f"Expected list, got {type(positions)}"
        
        # 验证数据结构
        for p in positions:
            assert 'symbol' in p, f"Missing 'symbol' in {p}"
            assert 'available' in p, f"Missing 'available' in {p}"
            assert 'frozen' in p, f"Missing 'frozen' in {p}"
            assert 'total' in p, f"Missing 'total' in {p}"
            
            # 验证类型为字符串
            assert isinstance(p['symbol'], str), f"symbol should be str, got {type(p['symbol'])}"
            assert isinstance(p['available'], str), f"available should be str, got {type(p['available'])}"
            assert isinstance(p['frozen'], str), f"frozen should be str, got {type(p['frozen'])}"
            assert isinstance(p['total'], str), f"total should be str, got {type(p['total'])}"
        
        # 打印结果供调试
        print(f"\n=== Fetch Positions Result ===")
        print(f"Total positions: {len(positions)}")
        for p in positions:
            print(f"  {p['symbol']}: available={p['available']}, frozen={p['frozen']}, total={p['total']}")
        
        # 如果有持仓，验证数据合理性
        if positions:
            for p in positions:
                # total 应该 >= 0
                total = float(p['total'])
                assert total >= 0, f"total should be >= 0, got {total}"
                
                # available 和 frozen 应该可以转换为数字
                float(p['available'])
                float(p['frozen'])


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
