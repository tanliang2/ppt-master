# PPT Master Agent Backend

这是 PPT Master 的后端编排层。它不重写现有 PPT 生成核心，而是通过 API 封装现有脚本、任务状态和多模型配置。

## 启动

```bash
pip install -r requirements.txt
uvicorn backend.app.main:app --reload --port 8080
```

健康检查：

```bash
curl http://127.0.0.1:8080/api/health
```

## 多模型配置

默认读取 `backend/config.example.json`。生产环境建议复制为本地文件，并通过环境变量指定：

```bash
cp backend/config.example.json backend/config.local.json
export PPT_MASTER_LLM_CONFIG=backend/config.local.json
export OPENAI_API_KEY=sk-...
```

配置原则：

- 每个 provider 使用自己的环境变量，例如 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`QWEN_API_KEY`。
- `profile` 绑定具体模型、接入点和可承担角色。
- 任务通过 `profile_id` 选择实际模型。
- API 展示时只返回密钥环境变量名和是否已设置，不返回密钥值。

## 常用 API

查看模型配置：

```bash
curl http://127.0.0.1:8080/api/model-profiles
```

本地校验模型配置：

```bash
curl -X POST http://127.0.0.1:8080/api/model-profiles/test \
  -H 'Content-Type: application/json' \
  -d '{"profile_id":"openai_default","live":false}'
```

创建项目：

```bash
curl -X POST http://127.0.0.1:8080/api/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"demo","canvas_format":"ppt169"}'
```

导入资料：

```bash
curl -X POST http://127.0.0.1:8080/api/projects/demo_ppt169_20260514/sources \
  -H 'Content-Type: application/json' \
  -d '{"items":["/absolute/path/source.pdf"],"mode":"copy"}'
```

生成 Strategist 起步计划：

```bash
curl -X POST http://127.0.0.1:8080/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "mode":"agent_plan",
    "project_path":"demo_ppt169_20260514",
    "profile_id":"openai_default",
    "prompt":"做一份面向管理层的 12 页汇报"
  }'
```

执行后处理导出：

```bash
curl -X POST http://127.0.0.1:8080/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"mode":"export","project_path":"demo_ppt169_20260514"}'
```

## 当前边界

当前版本是后端 MVP：

- 已支持模型 profile、项目创建、资料导入、任务状态、导出任务、`agent_plan.md` 生成。
- 逐页 SVG Executor 尚未自动化接管；后续应在 `PipelineRunner._agent_plan` 之后新增 `design_spec`、`spec_lock`、`executor_page` 等阶段。
- 状态存储使用 `backend/runtime/state.json`，适合本地开发；生产部署建议替换为数据库和 Redis 队列。

