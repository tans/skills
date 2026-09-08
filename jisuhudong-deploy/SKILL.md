---
name: jisuhudong-deploy
slug: jisuhudong-deploy-user-e866c542
displayName: 极速互动部署
version: 1.0.1
summary: 通过 SSH 和宝塔把项目部署到 *.jisuhudong.com，并支持后续更新。
description: 将已有 Web/Node 服务部署或更新到极速互动的宝塔主机，统一使用 *.jisuhudong.com 子域名，项目与数据放在 /data 下。适用于需要真实服务器、域名、反向代理、进程守护和 HTTPS 验收的部署请求；不用于只做本地启动或静态部署方案评估。
---

# 极速互动部署

## 固定约束

- 目标域名必须是 `<slug>.jisuhudong.com`。拒绝裸域名、其他主域名或把 slug 直接拼成不合法的 Host；slug 只允许小写字母、数字和连字符，长度 1-63。
- 代码、上传文件、数据库、日志、备份和运行时配置统一放在远端 `/data/<slug>/`，可按用途建立 `app/`、`data/`、`logs/`、`backups/`；不要把持久数据放在发布目录或 `/tmp`。
- 访问私钥由用户首次提供后保存到本机 `~/.config/jisuhudong-deploy/keys/<host>.key`，目录 `0700`、文件 `0600`，后续优先复用；绝不把私钥写入仓库、命令输出、日志或远端项目目录。不能自动发现或上传其他密钥。
- 所有部署和更新都必须先读取现有状态，保持可回滚；更新前制作 `/data/<slug>/backups/<timestamp>/` 备份（至少配置和数据库，按项目类型补充上传文件）。

## 执行流程

1. 从项目的 README、package/build 文件和 `.env.example` 确定启动命令、监听端口、健康检查、构建产物和持久化目录。缺失且无法推断的关键值只问一个问题。
2. 确认或复用本地私钥，使用 `ssh -o BatchMode=yes` 测试连接；首次保存前验证用户明确给出的主机、用户名和指纹，不关闭 HostKey 校验。
3. 远端检查 OS、宝塔安装、Nginx/OpenResty、`/www/server/panel/vhost/nginx`、面板站点数据库和端口占用。已存在同一 slug 时进入更新流程，不重复建站或覆盖数据。
4. 仅当确认项目是非静态应用且同一 slug 尚未部署时，让用户在 `Docker` 与 `直接运行` 中二选一，并记录选择后沿用，后续更新不得擅自切换。静态站点不进入此选择，按宝塔静态站点流程处理。
5. 创建 `/data/<slug>` 及子目录，上传或拉取代码，安装锁文件对应依赖，构建并以非特权用户运行（项目需要 root 时说明原因）。把 secrets 写入远端 `0600` 配置文件或进程环境，不提交 `.env`。
6. 按首次选择部署：`Docker` 使用项目既有 `Dockerfile`/Compose，容器和卷命名包含 slug，持久卷映射到 `/data/<slug>/data`，设置重启策略；`直接运行` 使用宝塔 Node 项目管理、Supervisor 或 systemd，工作目录和日志均在 `/data/<slug>`，设置开机恢复。记录实际 PID/容器、监听端口和日志路径。
7. 用宝塔站点能力创建/更新站点和反向代理：HTTP/HTTPS 的 server_name 均为 `<slug>.jisuhudong.com`，代理到本机回环地址，设置 WebSocket 和真实客户端头；优先使用宝塔 CLI/API，面板 API 关闭时才编辑其生成配置并用 `nginx -t` 后 reload。不要只写一个脱离宝塔记录的 Nginx vhost。
8. 配置 DNS 只在用户授权且已有 DNS 凭据时执行；否则报告需要把 `<slug>.jisuhudong.com` 指向目标主机的记录。
9. 申请或配置 HTTPS（宝塔证书/ACME），然后从外部验证：DNS、HTTP 重定向、HTTPS 证书域名、首页或健康接口、关键业务路径和更新后的版本标识。仅 `curl 127.0.0.1`、Docker health 或 HTTP 200 不算完成。
10. 更新时先比较当前版本和目标版本，优雅停止，备份，部署到临时目录后切换；沿用已记录的 Docker/直接运行方式。失败自动恢复上一版本并再次验证。报告真实覆盖范围和任何未验证的外部依赖。

## 主机事实和参考

本技能的基准宝塔环境记录在 [references/host-baseline.md](references/host-baseline.md)。每次执行仍须现场检查，不得把端口、路径或面板状态当作永远不变。

## 完成报告

必须包含：目标 URL、远端项目/数据路径、运行方式与端口、备份位置、部署或更新版本、HTTPS/健康/代表业务验证结果、DNS 或用户操作门槛、失败时的回滚结果。不要回显任何私钥、token、密码或 `.env` 内容。
