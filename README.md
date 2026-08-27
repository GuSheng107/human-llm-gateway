# Human LLM Gateway

一个将 OpenAI Chat Completions / Anthropic Messages 请求路由到真人 IM 或真实 LLM 的 Python 网关。每个 API Key 只绑定一个真人和一个 IM 连接，但可以创建很多组独立绑定。

## 启动

```powershell
cd D:\pyPrj\human-llm-gateway
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

管理台开发模式：

```powershell
cd D:\pyPrj\human-llm-gateway\admin
npm install --registry=https://registry.npmmirror.com
npm run dev
```

构建 `admin` 后，FastAPI 会在存在 `admin/dist` 时直接提供管理页面。

## 管理配置顺序

1. 登录管理台，默认账号来自 `.env`，首次运行请立即修改 `ADMIN_PASSWORD` 和 `APP_SECRET`。
2. 在“LLM 路由”中创建供应商。`openai_compatible` 可配置 OpenAI、Kimi、MiniMax、DeepSeek、Qwen 等兼容地址；`anthropic` 可配置 Claude 或兼容 Anthropic Messages 的地址。
3. 点击“同步模型”显式获取该供应商的 `/models` 目录，再在路由中填写对外模型名和实际上游模型。
4. 创建 API Key，选择已有模型路由，并绑定一个真人和一个 IM 连接。

客户端传入的 `model` 只为兼容 SDK；实际请求始终使用管理后台路由的 `upstream_model`。`GET /v1/models` 返回该 Key 所属路由发布的模型名。

首次启动时，如果 `DATABASE_URL` 指向的 SQLite 数据库不存在，服务会自动创建目录、数据库和全部表，并使用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 初始化管理员。管理员密码只以哈希形式写入数据库；数据库已存在时不会用环境变量覆盖已有管理员密码。

## 真人回复 DSL

```text
/think
先判断问题，再模拟查询天气。
/tool get_weather {"city":"北京"}
/reply
北京今天晴，最高 25°C。
/done
```

管理员也可以在任务详情页直接发送同一段 DSL；网页回复和 IM 回复使用相同解析、审计、幂等和伪流式链路。

## 当前平台边界

- Fake：本地测试，不发外部请求。
- Telegram：Bot API 收发和轮询。
- 企业微信：Webhook 出站；入站由标准化回调进入核心。
- 个人微信：只保留 Sidecar 配置契约，当前不宣称已接入个人微信。

## 验收

```powershell
python -m pytest -q
cd admin
npm run build
```

测试只使用 mock LLM、Fake Connector 和临时 SQLite，不执行真实账号测活或真实模型调用。
