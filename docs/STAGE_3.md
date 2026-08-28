# Stage 3: Timestamped Speech Evidence

## Boundary

Stage 3 implements only:

- isolated Faster-Whisper inference on one configured H20 GPU;
- VAD and language detection;
- segment- and word-level timestamps normalized to integer milliseconds;
- deterministic whitespace cleanup, duplicate removal, and adjacent-segment merging;
- validated `SpeechTranscript` and standard `Evidence` artifacts;
- model-, parameter-, and version-aware caching;
- ASR latency and real-time-factor metrics;
- valid empty transcripts for assets without an audio stream.

Stage 3 does not implement OCR, Qwen inference, object detection, music analysis, marketing insights, Agent planning, reports, a Web UI, or full-dataset scheduling.

## Processing Flow

```text
AdAsset + Timeline
  ↓
检查音轨
  ↓
Faster-Whisper large-v3-turbo
  ↓
语言识别 + VAD + 片段/词级时间戳
  ↓
时间范围归一化与确定性清洗
  ↓
SpeechTranscript Schema 校验
  ↓
转换为 stated_by_ad Speech Evidence
  ↓
写入 transcript.json + evidence.jsonl + metrics
  ↓
相同视频、模型、版本和参数命中缓存
```

## Environment

ASR uses a dedicated environment to isolate CTranslate2/CUDA dependencies:

```bash
python -m venv .venv-asr
.venv-asr/bin/python -m pip install -r requirements/asr.txt
```

Pinned dependencies:

- Faster-Whisper 1.2.1;
- CTranslate2 4.8.1.

The main CLI remains in `.venv`. It launches the ASR worker using `.venv-asr/bin/python` and sets `CUDA_VISIBLE_DEVICES=0`. Inside that isolated process, the configured H20 is visible as device index `0`.

## Default Configuration

```yaml
asr:
  model: faster-whisper-large-v3-turbo-ct2
  device: cuda
  device_index: 0
  compute_type: float16
  beam_size: 5
  vad_filter: true
  condition_on_previous_text: false
  word_timestamps: true
  merge_gap_ms: 500
  timeout_seconds: 600
```

## Command

```bash
.venv/bin/advista-agent speech /path/to/ad.mp4
```

Use `--force` to ignore an otherwise valid Stage 3 cache entry.

## Artifacts

```text
outputs/runs/ingest_<asset-hash>/
├── speech/
│   ├── transcript.json
│   └── evidence.jsonl
├── stage_3_metrics.json
└── manifest.json
```

Each Evidence record includes:

- asset and evidence IDs;
- speech modality;
- segment start/end milliseconds;
- transcript text and confidence;
- `stated_by_ad` epistemic status;
- Faster-Whisper version;
- transcript artifact path and SHA-256;
- language and word-level timestamp metadata.

Low-confidence ASR text is retained with its confidence instead of being silently deleted. Evidence consumers must decide how to handle uncertainty.

## Cache Identity

The Stage 3 cache key includes:

- source video SHA-256;
- schema and stage name;
- model path, model config SHA-256, weight size, and weight mtime;
- Faster-Whisper and CTranslate2 versions;
- device and compute type;
- beam size, VAD, previous-text conditioning, and word timestamps;
- deterministic merge-gap configuration.

## Verification Results

Verified on 2026-08-09 with one NVIDIA H20 using FP16.

| Video | Duration | Language | Raw/Clean Segments | Words | Inference | RTF |
|---|---:|---|---:|---:|---:|---:|
| `00045b16...` | 57.049 s | English | 1 / 1 | 3 | 2.719 s | 0.0477 |
| `00fa2e3a...` | 10.751 s | Spanish | 3 / 1 | 37 | 2.703 s | 0.2514 |
| `003466d9...` | 18.737 s | English | 7 / 1 | 89 | 2.913 s | 0.1555 |
| `0006776c...` | 78.087 s | English | 17 / 6 | 183 | 3.761 s | 0.0482 |
| `005f54e1...` | 14.976 s | Spanish | 5 / 1 | 36 | 2.833 s | 0.1892 |

Short clips have a higher RTF because each CLI invocation starts and loads an isolated model process. A persistent service or batch worker is intentionally deferred to the later acceleration stage.

The retained Demo Run successfully returned `cache_hit: true` on a repeated execution. Twenty-three tests passed before final cleanup, including speech schemas, cleanup behavior, virtual-environment interpreter preservation, timeline compatibility, and maintenance safety.

## File Management

Generated file cleanup is explicit and preview-first:

```bash
# Preview Python caches and egg-info.
.venv/bin/advista-agent clean

# Apply generated-file cleanup.
.venv/bin/advista-agent clean --apply

# Preview removal of non-Demo runs.
.venv/bin/advista-agent clean --runs

# Apply while retaining the configured Demo Run.
.venv/bin/advista-agent clean --runs --apply
```

The cleanup command never targets `.venv`, `.venv-asr`, source files, documentation, model weights, or source videos.

## Stage Result

Stage 3 establishes the first timestamped semantic evidence stream:

```text
AdAsset + Timeline
  -> SpeechTranscript
  -> Speech Evidence
```

The next stage can add timestamped OCR Evidence using Stage 2 keyframes, but must not combine OCR and Qwen work into this completed ASR stage.
