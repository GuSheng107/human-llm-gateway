# 部署与运维

当前交付目标是单实例、邀请码注册的小规模使用。运行一个 Uvicorn worker；连接器、
验证码和运行协调器属于进程状态，不能使用 `--workers`、多副本或共享同一数据库启动
多个网关进程。公网使用前完成本文的 TLS、超时和备份配置。

## 1. 新实例

- Python 3.12（`.python-version` 指定，`uv sync` 自动选择）。
- Node.js 24；最低兼容范围为 `^20.19.0 || >=22.12.0`，不支持 Node.js 18。
- uv、Git，以及可访问锁文件中的 Python/npm/Git 依赖的网络。
- SQLite 使用本机持久磁盘，数据库和日志不进入 Git。

以下为 Bash 示例。在仓库根目录执行：

```bash
uv sync --locked
uv run --locked python -c "import secrets; print('APP_SECRET=' + secrets.token_urlsafe(32))" > .env
chmod 600 .env
```

用 UTF-8 编辑 `.env`，补充 `ADMIN_USERNAME`、符合密码策略的 `ADMIN_PASSWORD`、
`DATABASE_URL=sqlite:////var/lib/human-llm-gateway/gateway.db` 和实际
`GATEWAY_PUBLIC_HOSTS=gateway.example.com`。环境变量完整说明见根目录 `.env.example`。
不要原样使用示例密码；APP_SECRET 生成后必须持久保存，不能在每次启动时重新生成。

```bash
cd admin
npm ci
npm run build
cd ..
uv run --locked uvicorn app.api:app --host 127.0.0.1 --port 8000 \
  --ws-max-size 1048576 --timeout-graceful-shutdown 30
```

后端托管 `admin/dist`，管理台和 API 同端口。首次启动自动建库、创建管理员和默认模型；
管理员登录后必须改密，再创建邀请码或普通用户。管理员不能替普通用户回复任务，
体验人工闭环时请使用普通用户账号。

Windows 本机也使用相同的 uv/npm 命令。环境文件用 UTF-8 文本编辑器保存；数据库可写为
`DATABASE_URL=sqlite:///E:/gateway-data/gateway.db`。前端热更新使用 `npm run dev`，
Vite 统一代理至 `127.0.0.1:8000`。

## 2. 反向代理与长请求

人工等待阶段不会提前发送响应头或 SSE；完整回复提交后才开始伪流式输出。
人工等待最长 1800 秒，上游生成另有最多 600 秒总预算。因此代理和 SDK 的读取超时
必须覆盖完整等待和输出时间，建议至少 3600 秒。客户端重试会创建新的任务，人工模式
建议关闭自动重试，避免重复投递。

Nginx 示例（TLS 证书由部署者签发和续期）：

```nginx
# 放在 http {} 内，server {} 外
map $http_upgrade $connection_upgrade {
    default upgrade;
    '' close;
}

server {
    listen 443 ssl;
    server_name gateway.example.com;
    ssl_certificate /etc/ssl/gateway/fullchain.pem;
    ssl_certificate_key /etc/ssl/gateway/privkey.pem;
    client_max_body_size 8m;

    # 监控仅供本机或可信监控网段；管理和推理接口另有各自鉴权。
    location = /metrics {
        allow 127.0.0.1;
        deny all;
        proxy_pass http://127.0.0.1:8000;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }
}
```

仅信任部署者控制的代理。Uvicorn 默认只信任本机代理；代理跨机器时应显式指定
`--forwarded-allow-ips` 为该代理 IP，不能无条件设为 `*`。公网域名必须同时填入
`GATEWAY_PUBLIC_HOSTS`，防止 LLM 上游指回本网关。默认不允许内网 LLM 上游；仅在
部署者确需本机或内网模型时设置 `LLM_ALLOW_PRIVATE_UPSTREAM=true`。

SDK 示例：`OpenAI(base_url="https://gateway.example.com/v1", api_key=key,
timeout=3600, max_retries=0)`。`key` 是网关创建时一次性返回的 Key。

## 3. 服务管理与退出

Linux systemd 示例，用户 `hlg` 和 `/var/lib/human-llm-gateway` 须预先创建并授予写权限。
将 `.env` 移到 `/etc/human-llm-gateway.env`（权限 0600），由 systemd 加载：

```ini
[Unit]
Description=Human LLM Gateway
After=network-online.target
Wants=network-online.target

[Service]
User=hlg
Group=hlg
WorkingDirectory=/opt/human-llm-gateway
EnvironmentFile=/etc/human-llm-gateway.env
ExecStart=/opt/human-llm-gateway/.venv/bin/uvicorn app.api:app --host 127.0.0.1 --port 8000 --ws-max-size 1048576 --timeout-graceful-shutdown 30
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillSignal=SIGTERM
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

重启会中断仍在等待的 HTTP 调用；停止时先停止新准入，给在途请求 30 秒完成，再取消
残留任务、释放名额、停止连接器。强制结束后，下次启动同样取消旧活动任务，不会
重新执行上游请求。需要不中断长任务时，先在代理停止新请求，等待工作台活动任务归零再重启。

## 4. 在线备份与恢复

备份必须使用 SQLite backup API，不能在 WAL 写入期间只复制 `.db` 文件。维护入口
读取与服务相同的环境变量；systemd 环境不会自动传给交互式终端，运行命令前应由
部署平台安全加载对应环境文件。

```bash
uv run --locked python -m app.maintenance backup --output /srv/hlg-backups/2026-09-05.db
uv run --locked python -m app.maintenance verify-backup --path /srv/hlg-backups/2026-09-05.db
uv run --locked python -m app.maintenance restore \
  --source /srv/hlg-backups/2026-09-05.db --output /var/lib/human-llm-gateway/restored.db
```

目标必须不存在，命令不会覆盖原库。校验包含 SQLite 完整性、Schema 版本和加密
sentinel；错误 APP_SECRET 会拒绝恢复。恢复后停服，将 `DATABASE_URL` 指向新文件，
使用与备份匹配的代码版本和原 APP_SECRET 启动，再检查 `/readyz`、登录和加密配置。
原库保留到验收完成，便于重新切换。

建议每天备份，保留最近 7 份每日备份和 4 份每周备份，每月至少恢复到隔离目录演练一次。
使用部署平台的定时任务和备份生命周期功能落实此保留策略；仓库不会擅自删除备份。
备份含用户请求和密码哈希，应加密后异地存储并限制访问。

APP_SECRET 与数据库分开备份：优先放在部署平台的 Secret Manager；小型自托管可用
age/SOPS 加密环境文件，解密私钥存于密码管理器或离线介质。不要把明文主密钥与数据库
一起打包。当前仅支持单个主密钥，不能直接修改 APP_SECRET 轮换；在线 key ring 轮换
属于后续增强。

## 5. 监控、保留与故障排查

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8000/metrics
```

- `/healthz`：进程存活。
- `/readyz`：启动阶段 DB/Schema/加密检查和当前协调器运行状态；不探测实时磁盘可写性。
- `/metrics`：Prometheus HTTP 请求计数与在途请求数，标签仅含接口类别与状态码类别。
  计数随进程重启归零，不含用户、任务、模型、URL、Key 或异常正文。
- 单个 IM 连接异常只影响该连接，从工作台连接健康和日志页查看。
- 应用/审计日志等高频记录保留策略为每 7 天清理 7 天前的数据，实际可能保存近 14 天；
  请求任务及正式草稿长期保留，应监控磁盘使用量，在低于 20% 可用空间时告警。
- 日志输出由 systemd journal 接管。部署者可在专用主机的 journald 配置中设
  `SystemMaxUse=500M`、`MaxRetentionSec=7day`；这是主机级设置，需与其他服务协调。

| 现象 | 检查与处理 |
|---|---|
| 启动失败：APP_SECRET | 使用 32 随机字节的 base64url；恢复时使用原密钥 |
| Schema 不匹配 | 恢复匹配的代码与库；不提供旧库迁移或自动重建 |
| 管理台 404 | 先在 admin 执行 npm ci 与 npm run build，再启动后端 |
| 开发模式登录失败 | 确认后端在 8000、Vite 在 5173 |
| 人工回复前 504/断开 | 检查反代/CDN/SDK 的读取超时；平台硬上限也必须覆盖等待时间 |
| 429 | 检查当前用户全部 Key 的活动任务，共用 10 个名额 |
| IM 不投递 | 确认连接绑定、启用和运行状态，以及 Key 的投递连接 |
| LLM 保存/转发失败 | 查看连通性结果、协议匹配、上游密钥、模型及 SSRF 配置 |

## 6. 开放邀请码前验收

运行 AGENTS.md 的完整质量门禁，再用新库验证管理员首次改密、邀请码注册、普通用户
创建 Key、三协议请求、Web/IM 回复、LLM 草稿/转发/fallback、超限 429 和禁用用户。
至少验证一次“超过人工截止后 LLM 仍在生成”的情形，以及备份恢复和带活动任务退出。
真实 IM 账号、真实 LLM 上游和公网代理需在目标部署上验收；本地模拟上游测试不能代替
这三项。当前版本不承诺多副本、高并发 SLA 或旧数据库升级兼容。
