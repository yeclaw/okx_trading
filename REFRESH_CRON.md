# 每周成交额榜自动刷新

## 手动运行

```bash
cd /root/clawd/okx_trading
python refresh_symbols.py           # 预览
python refresh_symbols.py --update # 执行更新
```

## 设置定时任务

编辑 crontab：

```bash
crontab -e
```

添加以下行（每周一 9:00 自动更新）：

```
0 9 * * 1 cd /root/clawd/okx_trading && python refresh_symbols.py --update >> logs/refresh.log 2>&1
```

## 参数说明

| 参数 | 说明 |
|------|------|
| 无 | 仅预览，不修改文件 |
| --update | 执行更新，修改 main.py |

## 输出日志

- `logs/refresh.log` - 刷新日志
