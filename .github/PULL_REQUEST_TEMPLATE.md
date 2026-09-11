## 改动范围

<!-- 说明这个 PR 解决了什么问题、涉及哪些模块。 -->

## 验证方式

<!-- 列出实际执行的命令和结果；没有执行的项要写明原因和未验证的风险。 -->

- [ ] `uv lock --check`
- [ ] `uv run --locked ruff format --check app tests`
- [ ] `uv run --locked ruff check app tests`
- [ ] `uv run --locked python -m pytest -q`
- [ ] `(cd admin && npm ci && npm test && npm run build)`
- [ ] `git diff --check`

## 检查项

- [ ] 未提交数据库、`.env`、日志、构建产物、二维码或明文凭据
- [ ] 涉及契约变更时已同步 `docs/` 中对应的事实来源
- [ ] 新增或修改的行为有对应测试覆盖

## 关联 Issue

<!-- 关联的 issue 编号，没有可留空。 -->
