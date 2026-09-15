# AdVista Insight Agent

AdVista Insight Agent 是一个用于广告视频理解和营销分析的多模态 Agent。它可以从本地广告视频中提取广告卖点相关证据，并生成卖点分析报告、营销策略建议以及创意方案。风险和功效声明审计作为内部质量控制阶段运行，不再作为独立用户任务。

Agent 控制层使用 **LangGraph ReAct**，使用 LangChain 的 Structured Tools 接入现有视频分析工具，并通过 vLLM 调用 AdInsight-RL。原有确定性的证据流水线仍作为工具执行层，确保工具依赖、证据引用和审计规则不会被模型绕过。

## 使用的模型

- **AdInsight-RL**：项目的核心推理、规划、报告和创意生成模型。
  - 模型地址：https://www.modelscope.cn/models/luxiaoguo123/AdInsight-RL
- **Faster-Whisper**：提取广告视频中的语音和字幕信息。
- **DeepSeek-OCR**：提取视频画面中的文字信息。
- **PySceneDetect**：检测视频镜头和场景切换。


## 安装

建议使用 Linux、Python 3.11-3.13 和 NVIDIA GPU。

```bash
cd AdVista
python -m venv .venv
.venv/bin/python -m pip install -e .
python -m venv .venv-asr
.venv-asr/bin/python -m pip install -r requirements/asr.txt
```

下载 AdInsight-RL：

```bash
.venv/bin/python -m pip install modelscope
.venv/bin/python -c "from modelscope import snapshot_download; snapshot_download('luxiaoguo123/AdInsight-RL', local_dir='models/Qwen3.5-9B-GSPO-Step300-Merged')"
```

同时需要系统中安装 `ffmpeg` 和 `ffprobe`，并准备 OCR、vLLM 所需的运行环境。

## 启动

### 1. 配置模型路径

```bash
cp configs/default.yaml configs/local.yaml
```

编辑 `configs/local.yaml`，至少确认以下路径：

```yaml
paths:
  model_root: /path/to/models
  video_data: /path/to/videos
  output_root: /path/to/outputs
```

根据本机环境修改 ASR、OCR 和 vLLM 的 Python 路径。`configs/local.yaml` 不会上传到 GitHub。

如需启用面向外部服务的 PostgreSQL 元数据存储，通过环境变量配置连接，不要把密码写入 YAML：

```bash
export ADVISTA_DATABASE_URL='postgresql+psycopg://advista:password@127.0.0.1:5432/advista'
.venv/bin/alembic upgrade head
.venv/bin/advista-agent database-check --config configs/local.yaml
```

数据库目前作为可选基础设施接入；未设置该环境变量时，现有 SQLite 对话和文件状态流程保持不变。

如果需要把已有 SQLite 普通对话迁移到 PostgreSQL，先启动 PostgreSQL 并加载 `.env`，然后执行：

```bash
set -a
source .env
set +a
advista-agent migrate-conversations --config configs/local.yaml
```

该迁移可以重复执行，已有会话、消息、反馈和回答版本会被跳过，不会重复导入。

已有 Web Job JSON 可以回填为执行记录：

```bash
advista-agent migrate-jobs --config configs/local.yaml
```

已有任务的上下文制品哈希可以回填为历史上下文版本：

```bash
advista-agent migrate-contexts --config configs/local.yaml
```

查询某个 Job 时，服务会在返回旧 Job 状态的同时补充 `database_execution`，其中包含数据库中的执行状态、当前阶段、错误码和开始/完成时间；轮询运行中的 Job 还会把 `session.json` 中的工具调用同步到 PostgreSQL。

视频任务完成后会发布上下文版本，记录本次执行使用的模型、Prompt、schema 版本以及 asset、timeline、speech、OCR、Ledger、insights 和 report 制品哈希。上下文版本只追加，不覆盖历史版本。

管理员可以使用管理员 API Key 查询执行诊断：

```bash
curl -H "X-API-Key: <admin-api-key>" \
  "http://127.0.0.1:8080/api/admin/executions?status=failed&limit=20"
curl -H "X-API-Key: <admin-api-key>" \
  "http://127.0.0.1:8080/api/admin/executions/<execution-id>"
curl -H "X-API-Key: <admin-api-key>" \
  "http://127.0.0.1:8080/api/admin/stats"
curl -H "X-API-Key: <admin-api-key>" \
  "http://127.0.0.1:8080/api/admin/executions/export?status=failed"
```

诊断详情包含请求摘要、Execution 状态、工具调用时间线和上下文版本；统计接口提供状态、错误码、工具和模型聚合；导出接口只提供脱敏执行元数据。普通用户不能访问这些接口。

可以通过 cron 定期生成脱敏质量报表：

```bash
advista-agent quality-report --config configs/local.yaml
```

默认输出到 `outputs/diagnostics/quality-report.json`。

删除用户数据库记录前先预览：

```bash
advista-agent user-delete user_xxx --config configs/local.yaml
```

确认后执行永久删除：

```bash
advista-agent user-delete user_xxx --apply --config configs/local.yaml
```

该命令删除用户的会话、消息、请求、Execution、Tool Call、上下文和反馈记录；共享的内容寻址 Run 制品不会自动删除。

发布前可以手动生成 PostgreSQL 备份：

```bash
set -a
source .env
set +a
bash scripts/backup_postgres.sh
```

备份文件默认写入 `outputs/backups/`，该目录不会提交到 Git。

受控发布模板位于 `deploy/`：

- `deploy/Caddyfile.example`：HTTPS 反向代理模板。
- `deploy/advista.service.example`：systemd 服务模板。
- `configs/production.example.yaml`：仅监听本机、启用 API Token 和用户任务配额的生产配置模板。

正式部署前必须替换示例域名、数据库密码和管理员 Token，并将 PostgreSQL、vLLM 和应用内部端口限制在私有网络。

可以在不修改数据库的情况下验证备份归档：

```bash
bash scripts/verify_postgres_backup.sh outputs/backups/advista-<timestamp>.dump
```

当前开发机已在项目目录的 `.postgresql` 中建立本地 PostgreSQL，监听
`127.0.0.1:55432`。连接凭据保存在被 Git 忽略的 `.env`。启动服务前运行：

```bash
bash scripts/start_local_postgres.sh
set -a
source .env
set +a
advista-agent database-check --config configs/local.yaml
advista-agent serve --config configs/local.yaml
```

停止本地数据库：

```bash
bash scripts/stop_local_postgres.sh
```

本机联调可以一次启动数据库、AdInsight-RL 和 Web 服务：

```bash
bash scripts/start_local_stack.sh
```

本地地址：

- 工作台：`http://127.0.0.1:8080`
- 管理员诊断：`http://127.0.0.1:8080/admin`
- 模型 API：`http://127.0.0.1:8000/v1`

停止整套本地服务：

```bash
bash scripts/stop_local_stack.sh
```

当前本地测试服务只绑定 `127.0.0.1`，不会接受公网访问。

### 2. 启动 AdInsight-RL 服务

```bash
export QWEN_MODEL_PATH=/path/to/Qwen3.5-9B-GSPO-Step300-Merged
export QWEN_VIDEO_DIR=/path/to/videos
export QWEN_PYTHON=/path/to/vllm/bin/python
bash scripts/start_qwen_server.sh
```


## 命令行运行

```bash
.venv/bin/advista-agent agent /path/to/ad.mp4 \
  --goal "分析广告卖点、受众、风险并生成报告" \
  --qwen-planner \
  --backend langgraph \
  --config configs/local.yaml
```

也可以直接运行完整流程：

```bash
.venv/bin/advista-agent run /path/to/ad.mp4 --config configs/local.yaml
```

Agent 的三个可选交付任务为：

- `report`：提取证据并生成卖点分析报告，包含卖点、受众线索、创意结构、转化路径和内部风险审计。
- `strategy`：基于已验证卖点生成定位、传播、渠道和转化建议，并区分事实、推论和待验证假设。
- `creative`：基于证据生成 Hook、脚本、分镜、CTA 和 A/B 方案。

证据提取是上述交付任务的共享内部前置阶段；视频问答可在任务完成后继续使用。系统不会接受新的 `risk_audit` 公开任务，但会保留历史任务读取和报告内部的风险审计。
