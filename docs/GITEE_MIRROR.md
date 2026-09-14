# Gitee 镜像同步

本项目主仓库托管在 GitHub，同时在 Gitee 维护一个**只读镜像**，供国内用户快速克隆、浏览与下载。Gitee 侧不接受 PR，所有改动仍以 GitHub 为准。

## 为什么做镜像

- 国内直连 GitHub 常受网络波动影响，`git clone` 与网页浏览体验不稳定。
- Gitee 服务器在国内，速度明显更快，便于国内开发者参与和使用。
- 主仓库仍是唯一真理来源：分支保护、PR 流程、CI 门禁全部在 GitHub 侧执行。

## 数据流向

```text
开发者 → GitHub master →（GitHub Actions: mirror-gitee.yml）→ Gitee master
```

镜像为**单向推送**，Gitee 侧的改动不回流。

## 一次性配置（仓库管理员）

### 1. 在 Gitee 创建仓库

1. 登录 [gitee.com](https://gitee.com/)（需完成实名认证）。
2. 新建仓库，名称建议与 GitHub 一致：`human-llm-gateway`。
3. **不要**勾选「使用 Readme 文件初始化仓库」——保持空仓库，否则首次推送会因历史分叉被拒。
4. 仓库设为**公开**（与 GitHub 主仓一致）。
5. 许可证选择 AGPL-3.0，与主仓保持一致。

### 2. 生成 Gitee 私人令牌

1. 进入「设置 → 私人令牌 → 生成新令牌」。
2. 勾选权限：**projects**（仓库读写所必需）。
3. 生成后**立即复制**——令牌只显示一次。

### 3. 在 GitHub 配置 Secret 与 Variable

进入 GitHub 仓库 → **Settings → Secrets and variables → Actions**：

| 类型 | 名称 | 值 |
|---|---|---|
| Secret | `GITEE_TOKEN` | 第 2 步生成的私人令牌 |
| Variable | `GITEE_REPO` | 目标仓库全路径，形如 `你的用户名/human-llm-gateway` |

> 令牌存为 **Secret**（加密、日志中自动脱敏），仓库路径存为 **Variable**（明文，便于排查）。
> 二者缺失时，`mirror-gitee.yml` 会跳过同步并在日志中给出提示，不会让流水线变红。

## 同步行为

- **触发时机**：每次推送到 `master`，或手动触发 `workflow_dispatch`。
- **同步内容**：`master` 分支（强制推送）与全部标签。
- **不涉及**：其他分支、PR、Issues、Discussions——这些只在 GitHub 存在。

强制推送是镜像的标准做法：Gitee 侧仅作为副本，历史始终跟随 GitHub。

## 手动同步

若需在本地立即同步（不等待 Actions）：

```bash
git remote add gitee https://gitee.com/<owner>/<repo>.git
git push --force gitee master
git push gitee --tags
```

首次推送前请确认 Gitee 侧是**空仓库**，否则需先清空。

## 排查

| 现象 | 原因与处理 |
|---|---|
| 工作流日志显示「跳过镜像同步」 | `GITEE_TOKEN` 或 `GITEE_REPO` 未配置，按上文第 3 步补齐 |
| `remote: Permission denied` | 令牌权限不足或已过期，重新生成并勾选 **projects** |
| `failed to push some refs`（非 fast-forward） | Gitee 侧仓库非空，清空仓库或改用已有的非空推送策略 |
| 推送成功但 Gitee 页面未刷新 | Gitee 侧有缓存，等待数分钟或查看仓库的「动态」页 |
