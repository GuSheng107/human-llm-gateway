# Human LLM Gateway 架构索引

当前版本已经完成部署。本文件保留为根目录入口，完整架构事实来源统一维护在 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 运行单元

- 单个 FastAPI 进程同时托管管理 API、三种推理协议、连接器入口和构建后的 React 管理台。
- `app/api/` 负责 HTTP/WS 边界、鉴权、请求模型和响应。
- `app/services/` 负责用例编排和事务边界。
- `app/repositories/` 负责 SQLAlchemy 查询和持久化。
- `app/domain/` 负责枚举、状态机、协议无关规则和对话展示投影。
- `app/connectors/` 负责 IM 平台适配和连接运行时。
- `app/core/` 负责配置、数据库、加密、日志和应用生命周期。
- `admin/` 是 React 19 + TypeScript + Vite + Tailwind CSS 4 管理台。

## 当前运行链路

1. 外部调用方通过 `/v1/*` 和 API Key 进入协议适配层。
2. 服务层验证 Fake Model、所有权和用户级活动任务上限，创建请求任务。
3. 任务进入 Web 回复工作台，也可以投递到任务所有者自己的 IM 连接。
4. 用户提交人工回复，或按 API Key 策略使用自己的真实 LLM 配置。
5. 完整回复落库后，再按目标协议返回非流式或伪流式响应。
6. HTTP、审计、应用、连接和看门狗事件共享 trace；高频数据由七日保留任务清理。

## 关键边界

- Fake Model 与真实 LLM 配置解耦，真实供应商和模型不会出现在对外身份字段中。
- 普通 IM 接口只返回当前用户连接；管理员通过独立监管接口查看全部连接。
- 管理员不能读取用户 Secret、完整 API Key、Token、二维码或替用户回复。
- 调用方声明的工具不会自动执行；管理员白名单工具只在失败关闭的 OCI 沙箱中运行。
- 按当前数据库、API、连接器和回复工作台契约运行。

## 相关文档

- [产品定义](docs/PRODUCT.md)
- [API 契约](docs/API_CONTRACT.md)
- [数据库设计](docs/DATABASE.md)
- [UI 规范](docs/UI_GUIDE.md)
- [工具沙箱](docs/SANDBOX.md)
- [实施路线图](docs/ROADMAP.md)
