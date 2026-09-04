# Human LLM Gateway 产品规格索引

当前版本已经完成部署。本文件保留为根目录入口，完整产品事实来源统一维护在 [`docs/PRODUCT.md`](docs/PRODUCT.md)，阶段状态维护在 [`docs/ROADMAP.md`](docs/ROADMAP.md)。

## 当前产品范围

- 对外提供 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 三种协议。
- Fake Model 只表示对外身份；管理员维护系统模型，用户维护自己的私有模型。
- 用户通过 Web 统一回复工作台或自己的 IM 处理任务，也可以使用自己的 LLM 配置。
- 管理台包含控制台、回复工作台、IM 连接、API 管理、LLM 管理、日志和系统设置。
- 日志、审计和高频运行数据保留 7 天；请求任务和正式回复草稿保留。
- 网关不执行任何工具（无白名单、无沙箱）；人工回复的 tool call 名称必须命中调用方声明并仅做伪造输出转发，工具由调用方自行执行并承担后果。

## 当前部署入口

构建 `admin/dist` 后由 FastAPI 托管前端和 API：

```bash
cd admin && npm ci && npm run build
cd ..
uv run uvicorn app.api:app --host 0.0.0.0 --port 8000 --ws-max-size 1048576
```

存活检查使用 `GET /healthz`，就绪检查使用 `GET /readyz`。`/metrics` 暂未开放。

## 相关文档

- [产品定义](docs/PRODUCT.md)
- [架构](docs/ARCHITECTURE.md)
- [API 契约](docs/API_CONTRACT.md)
- [数据库设计](docs/DATABASE.md)
- [UI 规范](docs/UI_GUIDE.md)
- [实施路线图](docs/ROADMAP.md)
