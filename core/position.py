"""
持仓管理系统 - 核心模块
负责追踪持仓、分批建仓、网格管理
修复版：解决僵尸持仓、文件写入风险
[P1] 升级：使用 StateManager 统一存储
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from decimal import Decimal, ROUND_HALF_UP
import json
import os
import logging
from core.state_manager import StateManager

# 配置模块级logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def round_price(price: float, precision: int = 8) -> str:
    """安全的价格四舍五入"""
    d = Decimal(str(price))
    quantized = d.quantize(Decimal('1.' + '0' * precision), rounding=ROUND_HALF_UP)
    return str(quantized)


def parse_price(price_str: str) -> float:
    """将价格字符串转回浮点数"""
    return float(price_str)


@dataclass
class Batch:
    """单批建仓记录"""
    batch_id: int
    amount: float          # 数量
    price: float           # 成交价
    cost: float            # 金额 USDT
    timestamp: str
    status: str = 'filled'  # pending, filled, failed


@dataclass
class GridOrder:
    """网格订单"""
    side: str              # buy, sell
    price: float
    amount: float
    order_id: str = ''
    status: str = 'pending'  # pending, filled, cancelled
    filled_at: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """转换为字典（支持JSON序列化）"""
        return {
            'side': self.side,
            'price': self.price,
            'amount': self.amount,
            'order_id': self.order_id,
            'status': self.status,
            'filled_at': self.filled_at
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'GridOrder':
        """从字典创建"""
        return cls(
            side=data['side'],
            price=data['price'],
            amount=data['amount'],
            order_id=data.get('order_id', ''),
            status=data.get('status', 'pending'),
            filled_at=data.get('filled_at')
        )


@dataclass
class GridConfig:
    """网格配置"""
    enabled: bool = False
    upper_price: float = 0      # 网格上限
    lower_price: float = 0      # 网格下限
    step_percent: float = 0.5   # 每格百分比
    max_grids: int = 10         # 最大网格数
    sell_orders: List[GridOrder] = field(default_factory=list)
    buy_orders: List[GridOrder] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """转换为字典（支持JSON序列化）"""
        return {
            'enabled': self.enabled,
            'upper_price': self.upper_price,
            'lower_price': self.lower_price,
            'step_percent': self.step_percent,
            'max_grids': self.max_grids,
            'sell_orders': [o.to_dict() for o in self.sell_orders],
            'buy_orders': [o.to_dict() for o in self.buy_orders]
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'GridConfig':
        """从字典创建"""
        return cls(
            enabled=data.get('enabled', False),
            upper_price=data.get('upper_price', 0),
            lower_price=data.get('lower_price', 0),
            step_percent=data.get('step_percent', 0.5),
            max_grids=data.get('max_grids', 10),
            sell_orders=[GridOrder.from_dict(o) for o in data.get('sell_orders', [])],
            buy_orders=[GridOrder.from_dict(o) for o in data.get('buy_orders', [])]
        )


@dataclass
class Position:
    """持仓核心数据结构"""
    symbol: str
    status: str = 'open'      # open, closed
    total_amount: float = 0
    avg_price: float = 0
    batches: List[Batch] = field(default_factory=list)
    grid: Optional[GridConfig] = None
    stop_loss: float = 0
    take_profit: float = 0
    created_at: str = ''
    updated_at: str = ''
    note: str = ''
    highest_price: float = 0  # [Bug修复] 移动止盈用
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'symbol': self.symbol,
            'status': self.status,
            'total_amount': self.total_amount,
            'avg_price': self.avg_price,
            'batches': [
                {
                    'batch_id': b.batch_id,
                    'amount': b.amount,
                    'price': b.price,
                    'cost': b.cost,
                    'timestamp': b.timestamp,
                    'status': b.status
                } for b in self.batches
            ],
            'grid': self.grid.to_dict() if self.grid else None,
            'stop_loss': self.stop_loss,
            'take_profit': self.take_profit,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'note': self.note,
            'highest_price': self.highest_price  # [Bug修复] 移动止盈用
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        """从字典创建"""
        batches = [Batch(**b) for b in data.get('batches', [])]
        grid = None
        if data.get('grid'):
            grid = GridConfig.from_dict(data['grid'])
        
        return cls(
            symbol=data['symbol'],
            status=data.get('status', 'open'),
            total_amount=data.get('total_amount', 0),
            avg_price=data.get('avg_price', 0),
            batches=batches,
            grid=grid,
            stop_loss=data.get('stop_loss', 0),
            take_profit=data.get('take_profit', 0),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            note=data.get('note', ''),
            highest_price=data.get('highest_price', 0)  # [Bug修复] 移动止盈用
        )


class PositionManager:
    """
    持仓管理器
    
    功能：
    - 追踪持仓状态
    - 分批建仓管理
    - 网格交易管理
    - 止损止盈检查
    [P1] 升级：使用 StateManager 统一存储
    """
    
    def __init__(self, data_dir: str = 'records', 
                 max_batches: int = 2,
                 drop_threshold: float = 0.05,
                 batch_interval: int = 3600,
                 batch_amount: float = 50,
                 state_mgr: Optional[StateManager] = None):
        self.data_dir = data_dir
        self.positions: Dict[str, Position] = {}
        
        # 可选的 StateManager
        self.state_mgr = state_mgr
        
        # 可配置的策略参数（默认配置）
        self.max_batches = max_batches          # 最多几批
        self.drop_threshold = drop_threshold    # 跌多少%才补仓
        self.batch_interval = batch_interval    # 补仓间隔(秒)
        self.batch_amount = batch_amount        # 每批金额（默认$50）
        
        self.load()
    
    def load(self):
        """从 StateManager 或文件加载"""
        # 优先从 StateManager 加载
        if self.state_mgr:
            data = self.state_mgr.get_positions()
            for k, v in data.items():
                self.positions[k] = Position.from_dict(v)
            return
        
        # 回退到文件加载（兼容旧代码）
        try:
            path = f'{self.data_dir}/positions.json'
            with open(path, 'r') as f:
                data = json.load(f)
                for k, v in data.items():
                    self.positions[k] = Position.from_dict(v)
        except FileNotFoundError:
            self.positions = {}
        except json.JSONDecodeError:
            self._restore_from_backup()
    
    def _restore_from_backup(self):
        """从备份恢复"""
        backup_path = f'{self.data_dir}/positions.json.bak'
        if os.path.exists(backup_path):
            try:
                with open(backup_path, 'r') as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self.positions[k] = Position.from_dict(v)
                print(f"✅ 已从备份恢复 {len(self.positions)} 个持仓")
            except:
                self.positions = {}
        else:
            self.positions = {}
    
    def _atomic_save(self):
        """
        原子写入：先写临时文件，再重命名
        防止写入过程中崩溃导致数据损坏
        """
        os.makedirs(self.data_dir, exist_ok=True)
        path = f'{self.data_dir}/positions.json'
        temp_path = f'{self.data_dir}/positions.json.tmp'
        backup_path = f'{self.data_dir}/positions.json.bak'
        
        # 先备份旧文件
        if os.path.exists(path):
            import shutil
            shutil.copy2(path, backup_path)
        
        # 写入临时文件
        with open(temp_path, 'w') as f:
            json.dump({k: v.to_dict() for k, v in self.positions.items()}, f, indent=2, default=str)
        
        # 重命名为正式文件（原子操作）
        os.replace(temp_path, path)
    
    def save(self):
        """保存到 StateManager（统一存储）"""
        # 保存到 StateManager（优先）
        if self.state_mgr:
            # 转换为字典格式
            data = {}
            for k, v in self.positions.items():
                data[k] = v.to_dict()
            self.state_mgr.data['positions'] = data
            self.state_mgr.save()
            return
        
        # 回退到文件存储（兼容旧代码）
        self._atomic_save()
    
    def has_position(self, symbol: str) -> bool:
        """检查是否有未平仓的持仓"""
        return symbol in self.positions and self.positions[symbol].status == 'open'
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """获取持仓"""
        return self.positions.get(symbol)
    
    def add_batch(self, symbol: str, amount: float, price: float, cost: float) -> Position:
        """
        添加一批仓位
        
        ⚠️ 修复僵尸持仓Bug：
        - 如果symbol存在但status='closed'，创建新的持仓记录
        - 只追加到status='open'的持仓
        """
        now = datetime.now().isoformat()
        
        if symbol not in self.positions:
            # 情况1：完全新仓位
            pos = Position(
                symbol=symbol,
                total_amount=amount,
                avg_price=price,
                batches=[
                    Batch(
                        batch_id=1,
                        amount=amount,
                        price=price,
                        cost=cost,
                        timestamp=now
                    )
                ],
                created_at=now,
                updated_at=now
            )
            self.positions[symbol] = pos
            
        else:
            # 情况2：symbol已存在
            existing = self.positions[symbol]
            
            if existing.status == 'closed':
                # 🔴 修复：把旧仓位归档，新仓位存在标准symbol key下
                # 1. 把旧仓位重命名归档
                old_key = symbol
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                archive_key = f"{symbol}_closed_{timestamp}"
                self.positions[archive_key] = existing
                del self.positions[old_key]
                
                # 2. 新仓位存在标准key下（这样后续查询才能找到）
                pos = Position(
                    symbol=symbol,  # 保持原symbol显示名
                    total_amount=amount,
                    avg_price=price,
                    batches=[
                        Batch(
                            batch_id=1,
                            amount=amount,
                            price=price,
                            cost=cost,
                            timestamp=now
                        )
                    ],
                    created_at=now,
                    updated_at=now
                )
                self.positions[symbol] = pos
                self.save()
                return pos
            
            elif existing.status == 'open':
                # 正常追加到现有open持仓
                batch_id = len(existing.batches) + 1
                existing.batches.append(Batch(
                    batch_id=batch_id,
                    amount=amount,
                    price=price,
                    cost=cost,
                    timestamp=now
                ))
                
                # 🔴 修复：使用Decimal计算均价，避免浮点误差
                total_cost = sum(Decimal(str(b.cost)) for b in existing.batches)
                total_amount = sum(Decimal(str(b.amount)) for b in existing.batches)
                avg_price = float(total_cost / total_amount) if total_amount > 0 else price
                
                existing.avg_price = avg_price
                existing.total_amount = float(total_amount)
                existing.updated_at = now
        
        self.save()
        return self.positions.get(symbol) or self.positions.get(f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    
    def close_position(self, symbol: str, reason: str):
        """平仓 - 彻底删除持仓记录"""
        if symbol in self.positions:
            # 记录日志后删除持仓（不再保留 closed 状态）
            logger.info(f"平仓删除持仓 {symbol}: {reason}")
            del self.positions[symbol]
            self.save()
    
    def enable_grid(self, symbol: str, upper: float, lower: float, step_percent: float = 0.5):
        """启用网格"""
        if symbol in self.positions:
            self.positions[symbol].grid = GridConfig(
                enabled=True,
                upper_price=upper,
                lower_price=lower,
                step_percent=step_percent,
                max_grids=int((upper - lower) / (lower * step_percent / 100)) + 1
            )
            self.save()
    
    def disable_grid(self, symbol: str):
        """禁用网格"""
        if symbol in self.positions:
            self.positions[symbol].grid = None
            self.save()
    
    def should_add_batch(self, symbol: str, current_price: float):
        """
        检查是否应该加仓
        
        可通过set_strategy_params()自定义策略参数
        
        Returns:
            (should_add, reason_dict)
        """
        if not self.has_position(symbol):
            return False, {'reason': 'no_open_position'}
        
        pos = self.positions[symbol]
        
        # 检查是否已完成所有批次（使用可配置参数）
        if len(pos.batches) >= self.max_batches:
            return False, {'reason': 'max_batches_reached', 'max': self.max_batches}
        
        # 价格条件：比均价低X%以上才加仓（使用可配置参数）
        price_threshold = pos.avg_price * (1 - self.drop_threshold)
        if current_price >= price_threshold:
            return False, {
                'reason': 'price_not_low_enough', 
                'threshold': price_threshold,
                'drop_needed': self.drop_threshold * 100
            }
        
        # 检查最近一次加仓是否在X小时内（使用可配置参数）
        if pos.batches:
            last_batch = pos.batches[-1]
            last_time = datetime.fromisoformat(last_batch.timestamp)
            if (datetime.now() - last_time).total_seconds() < self.batch_interval:
                return False, {
                    'reason': 'too_soon_since_last_batch',
                    'min_interval': self.batch_interval
                }
        
        return True, {
            'avg_price': pos.avg_price,
            'current_price': current_price,
            'discount': (1 - current_price / pos.avg_price) * 100 if pos.avg_price > 0 else 0,
            'batch_count': len(pos.batches)
        }
    
    def set_strategy_params(self, max_batches: int = None, 
                            drop_threshold: float = None, 
                            batch_interval: int = None):
        """
        设置策略参数（动态调整）
        
        Args:
            max_batches: 最多几批（默认3）
            drop_threshold: 跌多少%才补仓（默认5%）
            batch_interval: 补仓间隔秒数（默认3600=1小时）
        """
        if max_batches is not None:
            self.max_batches = max_batches
        if drop_threshold is not None:
            self.drop_threshold = drop_threshold
        if batch_interval is not None:
            self.batch_interval = batch_interval
    
    def check_stop_loss(self, current_price: float) -> List[str]:
        """检查止损"""
        triggered = []
        for symbol, pos in self.positions.items():
            if pos.status == 'open' and pos.stop_loss > 0:
                if current_price <= pos.stop_loss:
                    triggered.append(symbol)
        return triggered
    
    def check_take_profit(self, current_price: float) -> List[str]:
        """检查止盈"""
        triggered = []
        for symbol, pos in self.positions.items():
            if pos.status == 'open' and pos.take_profit > 0:
                if current_price >= pos.take_profit:
                    triggered.append(symbol)
        return triggered
    
    def get_summary(self) -> Dict:
        """获取汇总"""
        open_positions = [p for p in self.positions.values() if p.status == 'open']
        
        total_value = sum(p.total_amount * p.avg_price for p in open_positions)
        
        return {
            'open_count': len(open_positions),
            'total_value': total_value,
            'positions': [
                {
                    'symbol': p.symbol,
                    'amount': p.total_amount,
                    'avg_price': p.avg_price,
                    'batch_count': len(p.batches),
                    'grid_enabled': p.grid.enabled if p.grid else False
                }
                for p in open_positions
            ]
        }
