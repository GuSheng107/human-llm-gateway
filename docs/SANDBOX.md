# 跨平台工具沙箱

## 1. 运行模型

工具白名单命令只在本机 Docker 或 Podman 的 Linux OCI 容器中运行。网关在
Windows、macOS 和 Linux 上使用同一组容器参数；Windows 与 macOS 需要
Docker Desktop 或 Podman Machine 使用 Linux 容器模式。

系统没有宿主进程、旧沙箱或宽松模式。找不到运行时、镜像或容器守护进程时，
执行记录以 `failed / sandbox_unavailable` 或 `sandbox_runtime_error` 结束。

## 2. 按平台安装运行时

安装完成不等于网关可用。必须在“网关进程实际运行的用户和环境”中同时满足：

1. `docker` 或 `podman` 命令位于 `PATH`；
2. CLI 能连接本机 Linux 容器运行时；
3. 默认沙箱镜像已构建到该运行时；
4. 网关服务重启后继承正确的 `PATH`、socket 权限和环境变量。

### 2.1 Linux

生产环境推荐 Docker Engine 或 rootless Podman，不需要桌面程序。Docker 应按
[官方发行版安装说明](https://docs.docker.com/engine/install/)配置软件源并安装；
Podman 可按[官方安装说明](https://podman.io/docs/installation)使用发行版软件包。

Ubuntu/Debian 安装 Podman：

```bash
sudo apt-get update
sudo apt-get install -y podman
podman info
```

Fedora/RHEL 系安装 Podman：

```bash
sudo dnf install -y podman
podman info
```

Docker Engine 安装完成后启动并验证 daemon：

```bash
sudo systemctl enable --now docker
docker version
```

若网关由 systemd 普通用户运行，必须以该用户验证，而不是只在管理员 shell 中验证：

```bash
sudo -u <gateway-user> sh -lc 'command -v docker && docker version'
```

使用 Docker group 可解决 socket 权限，但该组近似拥有宿主 root 权限；完成
`sudo usermod -aG docker <gateway-user>` 后必须重新登录或重启服务。更严格的部署
可使用 rootless Docker/Podman，并确保网关服务继承对应的本机 Unix socket 环境。

构建并检查默认镜像：

```bash
docker build -t human-llm-gateway-tool-sandbox:latest docker/tool-sandbox
docker image inspect human-llm-gateway-tool-sandbox:latest >/dev/null
```

使用 Podman 时把上述命令中的 `docker` 换为 `podman`，并设置
`TOOL_SANDBOX_RUNTIME=podman`。

### 2.2 macOS

选择其一：

- 安装并启动 [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)，等待 `docker version` 同时显示 Client 和 Server；
- 安装 Podman 官方安装包，然后创建并启动 Linux VM。

Podman 初始化：

```bash
podman machine init
podman machine start
podman info
```

随后在仓库根目录构建镜像：

```bash
docker build -t human-llm-gateway-tool-sandbox:latest docker/tool-sandbox
```

若选择 Podman，则将命令改为 `podman build`。每次重启 macOS 后，应先确认
Docker Desktop 已启动，或 `podman machine start` 已完成。

### 2.3 Windows

推荐安装 [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)，
启用 WSL 2 后端并使用 Linux containers。Docker Desktop 不支持 Windows Server；
Windows Server 应把网关和沙箱运行时部署到 Linux VM/WSL2 或独立 Linux 主机。

PowerShell 验证：

```powershell
wsl.exe --status
docker version
docker info --format '{{.OSType}}'
```

最后一条必须输出 `linux`。然后构建默认镜像：

```powershell
docker build -t human-llm-gateway-tool-sandbox:latest docker/tool-sandbox
docker image inspect human-llm-gateway-tool-sandbox:latest | Out-Null
```

使用 Podman 时安装官方 Windows 包，并执行：

```powershell
podman machine init
podman machine start
podman info
podman build -t human-llm-gateway-tool-sandbox:latest docker/tool-sandbox
```

## 3. 构建默认镜像

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

## 4. 配置

```dotenv
TOOL_SANDBOX_RUNTIME=auto
TOOL_SANDBOX_IMAGE=human-llm-gateway-tool-sandbox:latest
TOOL_SANDBOX_MEMORY_MB=256
TOOL_SANDBOX_CPUS=1.0
TOOL_SANDBOX_PIDS_LIMIT=64
TOOL_SANDBOX_TMPFS_MB=64
```

`auto` 依次查找 Docker、Podman。生产环境建议显式指定已部署的运行时。

## 5. 网关本身运行在 Docker 中

这是 Linux 服务器最常见的 `sandbox_unavailable` 原因：宿主机有 Docker，不代表
网关容器内部有 Docker CLI，也不代表它能访问宿主 daemon。网关容器必须同时具备：

- 镜像内安装与宿主 daemon 兼容的 Docker CLI；
- 挂载本机 `/var/run/docker.sock`；
- 网关容器内运行用户具有该 socket 的读写权限；
- `TOOL_SANDBOX_RUNTIME=docker`；
- 沙箱镜像构建在同一个宿主 daemon 中。

Compose 服务至少需要同等配置：

```yaml
services:
  gateway:
    environment:
      TOOL_SANDBOX_RUNTIME: docker
      TOOL_SANDBOX_IMAGE: human-llm-gateway-tool-sandbox:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
```

挂载 socket 会让网关容器拥有控制宿主 Docker daemon 的高权限，只能用于受信任的
网关服务容器，不能把 socket 再挂入工具沙箱。沙箱子容器仍由本项目固定参数创建，
不挂载宿主目录、socket 或 Secret。

从网关容器内部验证，以下三条必须全部成功：

```bash
docker exec <gateway-container> sh -lc 'command -v docker'
docker exec <gateway-container> docker version
docker exec <gateway-container> docker image inspect human-llm-gateway-tool-sandbox:latest
```

若第一条失败，应修改网关镜像安装 CLI；第二条失败，检查 socket 挂载和权限；第三条
失败，在宿主机连接同一 daemon 构建沙箱镜像。不要在网关容器中启动另一个特权
Docker daemon，也不要把远程 TCP/SSH daemon 配给网关，运行器会拒绝远端 endpoint。

## 6. `sandbox_unavailable` 排查

错误记录为 `failed / sandbox_unavailable` 且 `duration_ms=0` 时，说明网关尚未成功
启动容器 CLI，优先检查 CLI 不在 `PATH`、文件不可执行或网关容器内未安装 CLI。
`sandbox_runtime_error` 通常表示 CLI 已启动，但 daemon、镜像或容器参数失败。

必须在网关的实际运行环境中依次执行：

```bash
command -v docker || command -v podman
docker version
docker image inspect human-llm-gateway-tool-sandbox:latest
docker run --rm --pull=never --network none --read-only \
  --user 65532:65532 \
  --entrypoint python \
  human-llm-gateway-tool-sandbox:latest \
  -c 'print("sandbox-ok")'
```

使用 Podman 时替换命令名。仍失败时按顺序检查：

1. 网关服务重启后是否继承正确 `PATH`；
2. systemd 用户或网关容器用户是否能访问本机 runtime socket；
3. `TOOL_SANDBOX_RUNTIME` 是否与实际安装一致；
4. `TOOL_SANDBOX_IMAGE` 是否与本机镜像名和 tag 完全一致；
5. Docker Desktop/Podman Machine 或 Linux daemon 是否正在运行；
6. 宿主安全策略是否允许只读根文件系统、tmpfs、资源限制和 `--init`。

排查时不要把网关改成宿主 shell 执行，也不要关闭 `network=none`、只读根文件系统、
非 root、capability 清零或资源限制；运行时不可用必须继续失败关闭。

## 7. 强制隔离

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

## 8. 信任边界

OCI 运行时、宿主内核和管理员构建的镜像属于可信计算基。管理员仍应只登记必要
命令，不应把 Docker socket、宿主目录或 Secret 烘焙进镜像。Linux 生产环境可
优先使用 rootless Podman；Docker Desktop 的 Linux VM 提供 Windows/macOS 与
Linux 一致的容器语义。

## 9. 测试

`tests/test_m12_tools.py` 中的 API 契约测试打桩 `run_sandboxed`，不依赖容器运行时；
文件末尾的 `test_e2e_*` 用例在检测到 Docker/Podman 且沙箱镜像已构建时，真实创建容器
验证成功执行、超时终止、最小环境/tmpfs 和断网，否则自动跳过。发布前建议在装好
运行时的机器上执行一次完整 `pytest tests/test_m12_tools.py`。

设计参考：

- [DeepSeek Harness sandbox](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/sandbox/README.md)：失败关闭与明确的平台隔离边界；
- [OpenClaw sandboxing](https://github.com/openclaw/openclaw/blob/main/docs/gateway/sandboxing.md)：无网络、只读根文件系统、capability 清零和 no-new-privileges；
- [Hermes Agent code execution](https://github.com/NousResearch/hermes-agent/blob/main/tools/code_execution_tool.py)：工具白名单、最小环境和隔离执行后端。
