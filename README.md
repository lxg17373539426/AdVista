# AdVista Insight Agent

## 把广告视频变成可验证、可执行的营销洞察

AdVista 是一个面向广告团队、品牌市场团队和内容创意团队的视频营销分析工作台。上传一条广告视频，系统会提取镜头、关键帧、语音和画面文字，建立可定位的多模态证据链，再生成卖点分析、营销策略、广告创意和视频问答结果。

AdVista 不只回答「视频讲了什么」，还会区分广告直接表达、画面观察、营销推断和待验证假设，避免把模型生成内容直接当成事实。

**[观看 2 分 04 秒完整产品演示](docs/assets/advista-real-demo.mp4)**

演示直接录制自 AdVista 工作台，展示视频上传、任务执行、卖点分析报告、报告下载、多模态证据文档，以及基于当前视频的连续追问。

![AdVista 工作台](docs/assets/workbench.png)

## 核心能力

### 从一条视频生成营销交付物

同一份广告素材可以生成不同类型的交付结果：

| 任务 | 输出 |
| --- | --- |
| 卖点分析报告 | 产品、核心卖点、受众线索、创意结构、转化路径和内部风险审计 |
| 营销策略建议 | 定位、核心传播信息、内容方向、渠道建议和转化建议 |
| 广告创意生成 | Hook、口播脚本、分镜、CTA 和 A/B 测试方案 |
| 视频问答 | 基于当前视频证据回答画面、语音、字幕和时间范围问题 |

### 每个结论都能回到视频证据

报告结论可以关联到具体时间段、关键帧、ASR 语音内容和 OCR 画面文字。证据文档以单文件 HTML 展示关键帧画廊和原始文字证据，下载后仍可离线查看。

![AdVista 多模态证据链](docs/assets/evidence-chain.png)

系统对洞察进行结构和引用校验，并保留置信度、审计状态与证据缺口。需要人工判断的内容会进入「建议复核」，不会替用户自动批准。

![AdVista 卖点分析报告](docs/assets/report-preview.png)

### 核心链路可以在本地运行

视频解析、Faster-Whisper、DeepSeek-OCR 和 AdInsight-RL 推理可以运行在团队自己的 GPU 环境中。核心分析流程不依赖公共大模型 API，适合对素材保密性、部署自主权和推理成本有要求的团队。

```text
广告视频
   ↓
镜头检测与关键帧提取
   ↓
ASR 语音识别 + OCR 画面文字识别
   ↓
Evidence Ledger
   ↓
本地 AdInsight-RL
   ↓
报告 / 策略 / 创意 / 视频问答
```

## 产品工作流

```text
上传广告视频
      ↓
选择任务或直接描述分析目标
      ↓
提取镜头、关键帧、语音和画面文字
      ↓
建立统一、可定位的多模态证据链
      ↓
生成并校验证据约束的营销洞察
      ↓
在线查看、继续追问或下载交付物
```

一次视频任务会被保存为独立会话。刷新页面或重新打开历史记录后，原始问题、回答、报告、证据链和下载入口仍然可用。

## 演示提示词

上传视频并选择「卖点分析报告」后，可以使用：

```text
请分析这条广告视频，识别它推广的产品、核心卖点、目标受众、创意结构和转化路径，并生成一份带视频证据引用的卖点分析报告。请区分广告直接表达的内容、画面观察、营销推断和仍需验证的信息。
```

报告完成后，可以在同一个视频会话中继续提问：

```text
视频前 5 秒展示了什么？这个开场 Hook 想吸引用户关注什么？请把事实描述和营销推断分开。
```

```text
请整理视频中的语音内容和画面文字，并说明哪些卖点来自 ASR，哪些来自 OCR。如果识别结果存在噪声或不确定性，也请指出。
```

```text
基于已经验证的卖点，为这款产品制定一份营销策略。请给出产品定位、目标受众、核心传播信息、内容方向、渠道建议和转化建议，并区分事实依据、营销推断和待验证假设。
```

## 技术架构

Agent 控制层使用 LangGraph ReAct，通过 LangChain Structured Tools 接入确定性视频分析工具，并通过 vLLM 调用 AdInsight-RL。

```text
Web 工作台 / CLI
       ↓
LangGraph Agent
       ↓
确定性工具流水线
       ├── 视频登记与媒体探测
       ├── PySceneDetect 镜头检测
       ├── 关键帧提取
       ├── Faster-Whisper ASR
       ├── DeepSeek-OCR
       └── Evidence Ledger
       ↓
AdInsight-RL via vLLM
       ↓
结构校验 + 引用校验 + 风险审计
       ↓
报告 / 策略 / 创意 / 问答
```

模型负责理解、规划和生成；确定性流程负责工具依赖、证据来源、制品哈希、引用约束和输出审计。

## 模型与组件

| 组件 | 用途 |
| --- | --- |
| [AdInsight-RL](https://www.modelscope.cn/models/luxiaoguo123/AdInsight-RL) | 核心推理、规划、报告和创意生成 |
| Faster-Whisper | 广告语音识别和词级时间戳 |
| DeepSeek-OCR | 视频关键帧文字识别 |
| PySceneDetect | 镜头切分和场景变化检测 |
| vLLM | 本地 OpenAI 兼容模型服务 |
| PostgreSQL / SQLite | 会话、任务、回答版本、反馈和执行元数据 |

## 环境要求

- Linux
- Python 3.11–3.13
- NVIDIA GPU
- `ffmpeg` 和 `ffprobe`
- 可用的 ASR、OCR 和 vLLM Python 环境

## 本地安装

安装 AdVista 主程序：

```bash
git clone https://github.com/lxg17373539426/AdVista.git
cd AdVista
python -m venv .venv
.venv/bin/python -m pip install -e .
```

安装独立 ASR 环境：

```bash
python -m venv .venv-asr
.venv-asr/bin/python -m pip install -r requirements/asr.txt
```

下载 AdInsight-RL：

```bash
.venv/bin/python -m pip install modelscope
.venv/bin/python -c "from modelscope import snapshot_download; snapshot_download('luxiaoguo123/AdInsight-RL', local_dir='models/Qwen3.5-9B-GSPO-Step300-Merged')"
```

OCR 和 vLLM 依赖与 GPU、CUDA 版本有关，建议分别使用独立环境，避免与主程序或 ASR 依赖冲突。

## 本地配置

复制默认配置：

```bash
cp configs/default.yaml configs/local.yaml
```

至少确认模型、视频和输出路径：

```yaml
paths:
  model_root: /path/to/models
  video_data: /path/to/videos
  output_root: /path/to/outputs
```

同时检查以下解释器路径：

```yaml
asr:
  python_executable: /path/to/asr/python

ocr:
  deepseek_python: /path/to/ocr/python

insight:
  python_executable: /path/to/vllm/python
```

`configs/local.yaml`、`.env`、模型、数据库、视频和运行制品不会上传到 GitHub。

## 启动服务

### 通用启动流程

先启动 AdInsight-RL：

```bash
export QWEN_MODEL_PATH=/path/to/Qwen3.5-9B-GSPO-Step300-Merged
export QWEN_VIDEO_DIR=/path/to/videos
export QWEN_PYTHON=/path/to/vllm/bin/python
bash scripts/start_qwen_server.sh
```

再启动 Web 服务：

```bash
.venv/bin/advista-agent serve --config configs/local.yaml
```

默认服务地址：

| 服务 | 地址 |
| --- | --- |
| 工作台 | `http://127.0.0.1:8080` |
| 管理员诊断 | `http://127.0.0.1:8080/admin` |
| 模型 API | `http://127.0.0.1:8000/v1` |

默认服务只绑定 `127.0.0.1`，不会接受公网访问。

检查完整运行环境：

```bash
.venv/bin/advista-agent doctor --config configs/local.yaml
```

`doctor` 会检查模型目录、FFmpeg、ASR、OCR、推理解释器和模型 API。Web `/api/health` 只检查应用与可选数据库，不代表完整模型工具链可用。

### 当前开发机联调脚本

当前开发机已在项目目录的 `.postgresql` 中准备本地 PostgreSQL，并在 `configs/local.yaml` 和 `.env` 中保存被 Git 忽略的本机配置。该环境可以使用：

```bash
bash scripts/start_local_stack.sh
```

本机 PostgreSQL 监听 `127.0.0.1:55432`。停止整套联调服务：

```bash
bash scripts/stop_local_stack.sh
```

`start_local_stack.sh` 包含当前开发机的默认模型路径和解释器路径。其他机器应先修改脚本使用的环境变量，或采用前述通用启动流程。

## 数据库

数据库是可选基础设施。未配置 PostgreSQL 时，应用使用 SQLite；启用 PostgreSQL 后，会话、消息、回答版本、反馈和执行元数据统一写入数据库。

```bash
export ADVISTA_DATABASE_URL='postgresql+psycopg://advista:password@127.0.0.1:5432/advista'
.venv/bin/alembic upgrade head
.venv/bin/advista-agent database-check --config configs/local.yaml
```

连接凭据只应通过环境变量或部署密钥提供，不要写入 YAML、源码或 Git 远程地址。

迁移已有 SQLite 会话：

```bash
set -a
source .env
set +a
.venv/bin/advista-agent migrate-conversations --config configs/local.yaml
```

回填已有任务和上下文版本：

```bash
.venv/bin/advista-agent migrate-jobs --config configs/local.yaml
.venv/bin/advista-agent migrate-contexts --config configs/local.yaml
```

迁移命令可以重复执行，已存在的记录会被跳过。SQLite 历史不会在启用 PostgreSQL 后自动复制。

## 命令行使用

通过 Agent 运行：

```bash
.venv/bin/advista-agent agent /path/to/ad.mp4 \
  --goal "分析广告卖点、受众、风险并生成报告" \
  --qwen-planner \
  --backend langgraph \
  --config configs/local.yaml
```

直接运行完整流程：

```bash
.venv/bin/advista-agent run /path/to/ad.mp4 --config configs/local.yaml
```

公开交付任务包括：

- `report`：卖点分析报告；
- `strategy`：营销策略建议；
- `creative`：广告创意方案。

证据提取是三个任务共享的内部前置阶段。任务完成后，可以继续围绕当前视频进行问答。

## 公网部署

如果模型继续运行在本地 GPU 机器上，只应公开 HTTPS 网关：

```text
外部用户 -- HTTPS --> Caddy -- 127.0.0.1:8080 --> AdVista Web
                                      └--------> 127.0.0.1:8000 vLLM
```

不要将 `8000`、`8080` 或 `55432` 直接暴露到公网。生产模板位于：

- `deploy/Caddyfile.example`
- `deploy/advista-gateway.service.example`
- `deploy/advista.service.example`
- `configs/production.example.yaml`

复制生产配置：

```bash
cp configs/production.example.yaml configs/production.yaml
```

生产环境应启用数据库用户认证：

```yaml
web:
  host: 127.0.0.1
  port: 8080
  require_api_auth: true
```

为用户创建一次性 API Key：

```bash
.venv/bin/advista-agent user-create user@example.com --config configs/production.yaml
```

程序调用可以发送：

```bash
curl -H 'X-API-Key: avk_...' https://advista.example.com/api/health
```

有公网 IP 时，只将 TCP `80/443` 转发到 Caddy；没有公网 IP 时，可以使用 Cloudflare Tunnel、Tailscale Funnel 或同类出站隧道。

## 运维与数据治理

生成脱敏质量报告：

```bash
.venv/bin/advista-agent quality-report --config configs/local.yaml
```

预览并删除指定用户数据：

```bash
.venv/bin/advista-agent user-delete user_xxx --config configs/local.yaml
.venv/bin/advista-agent user-delete user_xxx --apply --config configs/local.yaml
```

备份并验证 PostgreSQL：

```bash
bash scripts/backup_postgres.sh
bash scripts/verify_postgres_backup.sh outputs/backups/advista-<timestamp>.dump
```

管理员诊断 API 支持查询执行记录、失败原因、工具调用时间线、上下文版本和脱敏统计。管理员入口与 Token 不应暴露给普通用户。

## 开发与验证

安装开发依赖：

```bash
.venv/bin/python -m pip install -e '.[dev]'
```

运行检查：

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check ad_vista_agent tests scripts
.venv/bin/python -m mypy ad_vista_agent --ignore-missing-imports
node --check ad_vista_agent/web/static/app.js
```

普通测试使用临时目录、隔离数据库和假模型，不要求真实 GPU 模型服务。PostgreSQL 集成测试默认跳过，需要通过专用测试数据库环境变量启用；不要将测试指向用户数据库。

## 说明

AdVista 的输出用于辅助分析和创作。广告主张、产品功效、受众属性、合规性和转化效果仍需结合原始素材与实际业务进行人工核验和测试。
