# 极速互动宝塔主机基线

最近现场核查日期：2026-09-08。

- SSH 别名：`miao-prod`，主机名 `miao.jisuhudong.com`，用户 `root`，配置来源 `/Users/ke/code/ssh/config`。
- 系统：CentOS 7。宝塔面板目录：`/www/server/panel`；服务脚本：`/etc/init.d/bt`。
- Web：Nginx 监听 80/443，站点配置目录通常为 `/www/server/panel/vhost/nginx`，重载前运行 `nginx -t`。
- 面板 API 配置 `/www/server/panel/config/api.json` 当前 `open: false`；不要假设 token API 可用，先检查再决定 CLI 或配置文件路径。
- 已有业务数据位于 `/data`，包括 `/data/miaomiao`、`/data/miaozao`、`/data/videobuddy` 等；新产品按 `/data/<slug>` 隔离。
- 现场端口包含 MongoDB 27017、PostgreSQL 5432、多个 Node/Docker 服务；部署前必须用 `ss -ltnp` 重新选取未占用的回环端口。

## 只读侦察命令

```bash
ssh -F /Users/ke/code/ssh/config miao-prod 'hostname; cat /etc/os-release; ss -ltnp'
ssh -F /Users/ke/code/ssh/config miao-prod 'find /www/server/panel/vhost/nginx -maxdepth 1 -type f -name "*.conf" -print'
ssh -F /Users/ke/code/ssh/config miao-prod 'sqlite3 /www/server/panel/data/default.db ".tables"'
```

面板数据库字段或文件布局可能因版本变化，必须先 `pragma table_info(...)` 或查看现有站点，再执行写操作。
