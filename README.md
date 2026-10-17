# AdVista Insight Agent

## 把广告视频变成可验证、可执行的营销洞察

AdVista 是一个面向广告团队、品牌市场团队和内容创意团队的视频营销分析工作台。上传一条广告视频，系统会提取镜头、语音、画面文字和关键帧，生成带证据引用的卖点分析，并继续输出营销策略、广告创意和视频问答结果。

它不只回答「视频讲了什么」，还会标明哪些内容来自直接证据、哪些属于画面观察或营销推断，以及哪些判断仍然需要人工验证。

<p align="center">
  <img src="docs/assets/advista-demo.gif" alt="AdVista 产品演示" width="800">
</p>

<p align="center">
  <a href="docs/assets/advista-demo.mp4">观看 90 秒完整产品演示</a>
</p>

## 三个核心亮点

### 一条视频，生成完整营销交付物

从同一份广告素材中提取核心卖点、叙事结构和转化路径，并继续生成营销策略、广告脚本、分镜、CTA 和 A/B 创意方案。

![AdVista 脱敏工作台演示界面](docs/assets/workbench.png)

### 每个结论，都能回到视频证据

报告中的结论可以关联到具体时间段、关键帧、语音内容和画面文字。系统区分直接证据、画面观察、营销推断和待验证假设，减少把模型猜测当成事实的风险。

![AdVista 证据链展示](docs/assets/evidence-chain.png)

真实样例报告保留了审计结果、证据引用、置信度和当前证据缺口；需要人工检查的结论会进入「建议复核」，不会替用户自动批准。

![AdVista 真实广告分析报告](docs/assets/report-preview.png)

### 核心分析链路可在本地运行

视频解析、语音识别、OCR 和 9B 模型推理可以运行在团队自己的 GPU 环境中。核心流程不依赖公共大模型 API，适合对广告素材保密性、部署自主权和推理成本有要求的团队。

```text
广告视频
   ↓
镜头与关键帧 → ASR / OCR → Evidence Ledger
   ↓
本地 9B 模型
   ↓
卖点报告 / 营销策略 / 广告创意 / 视频问答
```

## 从视频到结果

```text
上传广告视频
      ↓
提取镜头、语音、画面文字和关键帧
      ↓
建立统一、可定位的视频证据链
      ↓
生成并校验证据约束的营销洞察
      ↓
输出报告、策略或广告创意
```

同一份证据可以继续用于不同营销任务：

![AdVista 营销交付物](docs/assets/strategy-creative.png)

> AdVista 的输出用于辅助分析和创作。广告主张、功效、受众属性和转化效果仍需结合实际业务进行人工核验和测试。

## 技术概览

Agent 控制层使用 **LangGraph ReAct**，通过 LangChain Structured Tools 接入视频分析工具，并通过 vLLM 调用 AdInsight-RL。确定性的证据流水线负责工具依赖、证据引用和输出审计，模型负责理解、规划与生成。

## 使用的模型

- **AdInsight-RL**：项目的核心推理、规划、报告和创意生成模型。
  - 模型地址：https://www.modelscope.cn/models/luxiaoguo123/AdInsight-RL
- **Faster-Whisper**：提取广告视频中的语音和字幕信息。
- **DeepSeek-OCR**：提取视频画面中的文字信息。
- **PySceneDetect**：检测视频镜头和场景切换。


## 安装

建议使用 Linux、Python 3.11-3.13 和 NVIDIA GPU。

```bash
cd AdVista-Agent
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

数据库为可选基础设施。启用后，Web 视频问答的消息、回答版本、历史和反馈统一使用 PostgreSQL；同一幂等键重试会返回原回答及原消息 ID、引用和报告链接。未配置数据库时使用 SQLite。已有 SQLite 历史不会自动复制到数据库，迁移需单独执行。

如果需要把已有 SQLite 普通对话迁移到 PostgreSQL，先启动 PostgreSQL 并加载 `.env`，然后执行：

```bash
set -a
source .env
set +a
advista-agent migrate-conversations --config configs/local.yaml
```

该迁移可以重复执行，已有会话、消息、反馈和回答版本会被跳过，不会重复导入。新版 SQLite 会话的手动标题会一并导入；重复迁移不会覆盖 PostgreSQL 中已有的名称。

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

### 公开访问网关

如果需要让外部人员使用，但不希望把 9B 模型迁移到云端，可以把整套服务继续运行在本地 GPU 机器，只公开 Caddy 网关：

```text
外部用户 -- HTTPS --> Caddy -- 127.0.0.1:8080 --> AdVista Web
                                      └--------> 127.0.0.1:8000 vLLM
```

不要开放 `8000`、`8080`、`55432` 到公网。复制并修改以下文件：

```bash
cp configs/production.example.yaml configs/production.yaml
cp deploy/gateway.env.example /etc/advista/gateway.env
```

生产配置必须启用数据库用户认证：

```yaml
web:
  host: 127.0.0.1
  port: 8080
  require_api_auth: true
```

`require_api_auth` 要求配置 `ADVISTA_DATABASE_URL`，每个外部用户使用独立 API Key。API Key 不写入仓库或 YAML，通过一次性命令创建：

```bash
advista-agent user-create user@example.com --config configs/production.yaml
```

命令输出的 `api_key` 只显示一次，应通过安全渠道交给对应用户。浏览器打开公网域名后输入 API Key；程序调用可直接发送：

```bash
curl -H 'X-API-Key: avk_...' https://advista.example.com/api/health
```

用户被停用后，已有浏览器会话也会失效：

```bash
advista-agent user-status user_xxx revoked --config configs/production.yaml
```

Caddy 反向代理配置位于 `deploy/Caddyfile.example`，它会自动申请 HTTPS、限制上传大小、添加安全响应头，并阻断管理员诊断入口。公网访问有两种方式：

- 有公网 IP：域名解析到本地网络，路由器只转发 TCP `80/443` 到 Caddy；不要转发 `8000/8080/55432`。
- 没有公网 IP：使用 Cloudflare Tunnel、Tailscale Funnel 或同类出站隧道，将域名只接到本机 Caddy。

公网入口服务示例位于 `deploy/advista-gateway.service.example`。长任务超时已在 Caddy 中设置为 20 分钟，但任务本身仍通过异步 Job API 执行。外部用户共享本地 GPU 的处理队列，生产环境应通过 `max_active_jobs`、`max_active_jobs_per_user`、`api_requests_per_minute` 和上传大小限制控制资源。

普通对话首轮回答及视频任务完成后，页面异步请求本地模型生成简短的内容摘要标题；模型不可用时使用用户问题提取标题，不影响回答显示。普通对话和视频任务分别保存在侧栏，每次视频执行均可独立打开。

点击侧栏会话旁或顶部的 `✎` 可重命名，标题上限为 80 个字符。手动名称会保存，刷新页面或继续聊天后仍保留，后续自动摘要不会覆盖。视频标题和原始任务目标分别显示，改名不会改变分析目标。

点击会话旁或顶部的垃圾桶按钮，确认后可从历史移除普通对话和视频任务。这里采用软删除，保留原视频、报告与历史数据；删除后旧会话链接不可继续访问，也不会被迟到的回答或标题重新创建。运行中的视频任务需要先取消并等待停止，再删除。

新生成的卖点报告采用浅色版式，以分析主题为标题，先展示摘要和核心卖点，再展示其他维度、审计发现和证据缺口。每条结论可以展开证据和来源信息，支持下载文字版和浏览器打印为 PDF。报告保留完整引用、置信度与审计结果；旧报告文件不自动改写。

洞察输出遇到结构或引用校验错误时，会基于同一份证据进行一次受限修正，并再次完整校验。外文 OCR 的中文解释保留可核对的短原文引用；修正失败的输出不会交付，页面会明确提示证据校验失败。若审计建议复核，任务进入“待确认”，需要用户查看报告并决定是否批准。

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

## 取消任务与停止服务

取消任务会向规划、工具和模型 HTTP 请求传播取消信号。可取消 HTTP 使用当前 Python 的轻量客户端进程，保留流式输出；请求通过 stdin 传输，取消或超时会终止客户端并断开连接，无需重启模型服务。

Web 服务接收 SIGTERM 或 Ctrl+C 时执行清理：拒绝新任务与聊天，取消队列和运行任务，等待正在执行的操作退出，再关闭数据库和监听 socket。取消在 JSON 和数据库中记录为 cancelled。已经提交的回答仍保留原终态，支持重放。

服务端是否立即停止 GPU 推理取决于模型服务的断连处理；应用关闭会等待不支持取消的纯计算函数完成。多主机使用不同输出目录时仍需要分布式任务协调。

## 开发与验证

使用独立虚拟环境安装开发依赖，避免系统 Python 中其他项目的依赖冲突：

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check ad_vista_agent tests scripts
.venv/bin/python -m mypy ad_vista_agent --ignore-missing-imports
node --check ad_vista_agent/web/static/app.js
```

普通测试使用临时目录、隔离数据库和假模型，不要求 GPU 或真实模型服务。取消回归会在 127.0.0.1 随机端口启动模拟模型，需要允许本机 socket。两项 PostgreSQL 集成测试默认跳过；隔离测试库通过 ADVISTA_TEST_DATABASE_URL 指定。视频一致性测试也可通过 ADVISTA_VIDEO_TEST_DATABASE_URL 在隔离 PostgreSQL 中执行，每项使用独立 schema。

标题存储测试也可通过 `ADVISTA_TITLE_TEST_DATABASE_URL` 使用隔离 PostgreSQL，每项创建并清理独立 schema；不要指向用户数据库。前端标题与会话切换行为回归需要 Node.js，通过 `pytest` 自动执行，无需访问模型或网络。

运行本机环境诊断时，使用已配置的 `configs/local.yaml`：

```bash
.venv/bin/advista-agent doctor --config configs/local.yaml
```

`doctor` 会检查模型目录、推理解释器及模型 API。模型文件存在但服务未启动时，诊断仍返回失败；先启动模型服务，再重试。Web `/api/health` 只检查应用及可选数据库，不证明模型、ASR 和 OCR 可用。
