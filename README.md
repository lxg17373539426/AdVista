# AdVista Insight Agent

AdVista Insight Agent is an evidence-grounded multimodal agent for advertising-video understanding and marketing decision support. It turns a local video into timestamped speech and OCR evidence, a unified Evidence Ledger, citation-validated insights, risk audits, reports, and creative deliverables.

This repository contains the application code and tests. Model weights, uploaded videos, generated reports, databases, logs, and other runtime artifacts are intentionally not stored in Git.

Design document: [`docs/PROJECT_PLAN.zh-CN.md`](docs/PROJECT_PLAN.zh-CN.md)

## Features

- Deterministic video ingestion, shot detection, keyframe extraction, and artifact caching.
- Timestamped Faster-Whisper speech evidence.
- Grounded DeepSeek-OCR evidence with PaddleOCR fallback.
- A unified Evidence Ledger for cross-modal grounding and conflict tracking.
- AdInsight-RL planning, structured advertising insights, critique, and multi-turn chat.
- Explicit deliverables: evidence, insights, risk audit, report, and creative package.
- Local Web workspace with upload, progress, timeline, chat, and export support.

## Models

- **AdInsight-RL**: the RL-trained model used for planning, reasoning, critique, and deliverables. Download it from [ModelScope](https://www.modelscope.cn/models/luxiaoguo123/AdInsight-RL). The weights are not included in this repository.
- **Faster-Whisper**: timestamped speech recognition. Install with [`requirements/asr.txt`](requirements/asr.txt).
- **DeepSeek-OCR**: OCR with PaddleOCR fallback. See [`requirements/deepseek-ocr.txt`](requirements/deepseek-ocr.txt).

The default configuration expects the RL checkpoint at `models/Qwen3.5-9B-GSPO-Step300-Merged`. Any local paths can be changed in the ignored `configs/local.yaml` file.

## Status

Stage 1 implements deterministic video ingestion. Stage 2 adds a normalized shot timeline and primary keyframes. Stage 3 adds timestamped Faster-Whisper speech evidence. Stage 4 adds grounded DeepSeek-OCR evidence with PaddleOCR fallback. Stage 5 builds a deterministic unified Evidence Ledger. Stage 6 uses Qwen3.5-9B to generate citation-validated structured advertising insights. Stage 7 audits accepted insights and generates Markdown/HTML deliverables. Stage 8 provides persistent fixed-pipeline orchestration and recovery. Stage 9 adds the first bounded Agent Core with goal-driven Qwen planning, local plan compilation, validated tool selection, observations, reflection, budgets, recovery, and human gates. Stage 10 adds SQLite-backed multi-turn conversations, answer versions, feedback, and citation-guarded follow-up questions over existing artifacts without rerunning ASR or OCR. Stage 11 adds citation-guarded Hooks, scripts, storyboards, and A/B creative variants. See [`docs/STAGE_11.md`](docs/STAGE_11.md), [`docs/STAGE_10.md`](docs/STAGE_10.md), and [`docs/STAGE_9.md`](docs/STAGE_9.md).

This repository is a research prototype. Keep the Web server bound to `127.0.0.1`; it does not provide production authentication or tenant isolation.

## Requirements

- Linux with Python 3.11-3.13.
- FFmpeg and ffprobe available on `PATH`.
- An NVIDIA GPU and compatible CUDA stack for the default ASR, OCR, and vLLM configuration.
- AdInsight-RL downloaded outside Git, for example to `models/Qwen3.5-9B-GSPO-Step300-Merged`.
- `modelscope` if downloading the model from ModelScope with the command below.

## Installation

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
python -m venv .venv-asr
.venv-asr/bin/python -m pip install -r requirements/asr.txt
.venv/bin/python -m pip install modelscope
```

Download the RL model to a directory outside Git, or use the repository-local `models/` directory, which is ignored:

```bash
.venv/bin/python -c "from modelscope import snapshot_download; snapshot_download('luxiaoguo123/AdInsight-RL', local_dir='models/Qwen3.5-9B-GSPO-Step300-Merged')"
```

Create the machine-specific configuration and update its paths:

```bash
cp configs/default.yaml configs/local.yaml
# Edit configs/local.yaml: model_root, video_data, and interpreter paths.
.venv/bin/advista-agent doctor --config configs/local.yaml
```

## Command-Line Usage

```bash
.venv/bin/advista-agent ingest /path/to/ad.mp4
.venv/bin/advista-agent timeline /path/to/ad.mp4
.venv/bin/advista-agent speech /path/to/ad.mp4
.venv/bin/advista-agent ocr /path/to/ad.mp4
.venv/bin/advista-agent ledger /path/to/ad.mp4
.venv/bin/advista-agent insights /path/to/ad.mp4
.venv/bin/advista-agent report /path/to/ad.mp4
.venv/bin/advista-agent run /path/to/ad.mp4
.venv/bin/advista-agent agent-plan --goal "只提取字幕和语音证据" --deliverable evidence --qwen
.venv/bin/advista-agent agent /path/to/ad.mp4 --goal "分析卖点、受众和风险并生成报告" --qwen-planner
.venv/bin/advista-agent agent-resume ingest_<asset-hash> --approve
.venv/bin/advista-agent chat ingest_<asset-hash> "这个广告最核心的卖点是什么？"
.venv/bin/advista-agent creative /path/to/ad.mp4
.venv/bin/advista-agent serve --config configs/local.yaml
.venv/bin/advista-agent session-show ingest_<asset-hash>
.venv/bin/advista-agent feedback ingest_<asset-hash> approve "回答边界清晰" --message-id msg_<id>
.venv/bin/advista-agent resume ingest_<asset-hash> --approve-review
.venv/bin/advista-agent clean
.venv/bin/python -m unittest discover -s tests -v
```

`configs/local.yaml` is ignored by Git. Commands without `--config` use portable packaged defaults, while production deployments should use a local configuration with paths appropriate for the machine.

Start the persistent Qwen service before `doctor`, `insights`, or the complete `run` command:

```bash
export QWEN_MODEL_PATH=/path/to/Qwen3.5-9B-GSPO-Step300-Merged
export QWEN_VIDEO_DIR=/path/to/allowed/video/directory
export QWEN_PYTHON=/path/to/vllm/python
bash scripts/start_qwen_service.sh
```

Open `http://127.0.0.1:8080` after starting the Web service. VS Code Remote users can forward port `8080` from the Ports panel.

The Web server is intentionally bound to `127.0.0.1` and has no production authentication or tenant isolation. Do not expose it directly to the public internet.

The Web composer `+` menu can attach a video and explicitly select Evidence, Insights, Risk Audit, Report, or Creative deliverables. If no deliverable is selected, AdInsight-RL infers the requested deliverables from the user's natural-language goal. Explicit selections are authoritative and the Agent executes only their minimum dependency closure.

`run` persists orchestration state in `outputs/runs/<run-id>/orchestration/state.json`. A failed run can be continued with `resume <run-id>`. A Critic `review` result pauses delivery until `--approve-review` is supplied; approval is recorded without altering the audit.

Qwen Stage 6 uses the configured persistent OpenAI-compatible vLLM service by default. Inference optimization reports: [中文报告](docs/INFERENCE_OPTIMIZATION.zh-CN.md) | [English report](docs/INFERENCE_OPTIMIZATION.md).

Stage 12 adds a local Web workspace for upload, progress, timeline inspection, chat, and exports. See [`docs/STAGE_12.md`](docs/STAGE_12.md).

## Development

Run the test suite after installing the development extra:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

The current test suite contains 103 tests. The default test suite does not require downloading model weights or processing a real advertisement video.

## License

No license has been selected for this repository yet. Until a `LICENSE` file is added, all rights are reserved by the copyright holder.
