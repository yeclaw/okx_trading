#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OKX API 统一客户端 (V3.4 - 超时保护 + 重试增强版)
================
修复:
1. [CRITICAL] 添加重试机制 + 超时保护，防止网络卡住无限等待
2. [MAJOR] fetch_ohlcv 临时会话丢失代理导致国内网络超时
3. [MINOR] 增强 SSL 配置的一致性
"""

import requests
import hmac
import base64
import json
import time
import logging
import urllib3
from typing import Dict, List, Optional
from dataclasses import dataclass
from decimal import Decimal

# 抑制 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


@dataclass
class OKXConfig:
    """OKX 客户端配置"""
    api_key: str = ''
    api_secret: str = ''
    passphrase: str = ''
    proxy: Dict = None
    okx_ips: List[str] = None
    verify_ssl: bool = False
    timeout: int = 20  # 基础超时

    def __post_init__(self):
        if self.proxy is None:
            self.proxy = {}
        # 自动去除首尾空格
        self.api_key = self.api_key.strip()
        self.api_secret = self.api_secret.strip()
        self.passphrase = self.passphrase.strip()
        if self.okx_ips is None:
            self.okx_ips = ["www.okx.com", "43.198.202.99", "16.162.36.167", "18.167.64.66"]


class OKXClient:
    """OKX API 统一客户端 - [增强] 超时保护 + 重试机制"""

    def __init__(self, config: Optional[OKXConfig] = None):
        self.config = config or OKXConfig()

        # 超时配置 (读/写分开)
        self.read_timeout = max(self.config.timeout, 10)  # 至少10秒
        self.write_timeout = max(self.config.timeout * 2, 20)  # 写操作更长

        # 重试配置
        self.max_retries = 3
        self.retry_delay = 2

        # 1. 创建 Session (直连，不预设代理)
        self._session = requests.Session()
        self._session.verify = self.config.verify_ssl
        self._session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'QuantBot/3.4'
        })

        # 2. 查找可达 IP (优先数字 IP)
        self._ip = self._find_available_ip()
        self._base_url = f'https://{self._ip}'

        # 3. 同步时间
        self.time_offset = 0
        self._sync_time()

        # 4. 代理设置 (可选，如果配置了代理且可用)
        if self.config.proxy:
            try:
                # 快速测试代理
                test_url = f'{self._base_url}/api/v5/public/time'
                self._session.get(test_url, timeout=5)
                self._session.proxies.update(self.config.proxy)
                logger.info(f"[OKX] 已启用代理: {self.config.proxy}")
            except Exception:
                logger.warning(f"[OKX] 代理不可用，使用直连")
                self.config.proxy = None

        logger.info(f"[OKX] 初始化完成 | IP: {self._ip} | Offset: {self.time_offset}ms")

    def _find_available_ip(self) -> str:
        """查找可达的 OKX IP"""
        for ip in self.config.okx_ips:
            try:
                test_url = f'https://{ip}/api/v5/public/time'
                self._session.get(test_url, headers={'Host': 'www.okx.com'}, timeout=5)
                return ip
            except Exception:
                continue
        return self.config.okx_ips[0]

    def _sync_time(self):
        """同步服务器时间"""
        try:
            url = f'{self._base_url}/api/v5/public/time'
            headers = {'Host': 'www.okx.com'}
            t1 = time.time()
            response = self._session.get(url, headers=headers, timeout=10)
            t2 = time.time()
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '0':
                    server_ts = int(data['data'][0]['ts'])
                    latency = (t2 - t1) * 1000 / 2
                    local_ts = int(t2 * 1000)
                    self.time_offset = int(server_ts - local_ts + latency)
                    logger.info(f"[Time] Server: {server_ts}, Offset: {self.time_offset}ms")
            else:
                logger.warning(f"[Time] HTTP Error: {response.status_code}")
        except Exception as e:
            logger.error(f"[Time] Sync Failed: {e}")

    def _get_timestamp(self) -> str:
        """获取修正后的时间戳 (ISO 8601 格式，OKX 要求)"""
        # OKX API 要求 ISO 8601 格式: YYYY-MM-DDThh:mm:ss.sssZ
        from datetime import datetime, timezone
        ts_ms = int(time.time() * 1000) + self.time_offset
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    def _sign(self, timestamp: str, method: str, endpoint: str, body: str = '') -> str:
        """生成签名"""
        message = timestamp + method + endpoint + body
        mac = hmac.new(
            bytes(self.config.api_secret, encoding='utf-8'),
            bytes(message, encoding='utf-8'),
            digestmod='sha256'
        )
        return base64.b64encode(mac.digest()).decode('utf-8')

    def _request_with_retry(self, method: str, endpoint: str, data: str = None, 
                            is_write: bool = False, retry_count: int = 0) -> Dict:
        """发送请求 - [增强] 自动重试 + 超时保护"""
        url = f'{self._base_url}{endpoint}'
        headers = {'Host': 'www.okx.com'}
        timeout = self.write_timeout if is_write else self.read_timeout

        if self.config.api_key:
            ts = self._get_timestamp()
            sign_body = data if data else ''
            signature = self._sign(ts, method.upper(), endpoint, sign_body)
            headers.update({
                'OK-ACCESS-KEY': self.config.api_key,
                'OK-ACCESS-SIGN': signature,
                'OK-ACCESS-TIMESTAMP': ts,
                'OK-ACCESS-PASSPHRASE': self.config.passphrase,
            })

        try:
            if method.upper() == 'GET':
                response = self._session.get(url, headers=headers, timeout=timeout)
            else:
                response = self._session.post(url, data=data, headers=headers, timeout=timeout)
            res_json = response.json()

            # [新增] 自动处理时间戳错误 (Code 50112 = Timestamp error)
            if res_json.get('code') == '50112':
                logger.warning(f"[OKX] 时间戳过期，重新同步时间...")
                self._sync_time()
                return {'code': '-1', 'msg': 'Timestamp error, synced time'}

            return res_json

        except requests.exceptions.Timeout:
            logger.warning(f"[OKX] 请求超时 ({timeout}s): {endpoint}")
            if retry_count < self.max_retries:
                logger.info(f"[OKX] 重试 {retry_count + 1}/{self.max_retries} ...")
                time.sleep(self.retry_delay * (retry_count + 1))  # 指数退避
                return self._request_with_retry(method, endpoint, data, is_write, retry_count + 1)
            return {'code': '-1', 'msg': f'Timeout after {self.max_retries} retries'}

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[OKX] 连接错误: {endpoint} - {e}")
            if retry_count < self.max_retries:
                logger.info(f"[OKX] 重试 {retry_count + 1}/{self.max_retries} ...")
                time.sleep(self.retry_delay * (retry_count + 1))
                return self._request_with_retry(method, endpoint, data, is_write, retry_count + 1)
            return {'code': '-1', 'msg': f'Connection error after {self.max_retries} retries'}

        except Exception as e:
            logger.error(f"[OKX] Request Failed: {e}")
            return {'code': '-1', 'msg': str(e)}

    def _request(self, method: str, endpoint: str, data: str = None) -> Dict:
        """发送请求 - 旧接口兼容"""
        return self._request_with_retry(method, endpoint, data)

    def _to_str(self, value) -> str:
        """安全地将数字转换为字符串 (避免科学计数法)"""
        if value is None:
            return ""
        # 使用 format 避免 1e-05 这种格式
        # .12f 足够覆盖加密货币的精度 (如 SHIB)
        s = "{:.12f}".format(float(value))
        return s.rstrip('0').rstrip('.') if '.' in s else s

    # ==================== 公开 API ====================

    def fetch_ticker(self, symbol: str) -> Dict:
        instId = symbol.replace('/', '-')
        data = self._request('GET', f'/api/v5/market/ticker?instId={instId}')
        if data.get('code') == '0' and data.get('data'):
            try:
                last_price = data['data'][0]['last']
                if not last_price:
                    return {'error': 'Empty price data'}
                return {'last': float(last_price)}
            except ValueError:
                logger.error(f"Ticker price parse error: {data['data'][0]}")
                return {'error': 'Parse error'}
        return {'error': data.get('msg', 'Unknown')}

    def fetch_balance(self) -> Dict:
        return self._request('GET', '/api/v5/account/balance')

    def fetch_open_orders(self, symbol: str) -> List[Dict]:
        instId = symbol.replace('/', '-')
        data = self._request('GET', f'/api/v5/trade/orders-pending?instId={instId}')
        if data.get('code') == '0':
            return data.get('data', [])
        return []

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> List:
        """
        获取K线数据
        [修复] 临时 Session 必须继承主 Session 的代理配置，否则国内网络无法连接
        [增强] 添加超时 + 重试
        """
        instId = symbol.replace('/', '-')
        bar_map = {'1h': '1H', '4h': '4H', '1d': '1D'}
        bar = bar_map.get(timeframe, '1H')

        # 创建新 Session 避免缓存，但必须继承配置
        temp_session = requests.Session()
        temp_session.verify = self.config.verify_ssl  # 继承 SSL 配置
        
        if self.config.proxy:
            temp_session.proxies.update(self.config.proxy)  # [关键修复] 继承代理

        url = f'{self._base_url}/api/v5/market/candles?instId={instId}&bar={bar}&limit={limit}'
        headers = {'Host': 'www.okx.com'}

        for retry in range(self.max_retries):
            try:
                resp = temp_session.get(url, headers=headers, timeout=self.read_timeout)
                data = resp.json()
                break
            except requests.exceptions.Timeout:
                logger.warning(f"fetch_ohlcv 超时 (尝试 {retry + 1}/{self.max_retries}): {symbol}")
                if retry < self.max_retries - 1:
                    time.sleep(self.retry_delay * (retry + 1))
                data = None
            except Exception as e:
                logger.warning(f"fetch_ohlcv 错误: {e}")
                data = None
                break
        else:
            # 所有重试都失败
            temp_session.close()
            return []

        temp_session.close()  # 显式关闭

        if data and data.get('code') == '0' and data.get('data'):
            ohlcv = []
            for row in data['data']:
                ts = int(row[0]) / 1000
                ohlcv.append([ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])])
            return ohlcv
        return []

    def create_order(self, symbol: str, side: str, amount: float,
                    order_type: str = 'market', price: float = None,
                    clOrdId: str = None) -> Dict:
        """
        创建订单
        [修复1] 添加 clOrdId 参数防止重复下单
        [修复2] 使用 _to_str 避免科学计数法 (如 1e-05) 导致 API 报错
        """
        instId = symbol.replace('/', '-')
        params = {
            'instId': instId,
            'tdMode': 'cash',
            'side': side,
            'ordType': order_type,
            'sz': self._to_str(amount)  # 修复科学计数法
        }
        if order_type == 'limit' and price:
            params['px'] = self._to_str(price)  # 修复科学计数法
        if order_type == 'market':
            params['tgtCcy'] = 'quote_ccy' if side == 'buy' else 'base_ccy'
        if clOrdId:
            params['clOrdId'] = clOrdId

        # 紧凑 JSON
        data = json.dumps(params, separators=(',', ':'))
        return self._request_with_retry('POST', '/api/v5/trade/order', data=data, is_write=True)

    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        instId = symbol.replace('/', '-')
        params = {'instId': instId, 'ordId': order_id}
        data = json.dumps(params, separators=(',', ':'))
        return self._request_with_retry('POST', '/api/v5/trade/cancel-order', data=data, is_write=True)

    def get_order(self, symbol: str, order_id: str) -> Dict:
        instId = symbol.replace('/', '-')
        return self._request('GET', f'/api/v5/trade/order?instId={instId}&ordId={order_id}')
    
    def fetch_order_history(self, symbol: str, limit: int = 50) -> Dict:
        """查询订单历史（已成交/已取消的订单）"""
        instId = symbol.replace('/', '-')
        return self._request('GET', f'/api/v5/trade/orders-history?instType=SPOT&instId={instId}&limit={limit}')

    def get_instruments(self, inst_type: str = 'SPOT') -> List[Dict]:
        data = self._request('GET', f'/api/v5/public/instruments?instType={inst_type}')
        if data.get('code') == '0' and data.get('data'):
            return data['data']
        return []

    def fetch_positions(self) -> List[Dict]:
        """获取现货持仓 - 通过账户余额获取"""
        data = self._request('GET', '/api/v5/account/balance')
        
        if data.get('code') != '0' or not data.get('data'):
            return []
        
        try:
            details = data['data'][0].get('details', [])
            positions = []
            
            for item in details:
                # OKX 现货返回字段是 ccy (不是 currency)
                # bal 可能是 null，需要用 eq 或 cashBal+spotBal 计算总量
                ccy = item.get('ccy')
                if not ccy:
                    continue
                    
                avail = item.get('availBal', '0')
                frozen = item.get('frozenBal', '0')
                
                # 优先用 eq（总权益），其次用 cashBal
                total = float(item.get('eq', 0) or item.get('cashBal', 0) or 0)
                
                if total > 0:  # 只返回有余额的币种
                    positions.append({
                        'symbol': ccy,
                        'available': avail,
                        'frozen': frozen,
                        'total': str(round(total, 8))
                    })
            
            return positions
        except (KeyError, IndexError, ValueError) as e:
            logger.error(f"[OKX] 解析持仓失败: {e}")
            return []


# ==================== 全局单例 ====================
_okx_client: Optional[OKXClient] = None


def get_client(config: Optional[OKXConfig] = None) -> OKXClient:
    global _okx_client
    if _okx_client is None:
        if config is None:
            try:
                from config import OKX_CONFIG
                config = OKXConfig(
                    api_key=OKX_CONFIG.get('api_key', ''),
                    api_secret=OKX_CONFIG.get('api_secret', ''),
                    passphrase=OKX_CONFIG.get('passphrase', ''),
                    proxy=OKX_CONFIG.get('proxies'),
                    verify_ssl=OKX_CONFIG.get('verify_ssl', False),
                    timeout=OKX_CONFIG.get('timeout', 20)
                )
            except ImportError:
                config = OKXConfig()
        _okx_client = OKXClient(config)
    return _okx_client


def reset_client():
    global _okx_client
    _okx_client = None
