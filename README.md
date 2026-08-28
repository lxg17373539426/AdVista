# AdVista Insight Agent

AdVista Insight Agent 是一个用于广告视频理解和营销分析的多模态 Agent。它可以从本地广告视频中提取语音、画面文字和时间线证据，并生成广告洞察、风险审计、分析报告以及创意方案。

Agent 控制层使用 **LangGraph ReAct**，使用 LangChain 的 Structured Tools 接入现有视频分析工具，并通过 vLLM 调用 AdInsight-RL。原有确定性的证据流水线仍作为工具执行层，确保工具依赖、证据引用和审计规则不会被模型绕过。

## 使用的模型

- **AdInsight-RL**：项目的核心推理、规划、报告和创意生成模型。
  - 模型地址：https://www.modelscope.cn/models/luxiaoguo123/AdInsight-RL
- **Faster-Whisper**：提取广告视频中的语音和字幕信息。
- **DeepSeek-OCR**：提取视频画面中的文字信息。
- **PaddleOCR**：DeepSeek-OCR 不可用时的 OCR 备用方案。
- **PySceneDetect**：检测视频镜头和场景切换。

模型权重不会上传到 GitHub，需要在本地单独下载。

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

### 2. 启动 AdInsight-RL 服务

```bash
export QWEN_MODEL_PATH=/path/to/Qwen3.5-9B-GSPO-Step300-Merged
export QWEN_VIDEO_DIR=/path/to/videos
export QWEN_PYTHON=/path/to/vllm/bin/python
bash scripts/start_qwen_server.sh
```

### 3. 启动 Web 页面

```bash
.venv/bin/advista-agent serve --config configs/local.yaml
```

浏览器打开：

```text
http://127.0.0.1:8080
```

如果使用 VSCode Remote SSH，可以在 VSCode 的 `PORTS` 面板转发 `8080` 端口，然后点击 `Open in Browser`。

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

运行结果保存在 `outputs/` 目录中。视频、模型权重和运行结果默认不会提交到 GitHub。

## 注意

Web 服务默认只监听本机地址 `127.0.0.1`，不建议直接暴露到公网。
