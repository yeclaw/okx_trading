#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓管理器适配器
===============
通过 StateManager 统一存储持仓数据
"""

import json
import os
import time
from typing import Dict, Optional
from datetime import datetime


def load_positions(state_manager) -> Dict:
    """从 StateManager 加载持仓"""
    return state_manager.get_positions()


def save_positions(state_manager, positions: Dict):
    """保存持仓到 StateManager"""
    # 转换 Position 对象为字典
    data = {}
    for k, v in positions.items():
        if hasattr(v, 'to_dict'):
            data[k] = v.to_dict()
        else:
            data[k] = v
    state_manager.set_positions(data)


def load_grid_state(state_manager) -> Dict:
    """从 StateManager 加载网格状态"""
    return state_manager.get_grid()


def save_grid_state(state_manager, grid_state: Dict):
    """保存网格状态到 StateManager"""
    # 移除 prices_set（set 对象不可 JSON 序列化）
    clean_state = {}
    for symbol, state in grid_state.items():
        state_copy = state.copy()
        if 'prices_set' in state_copy:
            del state_copy['prices_set']
        clean_state[symbol] = state_copy
    state_manager.set_grid(clean_state)
