#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一状态管理器
================
合并 positions.json 和 grid_state.json 为单一的 state.json
确保持仓和网格数据的一致性
"""

import json
import os
import time
import logging
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StateManager:
    """
    统一状态管理器
    
    功能：
    - 统一存储持仓和网格状态
    - 版本控制
    - 自动备份
    - 数据校验
    """
    
    def __init__(self, data_dir: str = 'data', filename: str = 'state.json'):
        self.data_dir = data_dir
        self.filepath = os.path.join(data_dir, filename)
        self.version = "2"
        
        # 确保目录存在
        os.makedirs(data_dir, exist_ok=True)
        
        # 内存中的状态
        self.data = {
            "version": self.version,
            "positions": {},      # 持仓数据
            "grid": {},           # 网格数据
            "meta": {
                "created_at": None,
                "updated_at": None
            }
        }
        
        # 加载现有状态
        self.load()
    
    def load(self) -> bool:
        """从文件加载状态"""
        if not os.path.exists(self.filepath):
            logger.info(f"[State] 新建状态文件: {self.filepath}")
            return False
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
            
            # 验证版本
            if self.data.get("version") != self.version:
                logger.warning(f"[State] 版本不匹配: {self.data.get('version')} -> {self.version}")
            
            logger.info(f"[State] 已加载: {self.filepath}")
            return True
        except json.JSONDecodeError as e:
            logger.error(f"[State] JSON 解析错误: {e}")
            return self._restore_backup()
        except Exception as e:
            logger.error(f"[State] 加载失败: {e}")
            return False
    
    def save(self) -> bool:
        """保存状态到文件"""
        # 更新元数据
        self.data["meta"]["updated_at"] = time.time()
        
        # 写入临时文件（原子操作）
        temp_path = self.filepath + ".tmp"
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            
            # 重命名完成写入
            os.replace(temp_path, self.filepath)
            
            # 同时创建备份
            self._create_backup()
            
            logger.debug(f"[State] 已保存: {self.filepath}")
            return True
        except Exception as e:
            logger.error(f"[State] 保存失败: {e}")
            return False
    
    def _create_backup(self):
        """创建备份"""
        backup_path = self.filepath + ".bak"
        try:
            with open(backup_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[State] 备份失败: {e}")
    
    def _restore_backup(self) -> bool:
        """从备份恢复"""
        backup_path = self.filepath + ".bak"
        if not os.path.exists(backup_path):
            logger.error(f"[State] 无备份可恢复")
            return False
        
        try:
            with open(backup_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
            logger.info(f"[State] 已从备份恢复: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"[State] 恢复失败: {e}")
            return False
    
    def get_positions(self) -> Dict:
        """获取所有持仓"""
        return self.data.get("positions", {})
    
    def set_positions(self, positions: Dict):
        """设置持仓"""
        self.data["positions"] = positions
        self.save()
    
    def get_grid(self) -> Dict:
        """获取网格状态"""
        return self.data.get("grid", {})
    
    def set_grid(self, grid: Dict):
        """设置网格状态"""
        self.data["grid"] = grid
        self.save()
    
    def clear_all(self):
        """清空所有状态"""
        self.data = {
            "version": self.version,
            "positions": {},
            "grid": {},
            "meta": {
                "created_at": None,
                "updated_at": time.time()
            }
        }
        self.save()
        logger.info("[State] 所有状态已清空")
    
    def get_status(self) -> Dict:
        """获取状态摘要"""
        return {
            "version": self.data.get("version"),
            "positions_count": len(self.data.get("positions", {})),
            "grid_count": len(self.data.get("grid", {})),
            "updated_at": self.data.get("meta", {}).get("updated_at")
        }
