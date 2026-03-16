#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 机器人状态检查脚本 ⭐

功能：
- 检查进程是否存在
- 检查日志更新时间
- 计算时间差，判断是否卡住
- 检查 API 连接

使用方法：
    python3 scripts/status.py --quick    # 快速检查
    python3 scripts/status.py --deep     # 深度检查（推荐）
    python3 scripts/status.py --api      # 只检查 API
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import subprocess
import time
from datetime import datetime

# 颜色输出
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


def print_status(msg, status='OK'):
    """带状态的打印"""
    colors = {
        'OK': GREEN,
        'WARN': YELLOW,
        'FAIL': RED,
        'INFO': BLUE,
    }
    color = colors.get(status, RESET)
    print(f"{color}[{status}]{RESET} {msg}")


def check_process():
    """检查进程是否存在"""
    result = subprocess.run(['pgrep', '-f', 'rsi_grid/main.py'], 
                          capture_output=True)
    if not result.stdout:
        return None, []
    
    pids = result.stdout.strip().decode().split('\n')
    return pids[0], pids


def check_log_freshness(log_path='logs/trading.log', expected_interval=900):
    """
    检查日志新鲜度
    
    Args:
        log_path: 日志文件路径
        expected_interval: 预期扫描间隔（秒）
    
    Returns:
        (freshness, details)
        freshness: True=正常, False=卡住, None=未知
    """
    if not os.path.exists(log_path):
        return None, "日志文件不存在"
    
    try:
        with open(log_path) as f:
            last_line = f.readlines()[-1]
        
        # 解析日志时间: '2026-02-13 14:43:17,353'
        try:
            log_time_str = last_line.split(' - ')[0].split(',')[0]
            log_time = datetime.strptime(log_time_str, '%Y-%m-%d %H:%M:%S')
        except (IndexError, ValueError) as e:
            return None, f"日志格式错误: {e}"
        
        now = datetime.now()
        age_seconds = (now - log_time).total_seconds()
        age_minutes = age_seconds / 60
        
        # 判断是否卡住
        # 预期扫描间隔 + 2 分钟容错
        tolerance = expected_interval + 120
        
        if age_seconds > tolerance:
            return False, {
                'log_time': log_time_str,
                'age_minutes': round(age_minutes, 1),
                'expected_interval': expected_interval,
                'tolerance': tolerance,
            }
        else:
            return True, {
                'log_time': log_time_str,
                'age_minutes': round(age_minutes, 1),
                'expected_interval': expected_interval,
            }
    
    except Exception as e:
        return None, f"读取日志失败: {e}"


def check_api():
    """检查 API 连接"""
    try:
        from okx_client import get_client
        client = get_client()
        
        # 检查时间同步
        ts = client._request('GET', '/api/v5/public/time')
        if ts.get('code') != '0':
            return False, "时间同步失败"
        
        # 检查余额
        balance = client.fetch_balance()
        if balance.get('code') != '0':
            return False, "余额查询失败"
        
        # 检查 K 线
        ohlcv = client.fetch_ohlcv('BTC/USDT', limit=10)
        if not ohlcv:
            return False, "K线获取失败"
        
        return True, {
            'offset_ms': client.time_offset,
            'balance_ok': True,
            'ohlcv_ok': True,
        }
    
    except ImportError as e:
        return None, f"导入失败: {e}"
    except Exception as e:
        return False, f"API 错误: {e}"


def check_quick():
    """快速检查"""
    print(f"\n{'=' * 60}")
    print("RSI 机器人快速检查")
    print('=' * 60)
    
    # 1. 检查进程
    pid, pids = check_process()
    if not pid:
        print_status("进程不存在", 'FAIL')
        print("请启动机器人: python3 robots/rsi_grid/main.py")
        return False
    
    print(f"进程: PID {pid}")
    if len(pids) > 1:
        print(f"警告: 发现 {len(pids)} 个进程: {pids}")
    
    # 2. 检查日志
    fresh, details = check_log_freshness()
    
    if fresh is None:
        print_status(f"日志检查失败: {details}", 'WARN')
    elif fresh:
        print_status(f"日志正常: {details['log_time']} ({details['age_minutes']} 分钟前)", 'OK')
    else:
        print_status(f"日志卡住: {details['log_time']} ({details['age_minutes']} 分钟前)", 'FAIL')
        print(f"预期间隔: {details['expected_interval']} 秒")
        print(f"容错: {details['tolerance']} 秒")
        return False
    
    print('=' * 60)
    return True


def check_deep():
    """深度检查 - 完整健康检查"""
    print(f"\n{'=' * 60}")
    print("RSI 机器人深度健康检查")
    print('=' * 60)
    
    all_ok = True
    
    # 1. 检查进程
    print("\n[1/4] 进程检查")
    print("-" * 40)
    pid, pids = check_process()
    if not pid:
        print_status("进程不存在", 'FAIL')
        all_ok = False
    else:
        print_status(f"进程运行中: PID {pid}", 'OK')
        
        # 进程状态
        ps_result = subprocess.run(['ps', '-p', pid, '-o', 'state,etime,time'], 
                                  capture_output=True)
        if ps_result.stdout:
            lines = ps_result.stdout.decode().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 3:
                    state = parts[0]
                    print(f"  状态: {state}")
                    if state == 'S':
                        print("  解释: 休眠中（等待下一次扫描）")
                    elif state == 'R':
                        print("  解释: 运行中")
                    elif state == 'Z':
                        print("  警告: 僵尸进程！")
                        all_ok = False
    
    # 2. 检查日志
    print("\n[2/4] 日志检查")
    print("-" * 40)
    fresh, details = check_log_freshness()
    
    if fresh is None:
        print_status(f"检查失败: {details}", 'WARN')
    elif fresh:
        print_status(f"日志正常", 'OK')
        print(f"  最后更新: {details['log_time']}")
        print(f"  距今: {details['age_minutes']} 分钟")
        print(f"  扫描周期: {details['expected_interval']} 秒")
    else:
        print_status(f"日志卡住!", 'FAIL')
        print(f"  最后更新: {details['log_time']}")
        print(f"  距今: {details['age_minutes']} 分钟")
        print(f"  预期周期: {details['expected_interval']} 秒")
        print(f"  容错: {details['tolerance']} 秒")
        all_ok = False
    
    # 3. 检查 API
    print("\n[3/4] API 检查")
    print("-" * 40)
    api_ok, api_details = check_api()
    
    if api_ok:
        print_status("API 连接正常", 'OK')
        if isinstance(api_details, dict):
            print(f"  时间偏移: {api_details.get('offset_ms', 'N/A')} ms")
    elif api_ok is None:
        print_status(f"API 检查跳过: {api_details}", 'WARN')
    else:
        print_status(f"API 异常: {api_details}", 'FAIL')
        all_ok = False
    
    # 4. 持仓盈亏
    print("\n[4/4] 持仓盈亏")
    print("-" * 40)
    try:
        from core.state_manager import StateManager
        from okx_client import get_client
        
        state_mgr = StateManager()
        client = get_client()
        state = state_mgr.get_grid()
        # StateManager 直接返回 grid 数据
        grid = state if state else {}
        
        # 从 OKX API 获取真实持仓数据（包含均价和未实现盈亏）
        api_positions = {}
        try:
            balance_resp = client._request('GET', '/api/v5/account/balance')
            if balance_resp.get('code') == '0' and balance_resp.get('data'):
                bal_data = balance_resp['data'][0]
                details = bal_data.get('details', [])
                for item in details:
                    eq = float(item.get('eq', 0))
                    if eq > 0:
                        ccy = item.get('ccy', '')
                        api_positions[ccy] = {
                            'eq': eq,
                            'accAvgPx': float(item.get('accAvgPx') or 0),  # 账户均价
                            'spotUpl': float(item.get('spotUpl') or 0),  # 未实现盈亏
                            'spotUplRatio': float(item.get('spotUplRatio') or 0)  # 盈亏比例
                        }
                print_status(f"已获取真实持仓: {len(api_positions)} 个币种", 'INFO')
        except Exception as e:
            print_status(f"获取真实持仓失败，使用缓存: {e}", 'WARN')
        
        # 获取所有持仓的币种
        for symbol, data in grid.items():
            trade_count = data.get('trade_count', 0)
            realized_pnl = data.get('realized_pnl', 0)
            
            # 解析交易对获取币种符号 (如 DOGE-USDT -> DOGE)
            base_ccy = symbol.split('/')[0] if '/' in symbol else symbol
            
            # 使用 API 返回的真实数据
            api_data = api_positions.get(base_ccy, {})
            position_size = api_data.get('eq') or data.get('position_size', 0)
            entry_price = api_data.get('accAvgPx') or data.get('entry_price') or data.get('avg_price')
            unrealized_pnl = api_data.get('spotUpl')
            unrealized_pnl_pct = api_data.get('spotUplRatio')
            
            # 获取实时现价
            market_price = None
            try:
                ticker = client.fetch_ticker(symbol)
                if 'error' in ticker:
                    market_price = data.get('market_price', 0)
                    print_status(f"获取 {symbol} 现价失败: {ticker['error']}", 'WARN')
                else:
                    market_price = float(ticker['last'])
            except Exception as e:
                market_price = data.get('market_price', 0)
                if not market_price:
                    print_status(f"获取 {symbol} 现价失败: {e}", 'WARN')
            
            if position_size and market_price:
                position_value = position_size * market_price
                
                # 如果API有未实现盈亏直接用，否则自行计算
                if unrealized_pnl is None or unrealized_pnl == 0:
                    if entry_price and float(entry_price) > 0:
                        unrealized_pnl = position_size * (market_price - float(entry_price))
                        unrealized_pnl_pct = (market_price - float(entry_price)) / float(entry_price) * 100
                    else:
                        unrealized_pnl = 0
                        unrealized_pnl_pct = 0
                
                print(f"  {symbol}:")
                print(f"    持仓: {position_size:.6f} {base_ccy} (${position_value:.2f})")
                print(f"    均价: ${entry_price:.6f}" if entry_price else "    均价: N/A")
                print(f"    现价: ${market_price}")
                print(f"    未实现盈亏: {unrealized_pnl:+.2f} ({unrealized_pnl_pct*100:+.2f}%)" if unrealized_pnl_pct else f"    未实现盈亏: {unrealized_pnl:+.2f}")
                print(f"    已实现盈亏: ${realized_pnl:+.2f}")
                print(f"    成交次数: {trade_count}")
            else:
                print(f"  {symbol}: 无持仓数据")
    except Exception as e:
        print_status(f"盈亏计算失败: {e}", 'WARN')
    
    # 5. 总结
    print("\n[5/5] 健康评估")
    print("-" * 40)
    
    if all_ok:
        print_status("机器人运行正常 ✅", 'OK')
    else:
        print_status("机器人存在异常 ❌", 'FAIL')
        print("\n建议操作:")
        if not pid:
            print("  1. 启动机器人")
        elif not fresh:
            print("  1. 重启机器人: pkill -f 'rsi_grid/main.py'")
            print("  2. 检查网络连接")
            print("  3. 查看日志: tail -f logs/trading.log")
    
    print('=' * 60)
    return all_ok


def check_api_only():
    """只检查 API"""
    print(f"\n{'=' * 60}")
    print("API 连接检查")
    print('=' * 60)
    
    ok, details = check_api()
    
    if ok:
        print_status("API 连接正常", 'OK')
        if isinstance(details, dict):
            print(f"  时间偏移: {details.get('offset_ms', 'N/A')} ms")
    elif ok is None:
        print_status(f"检查失败: {details}", 'WARN')
    else:
        print_status(f"API 异常: {details}", 'FAIL')
    
    print('=' * 60)
    return ok


def main():
    parser = argparse.ArgumentParser(description='RSI 机器人状态检查')
    parser.add_argument('--quick', action='store_true', help='快速检查')
    parser.add_argument('--deep', action='store_true', help='深度检查（推荐）')
    parser.add_argument('--api', action='store_true', help='只检查 API')
    
    args = parser.parse_args()
    
    # 默认深度检查
    if not (args.quick or args.deep or args.api):
        args.deep = True
    
    if args.quick:
        check_quick()
    elif args.deep:
        check_deep()
    elif args.api:
        check_api_only()


if __name__ == '__main__':
    main()
