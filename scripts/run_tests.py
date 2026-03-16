#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试运行脚本

用法:
    python scripts/run_tests.py           # 运行所有测试
    python scripts/run_tests.py unit     # 只运行单元测试
    python scripts/run_tests.py integration  # 只运行集成测试
    python scripts/run_tests.py -v      # 详细输出
"""

import subprocess
import sys
import os

# 确保路径正确
os.chdir('/root/clawd/okx_trading')
sys.path.insert(0, '/root/clawd/okx_trading')


def run_tests(test_type=None, verbose=False):
    """运行测试"""
    
    cmd = ['python3', '-m', 'pytest', 'tests/']
    
    if test_type == 'unit':
        cmd.append('tests/test_rsi_strategy.py')
    elif test_type == 'integration':
        cmd.append('tests/test_integration.py')
    
    if verbose:
        cmd.append('-v')
    
    # 运行
    print("="*60)
    if test_type:
        print(f"运行 {test_type} 测试...")
    else:
        print("运行所有测试...")
    print("="*60)
    
    result = subprocess.run(cmd)
    
    # 输出结果
    print()
    if result.returncode == 0:
        print("✅ 所有测试通过!")
    else:
        print("❌ 有测试失败")
    
    return result.returncode


def quick_check():
    """快速检查：确保测试文件能正常导入"""
    print("="*60)
    print("快速检查：测试文件导入...")
    print("="*60)
    
    checks = [
        ("RSI 策略", "strategies/rsi_contrarian.py"),
        ("核心模块", "core/state_manager.py"),
        ("API 客户端", "okx_client.py"),
        ("配置", "config.py"),
        ("单元测试", "tests/test_rsi_strategy.py"),
        ("集成测试", "tests/test_integration.py"),
    ]
    
    all_pass = True
    for name, path in checks:
        try:
            module_name = path.replace('/', '.').replace('.py', '')
            __import__(module_name)
            print(f"  ✅ {name}")
        except Exception as e:
            print(f"  ❌ {name}: {e}")
            all_pass = False
    
    print()
    if all_pass:
        print("✅ 所有模块导入成功")
    else:
        print("❌ 有模块导入失败")
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='测试运行脚本')
    parser.add_argument('--unit', action='store_true', help='只运行单元测试')
    parser.add_argument('--integration', action='store_true', help='只运行集成测试')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('--check', action='store_true', help='快速检查')
    
    args = parser.parse_args()
    
    if args.check:
        sys.exit(quick_check())
    
    test_type = None
    if args.unit:
        test_type = 'unit'
    elif args.integration:
        test_type = 'integration'
    
    verbose = args.verbose
    verbose = True  # 默认详细
    
    sys.exit(run_tests(test_type, verbose))
