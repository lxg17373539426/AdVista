# Stage 2: Video Timeline, Shot Detection, and Keyframes

## Boundary

Stage 2 implements only:

- a millisecond-based video timeline;
- PySceneDetect content-based shot detection;
- deterministic shot-boundary normalization;
- minimum shot-duration merging;
- one midpoint primary keyframe per normalized shot;
- FFmpeg JPEG extraction and FFprobe image validation;
- keyframe SHA-256 verification;
- configuration- and version-aware Stage 2 caching;
- timeline artifacts, metrics, and manifest provenance.

Stage 2 does not implement ASR, OCR, Qwen inference, object detection, audio analysis, marketing insights, Agent planning, reports, or a Web interface.

## Processing Flow

```text
Stage 1 AdAsset
  ↓
校验源文件 SHA-256 和媒体元数据
  ↓
建立统一毫秒时间轴
  ↓
PySceneDetect 检测镜头边界
  ↓
镜头边界归一化与极短镜头合并
  ↓
计算每个镜头的中点关键帧
  ↓
FFmpeg 抽帧 + FFprobe 图片校验
  ↓
校验关键帧归属、文件存在性和 SHA-256
  ↓
写入 Timeline、Keyframes、Metrics 和 Manifest
  ↓
相同视频、配置和工具版本命中缓存
```

## Environment Isolation

PySceneDetect 0.6.7.1 requires `click<8.3`, while the existing global Hugging Face environment requires a newer Click version. The project therefore uses an isolated `.venv` and does not rely on the global Python environment.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
```

## Configuration

Default Stage 2 parameters:

```yaml
timeline:
  detector: pyscenedetect_content
  scene_threshold: 27.0
  min_shot_ms: 500
  frame_format: jpg
  jpeg_quality: 2
```

Stage 2 runs on CPU and does not allocate the configured H20 GPU.

## Command

```bash
.venv/bin/advista-agent timeline /path/to/ad.mp4
```

Optional overrides:

```bash
.venv/bin/advista-agent timeline /path/to/ad.mp4 \
  --scene-threshold 30 \
  --min-shot-ms 750 \
  --force
```

## Artifacts

```text
outputs/runs/ingest_<asset-hash>/
├── asset.json
├── manifest.json
├── stage_2_metrics.json
└── timeline/
    ├── shots.json
    ├── keyframes.jsonl
    └── frames/
        ├── shot_0001_primary.jpg
        └── ...
```

The Stage 2 cache key includes:

- source video SHA-256;
- schema and stage name;
- detector name and version;
- scene threshold and minimum shot duration;
- FFmpeg version;
- image format and JPEG quality.

Changing a relevant parameter or tool version triggers recomputation. Recomputed frame directories remove stale Stage 2 primary frames before writing the new indexed set.

## Validation Rules

- The timeline starts at `0 ms` and ends at the asset duration.
- Adjacent shots are continuous and have no gap or overlap.
- Shot indices are continuous and shot IDs are unique.
- Invalid or empty detector output falls back to one full-video shot.
- A short first shot merges forward; other short shots merge into the previous shot.
- Every shot has exactly one primary keyframe.
- Each keyframe timestamp belongs to its shot.
- Each frame exists, is non-empty, has valid dimensions, and matches its recorded SHA-256.

## Verification Results

Verified on 2026-08-09 with:

- Python 3.13.12 in the project `.venv`;
- PySceneDetect 0.6.7.1;
- FFmpeg/FFprobe 8.0;
- default threshold `27.0`;
- default minimum shot duration `500 ms`.

Five real advertising videos were processed successfully:

| Video ID | Duration | Shots | Keyframes | Detection | Extraction | Total |
|---|---:|---:|---:|---:|---:|---:|
| `00045b16...` | 57.049 s | 29 | 29 | 0.970 s | 3.969 s | 4.987 s |
| `0006776c...` | 78.087 s | 10 | 10 | 1.997 s | 1.538 s | 3.646 s |
| `0020d899...` | 57.236 s | 24 | 24 | 1.347 s | 3.409 s | 4.842 s |
| `003466d9...` | 18.737 s | 9 | 9 | 0.416 s | 1.314 s | 1.774 s |
| `005f54e1...` | 14.976 s | 12 | 12 | 0.253 s | 1.355 s | 1.695 s |

These five files are real dataset samples but do not have manual labels for the planned categories such as single-shot narration or fade transitions. This is an engineering integration verification, not a manually categorized shot-detection benchmark.

Cache behavior was verified:

- a second run with unchanged configuration returned `cache_hit: true`;
- changing the threshold from `27` to `30` changed the cache key;
- for `003466d9...`, the changed threshold recomputed the timeline and changed the detected shot count from 9 to 8;
- returning to threshold `27` recomputed the default timeline, and the next run hit that cache.

Sixteen unit tests passed, covering Stage 1 compatibility, schemas, boundary normalization, cache keys, missing inputs, artifact storage, and keyframe hash validation.

## Stage Result

Stage 2 provides a stable visual timeline contract for later perception tools:

```text
AdAsset
  -> Timeline
  -> Shot
  -> Keyframe
```

The next stage may consume these artifacts for timestamped ASR and OCR evidence, but it must not bypass or redefine the Stage 2 timeline.
