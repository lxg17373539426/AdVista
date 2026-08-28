# Stage 1: Engineering Foundation and Video Ingestion

## Processing Flow

```text
广告视频
  ↓
文件与格式校验
  ↓
计算 SHA-256
  ↓
生成稳定 Asset ID / Run ID
  ↓
FFprobe 解析音视频元数据
  ↓
Pydantic Schema 校验
  ↓
原子写入 asset.json + manifest.json
  ↓
重复执行时命中缓存
```

## Boundary

Stage 1 implements only:

- Python package and CLI;
- validated YAML configuration;
- stable Asset, Evidence, Insight, Plan, and Report schemas;
- local video validation and SHA-256 fingerprinting;
- `ffprobe` metadata normalization;
- content-addressed run directories;
- atomic `asset.json` and `manifest.json` artifacts;
- environment diagnostics and unit tests.

Stage 1 explicitly does not implement:

- Qwen inference;
- ASR;
- OCR;
- scene detection or frame extraction;
- Agent planning and execution loops;
- marketing insight generation;
- reports or Web UI.

Those capabilities belong to later stages and must consume the Stage 1 schemas rather than bypass them.

## Configuration

The default configuration uses:

- one GPU only: device `0`, maximum GPU count `1`;
- models under `models/` or a locally configured external directory;
- videos under `data/videos/` or a locally configured external directory;
- artifacts under `outputs/`.

Stage 1 itself does not allocate the GPU.

## Commands

From the repository root:

```bash
python -m ad_vista_agent doctor
python -m ad_vista_agent ingest /path/to/video.mp4
python -m unittest discover -s tests -v
```

After installing the package:

```bash
python -m pip install -e .
advista-agent doctor
advista-agent ingest /path/to/video.mp4
```

## Artifacts

Ingestion writes:

```text
outputs/runs/ingest_<sha256-prefix>/
├── asset.json
└── manifest.json
```

Running ingestion again for the same file content returns `cache_hit: true`.

## Acceptance Criteria

- `doctor` verifies all configured paths, model directories, `ffprobe`, and the one-GPU boundary.
- A real MP4 produces valid media metadata, SHA-256, `asset.json`, and `manifest.json`.
- Re-ingesting the same video uses the content-addressed cached artifacts.
- Invalid paths and invalid Evidence intervals fail explicitly.
- All Stage 1 unit tests pass.

## Verification Record

Verified on 2026-08-09 with:

- Python 3.13.12;
- FFprobe 8.0;
- configured GPU device `0`: NVIDIA H20;
- model root: `models/`;
- video root: `data/videos/`.

Real-video verification used:

```text
/path/to/videos/example.mp4
```

Observed normalized metadata:

- duration: 57,049 ms;
- video: H.264, 720 x 1280, 25 FPS, 1,423 frames;
- audio: AAC, 44.1 kHz, stereo;
- file SHA-256: `eabdf3d87eb58a38feabbf4a35994466d861de83bccd3253e36357b4b5d7b9bf`.

The first ingestion wrote `asset.json` and `manifest.json`. The second ingestion returned `cache_hit: true`. Seven Stage 1 unit tests passed.
