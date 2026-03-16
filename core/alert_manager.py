# -*- coding: utf-8 -*-
"""
报警管理器
支持多种通知渠道：Telegram、Email、Webhook
"""

import os
import time
import threading
import requests
from datetime import datetime
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)


class AlertManager:
    """报警管理器"""
    
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.critical_channels = self.config.get('critical_channels', ['telegram'])
        self.warning_channels = self.config.get('warning_channels', [])
        
        # Telegram 配置
        self.telegram_token = self.config.get('telegram_token')
        self.telegram_chat_id = self.config.get('telegram_chat_id')
        
        # Email 配置
        self.smtp_host = self.config.get('smtp_host')
        self.smtp_port = self.config.get('smtp_port', 587)
        self.smtp_user = self.config.get('smtp_user')
        self.smtp_password = self.config.get('smtp_password')
        self.email_from = self.config.get('email_from')
        self.email_to = self.config.get('email_to', [])
        
        # Webhook 配置
        self.webhook_url = self.config.get('webhook_url')
        
        # 限流：同类型报警多久发一次
        self.cooldown_seconds = self.config.get('cooldown_seconds', 300)  # 默认 5 分钟
        self.last_alert_time = {}  # {alert_type: timestamp}
    
    def _should_send(self, alert_type: str) -> bool:
        """检查是否应该发送报警（限流）"""
        now = time.time()
        last = self.last_alert_time.get(alert_type, 0)
        if now - last < self.cooldown_seconds:
            return False
        self.last_alert_time[alert_type] = now
        return True
    
    def _format_message(self, level: str, title: str, message: str) -> str:
        """格式化报警消息"""
        emoji = {
            'CRITICAL': '🔴',
            'ERROR': '🟠',
            'WARNING': '🟡',
            'INFO': '🔵'
        }.get(level, '🔵')
        
        return f"""{emoji} *{level}* | RSI Robot
━━━━━━━━━━━━━━━━━━
📌 {title}
📝 {message}
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
    
    def send_telegram(self, message: str) -> bool:
        """发送 Telegram 消息"""
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("[Alert] Telegram 未配置")
            return False
        
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        data = {
            'chat_id': self.telegram_chat_id,
            'text': message,
            'parse_mode': 'Markdown'
        }
        
        try:
            response = requests.post(url, json=data, timeout=10)
            if response.status_code == 200:
                logger.info("[Alert] Telegram 发送成功")
                return True
            else:
                logger.error(f"[Alert] Telegram 发送失败: {response.text}")
                return False
        except Exception as e:
            logger.error(f"[Alert] Telegram 异常: {e}")
            return False
    
    def send_email(self, subject: str, message: str) -> bool:
        """发送 Email"""
        if not self.smtp_host or not self.email_to:
            logger.warning("[Alert] Email 未配置")
            return False
        
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            msg = MIMEMultipart()
            msg['From'] = self.email_from or self.smtp_user
            msg['To'] = ', '.join(self.email_to)
            msg['Subject'] = f"[RSI Robot] {subject}"
            
            body = f"""
RSI 量化机器人报警

{subject}

{message}

时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
            msg.attach(MIMEText(body, 'plain'))
            
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.email_from or self.smtp_user, self.email_to, msg.as_string())
            
            logger.info("[Alert] Email 发送成功")
            return True
        except Exception as e:
            logger.error(f"[Alert] Email 异常: {e}")
            return False
    
    def send_webhook(self, message: str, level: str = 'WARNING') -> bool:
        """发送 Webhook 通知"""
        if not self.webhook_url:
            return False
        
        data = {
            'level': level,
            'title': 'RSI Robot Alert',
            'message': message,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            response = requests.post(self.webhook_url, json=data, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"[Alert] Webhook 异常: {e}")
            return False
    
    def send(self, level: str, title: str, message: str, 
             channels: List[str] = None, force: bool = False) -> bool:
        """
        发送报警
        
        Args:
            level: CRITICAL, ERROR, WARNING, INFO
            title: 标题
            message: 内容
            channels: 指定渠道，默认根据 level 自动选择
            force: 强制发送，忽略限流
        """
        # 限流检查
        alert_type = f"{level}:{title}"
        if not force and not self._should_send(alert_type):
            logger.debug(f"[Alert] 限流跳过: {alert_type}")
            return False
        
        # 确定发送渠道
        if channels is None:
            if level == 'CRITICAL':
                channels = self.critical_channels
            else:
                channels = self.warning_channels
        
        if not channels:
            logger.warning("[Alert] 未配置任何通知渠道")
            return False
        
        formatted_msg = self._format_message(level, title, message)
        
        # 发送
        success = False
        for channel in channels:
            if channel == 'telegram':
                success = self.send_telegram(formatted_msg) or success
            elif channel == 'email':
                success = self.send_email(title, message) or success
            elif channel == 'webhook':
                success = self.send_webhook(formatted_msg, level) or success
        
        return success
    
    # 便捷方法
    def critical(self, title: str, message: str, force: bool = False):
        """严重错误报警（同步，确保送达）"""
        return self.send('CRITICAL', title, message, force=force)
    
    def error(self, title: str, message: str, force: bool = False):
        """错误报警"""
        return self.send('ERROR', title, message, force=force)
    
    def warning(self, title: str, message: str):
        """警告信息"""
        return self.send('WARNING', title, message)
    
    def info(self, title: str, message: str):
        """信息"""
        return self.send('INFO', title, message)


# 全局单例
_alert_manager = None

def get_alert_manager(config: dict = None) -> AlertManager:
    """获取报警管理器单例"""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager(config)
    return _alert_manager


# 使用示例
if __name__ == '__main__':
    # 配置
    config = {
        'critical_channels': ['telegram'],
        'warning_channels': ['telegram'],
        'telegram_token': 'YOUR_BOT_TOKEN',
        'telegram_chat_id': 'YOUR_CHAT_ID',
        'cooldown_seconds': 300,  # 5 分钟内同类型报警不重复
    }
    
    alert = AlertManager(config)
    
    # 测试
    alert.critical('测试报警', '这是一个测试消息', force=True)
