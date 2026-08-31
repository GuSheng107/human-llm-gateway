# 跨平台工具沙箱

## 1. 运行模型

工具白名单命令只在本机 Docker 或 Podman 的 Linux OCI 容器中运行。网关在
Windows、macOS 和 Linux 上使用同一组容器参数；Windows 与 macOS 需要
Docker Desktop 或 Podman Machine 使用 Linux 容器模式。

系统没有宿主进程、旧沙箱或宽松模式。找不到运行时、镜像或容器守护进程时，
执行记录以 `failed / sandbox_unavailable` 或 `sandbox_runtime_error` 结束。

## 2. 构建默认镜像

Linux/macOS（Docker）：

```bash
docker build -t human-llm-gateway-tool-sandbox:latest \
  docker/tool-sandbox
```

Podman：

```bash
podman build -t human-llm-gateway-tool-sandbox:latest \
  docker/tool-sandbox
```

Windows PowerShell（Docker Desktop 必须切换到 Linux containers）：

```powershell
docker build -t human-llm-gateway-tool-sandbox:latest docker/tool-sandbox
```

macOS/Windows 使用 Podman 时，先执行 `podman machine init`（首次）和
`podman machine start`。Linux 推荐 rootless Podman；三种系统中的白名单命令
都以镜像内的 Linux 命令和路径为准，不依赖宿主 shell。

默认镜像只提供 Python 3.12 和 Debian slim 基础命令。需要其他工具时，应基于
该 Dockerfile 构建固定版本的自定义镜像，再设置 `TOOL_SANDBOX_IMAGE`。运行时
使用 `--pull=never`，不会在用户确认执行后临时联网拉取镜像。
构建命令只发送 `docker/tool-sandbox` 目录作为上下文，不会把源码、`.env` 或
本地数据库发送给容器构建后端。

## 3. 配置

```dotenv
TOOL_SANDBOX_RUNTIME=auto
TOOL_SANDBOX_IMAGE=human-llm-gateway-tool-sandbox:latest
TOOL_SANDBOX_MEMORY_MB=256
TOOL_SANDBOX_CPUS=1.0
TOOL_SANDBOX_PIDS_LIMIT=64
TOOL_SANDBOX_TMPFS_MB=64
```

`auto` 依次查找 Docker、Podman。生产环境建议显式指定已部署的运行时。

## 4. 强制隔离

每次执行创建一个全新容器，并强制：

- `network=none`，没有入站或出站网络；
- 不挂载任何宿主目录、设备或容器运行时 socket；
- 根文件系统只读，只有 `/workspace`、`/tmp`、`/run` 是限额 tmpfs；
- UID/GID 65532 非 root 运行，删除全部 Linux capabilities；
- `no-new-privileges`、独立 PID/IPC/UTS/网络命名空间；
- 内存、CPU、PID、文件描述符、执行时间和 stdout/stderr 上限；
- 容器 CLI 只继承本机运行时连接和配置目录所需的宿主变量，拒绝环境变量指定的
  TCP/SSH 远端 daemon；网关 Secret 不进入 CLI；这些宿主配置也不会挂载进容器；
- 容器内只显式设置 `HOME`、`TMPDIR`、`PATH`，不传 API Key 或应用环境变量；
- 用户参数只替换为单个 argv 内容，不经过 shell，也不能控制可执行文件。

达到时间或输出上限后，网关终止 CLI 进程树并按随机容器名执行强制清理。

## 5. 信任边界

OCI 运行时、宿主内核和管理员构建的镜像属于可信计算基。管理员仍应只登记必要
命令，不应把 Docker socket、宿主目录或 Secret 烘焙进镜像。Linux 生产环境可
优先使用 rootless Podman；Docker Desktop 的 Linux VM 提供 Windows/macOS 与
Linux 一致的容器语义。

## 6. 测试

`tests/test_m12_tools.py` 中的 API 契约测试打桩 `run_sandboxed`，不依赖容器运行时；
文件末尾的 `test_e2e_*` 用例在检测到 Docker/Podman 且沙箱镜像已构建时，真实创建容器
验证成功执行、超时终止、最小环境/tmpfs 和断网，否则自动跳过。发布前建议在装好
运行时的机器上执行一次完整 `pytest tests/test_m12_tools.py`。

设计参考：

- [DeepSeek Harness sandbox](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sandbox/README.md)：失败关闭与明确的平台隔离边界；
- [OpenClaw sandboxing](https://github.com/openclaw/openclaw/blob/main/docs/gateway/sandboxing.md)：无网络、只读根文件系统、capability 清零和 no-new-privileges；
- [Hermes Agent code execution](https://github.com/NousResearch/hermes-agent/blob/main/tools/code_execution_tool.py)：工具白名单、最小环境和隔离执行后端。
