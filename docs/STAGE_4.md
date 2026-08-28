# Stage 4: Timestamped OCR Evidence

## Boundary

Stage 4 implements only:

- OCR over Stage 2 primary keyframes;
- DeepSeek-OCR as the primary grounded OCR model;
- PaddleOCR as a fallback when DeepSeek returns no usable grounded text or fails;
- normalized text regions, keyframe timestamps, model provenance, and frame hashes;
- validated `OcrFrameResult` and standard `Evidence` artifacts;
- model-, prompt-, keyframe-, parameter-, and version-aware caching;
- per-backend latency and fallback metrics.

Stage 4 does not implement Qwen inference, cross-frame OCR deduplication, selling-point analysis, object/logo detection, music analysis, Agent planning, reports, or a Web UI.

## Processing Flow

```text
AdAsset + Timeline + Keyframes
  ↓
校验关键帧路径与 SHA-256
  ↓
DeepSeek-OCR Grounding 推理
  ↓
解析文本与 0-999 坐标
  ↓
归一化为 0-1 BoundingBox
  ↓
无有效 Grounding 的帧进入 PaddleOCR fallback
  ↓
绑定 Keyframe / Shot / 毫秒时间戳
  ↓
输出 OCR Frame Results + OCR Evidence
  ↓
相同视频、关键帧、模型、Prompt 和参数命中缓存
```

## DeepSeek-OCR Environment

DeepSeek-OCR uses a dedicated Conda environment and does not modify the existing `vllm`, `deepseek_ocr`, or `ocr` environments.

```bash
conda create -n advista-deepseek-ocr python=3.12.9 -y

conda run -n advista-deepseek-ocr python -m pip install \
  torch==2.6.0 torchvision==0.21.0 \
  --index-url https://download.pytorch.org/whl/cu124

conda run -n advista-deepseek-ocr python -m pip install \
  -r requirements/deepseek-ocr.txt

MAX_JOBS=4 conda run -n advista-deepseek-ocr python -m pip install \
  flash-attn==2.7.3 --no-build-isolation
```

Verified stack:

- Python 3.12.9;
- PyTorch 2.6.0 + CUDA 12.4;
- Transformers 4.46.3;
- Tokenizers 0.20.3;
- FlashAttention 2.7.3;
- one NVIDIA H20 with BF16 support.

The environment occupies approximately 6.0 GB.

## PaddleOCR Fallback

Fallback uses the locally configured PaddleOCR Python environment:

- PaddleOCR 3.7.0;
- PaddlePaddle 3.3.1;
- locally cached PP-OCRv6 medium detection and recognition models.

The installed Paddle package is CPU-only. The worker therefore disables oneDNN and runs explicitly on CPU. This avoids the observed PIR/oneDNN incompatibility but makes fallback the dominant latency cost.

## Configuration

```yaml
ocr:
  deepseek_python: .venv-ocr/bin/python
  paddle_python: .venv-ocr/bin/python
  model: DeepSeek-OCR
  primary: deepseek_ocr
  fallback: paddleocr
  prompt: "<image>\n<|grounding|>Convert the image to markdown."
  base_size: 1024
  image_size: 640
  crop_mode: true
  timeout_seconds: 1800
  paddle_score_threshold: 0.5
```

## Grounding Protocol

DeepSeek-OCR returns regions in this form:

```text
<|ref|>text<|/ref|><|det|>[[x1,y1,x2,y2]]<|/det|>
recognized text
```

Coordinates use the range 0 to 999 and are converted to normalized 0 to 1 bounding boxes. DeepSeek-OCR does not return a calibrated confidence, so its Evidence uses `confidence: null`. PaddleOCR Evidence retains the actual recognition score.

Low-quality text is retained with its backend and provenance. Stage 4 does not use heuristic language rules to silently discard potential advertising claims.

## Command

```bash
.venv/bin/advista-agent ocr /path/to/ad.mp4
```

Use `--force` to ignore an otherwise valid Stage 4 cache entry.

## Artifacts

```text
outputs/runs/ingest_<asset-hash>/
├── ocr/
│   ├── raw.jsonl
│   └── evidence.jsonl
├── stage_4_metrics.json
└── manifest.json
```

`raw.jsonl` preserves per-keyframe raw model output and parsed regions. `evidence.jsonl` contains one standard OCR Evidence per text region.

Each OCR Evidence includes:

- keyframe and shot IDs;
- exact keyframe timestamp;
- normalized bounding box;
- observed text and available confidence;
- backend and model identity;
- backend framework versions;
- keyframe path and verified SHA-256.

## Cache Identity

The Stage 4 cache key includes:

- source video SHA-256;
- every keyframe ID and SHA-256;
- pipeline and schema versions;
- DeepSeek model config hash, weight size, and weight mtime;
- primary and fallback model/framework versions;
- grounding prompt;
- image preprocessing parameters;
- Paddle recognition-score threshold.

Cache reads validate that every OCR Evidence timestamp, path, and hash still matches its referenced keyframe.

## Deployment Investigation

The existing vLLM 0.19.1 environment recognized `DeepseekOCRForCausalLM` and loaded the 6.23 GiB model, but its engine process exited when processing the first image prompt. Eager mode also failed at first-prompt processing. The stage therefore uses the official Transformers inference path rather than hiding this failure behind retries.

## Verification Results

Verified on 2026-08-10.

| Video | Keyframes | DeepSeek Frames | Paddle Frames | Evidence | DeepSeek | Paddle | Total |
|---|---:|---:|---:|---:|---:|---:|---:|
| `00045b16...` | 29 | 9 | 20 | 62 | 29.66 s | 95.79 s | 125.51 s |
| `00fa2e3a...` | 12 | 0 | 12 | 136 | 15.83 s | 57.07 s | 74.83 s |
| `005f54e1...` | 12 | 5 | 7 | 91 | 19.91 s | 46.41 s | 67.99 s |

The retained Demo Run returned `cache_hit: true` on the second execution. Twenty-eight unit tests passed before final cleanup.

These are engineering integration results, not OCR CER or detection-mAP results. The extra test videos are real dataset samples but have no manually labeled OCR ground truth.

## Known Limitations

- Only one midpoint keyframe is analyzed per shot, so transient text can be missed.
- Consecutive frames can contain repeated subtitles. Cross-frame temporal deduplication belongs to the next Evidence Ledger stage.
- DeepSeek sometimes emits image-level grounding without text; these frames use PaddleOCR.
- Paddle fallback is CPU-only and dominates end-to-end latency.
- DeepSeek confidence is unavailable and remains `null`.
- Small decorative text can be recognized incorrectly and is retained for later evidence-quality filtering.

## Stage Result

Stage 4 establishes the second timestamped semantic evidence stream:

```text
AdAsset + Timeline + Keyframes
  -> OCR Frame Results
  -> OCR Evidence
```

The next stage should build a unified Evidence Ledger that aligns and deduplicates Speech and OCR evidence. It must not begin Qwen marketing reasoning until evidence identity, temporal aggregation, and conflict rules are stable.
