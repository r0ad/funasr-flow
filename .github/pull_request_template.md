# 变更说明

<!-- 一句话说清这个 PR 做了什么、为什么。关联 issue 用 "Closes #123"。 -->

## 类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 重构（不改变行为）
- [ ] 文档 / 注释
- [ ] 构建 / CI / 发布配置
- [ ] 性能优化

## 改动范围

<!-- 勾选涉及的模块，方便 reviewer 分配注意力 -->

- [ ] `backend/app/api/`（HTTP 接口）
- [ ] `backend/app/services/`（扫描 / 队列 / 流水线 / 声纹）
- [ ] `backend/app/engines/`（ASR 引擎）
- [ ] `backend/app/db.py` / `config.py`（schema、配置、环境变量）
- [ ] `frontend/`
- [ ] `.github/` / `Dockerfile` / 打包配置
- [ ] 文档（README / docs / SECURITY）

## 自检清单

- [ ] 本地 `ruff check backend` 通过
- [ ] 本地 `ruff format --check backend` 通过（或已执行 `ruff format backend`）
- [ ] `pytest -q` 通过；新增逻辑带测试（纯逻辑即可，**不要在测试里下载模型**）
- [ ] 新增/改动接口有对应的前端调用或说明
- [ ] 环境变量变化已同步到 `config.py`、`Dockerfile`、`docker-compose.yml`、README
- [ ] 数据库 schema 变化能兼容已有 `data/flow.db`（或写清了迁移方式）
- [ ] 前端改动能正常构建（`cd frontend && npm run build`），产物路径仍是 `frontend/dist`

## 隐私与安全

- [ ] 未提交任何真实录音、转写结果、`data/`、`.cache/`、`CallRecord/` 内容
- [ ] 日志/示例中的姓名、电话、路径已脱敏
- [ ] 未引入默认联网上传音频或采集遥测的行为

## 验证方式

<!-- 你是怎么验证的？命令、截图、手动步骤都可以。 -->

```
# 例如
ruff check backend && ruff format --check backend
pytest -q
docker compose up -d --build
```

## 备注给 reviewer

<!-- 有争议的设计取舍、已知限制、后续 TODO -->
