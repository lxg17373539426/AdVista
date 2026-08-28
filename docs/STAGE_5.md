# Stage 5: Unified Evidence Ledger

## Boundary

Stage 5 implements only deterministic evidence management:

- strict validation of existing Stage 3 Speech and Stage 4 OCR artifacts;
- a single time-ordered evidence stream;
- conservative temporal OCR clustering and duplicate accounting;
- candidate cross-modal support relations;
- candidate numeric conflict relations;
- source-, rule-, and version-aware caching;
- ledger indexes, metrics, and manifest provenance.

Stage 5 does not invoke ASR, OCR, Qwen, or any other model. It does not generate selling points, audiences, pain points, creative strategy, reports, Agent plans, or user-facing recommendations.

If Stage 3 or Stage 4 evidence is missing, Stage 5 fails explicitly instead of running those model stages automatically.

## Processing Flow

```text
Stage 3 Speech Evidence + Stage 4 OCR Evidence
  ↓
校验 Asset、时间范围、Artifact 路径与 SHA-256
  ↓
按毫秒时间统一排序
  ↓
OCR 文本规范化
  ↓
文本相似度 + 时间间隔 + 区域 IoU 聚合
  ↓
语音/OCR 时间窗口关联
  ↓
相似文本标记 supports 候选关系
  ↓
相似语境不同数字标记 possible_conflict
  ↓
写入 Evidence、Clusters、Relations、Ledger 和 Metrics
```

## Configuration

```yaml
ledger:
  ocr_similarity_threshold: 0.92
  ocr_max_gap_ms: 2500
  ocr_min_region_iou: 0.5
  cross_modal_similarity_threshold: 0.8
  cross_modal_window_ms: 1500
  conflict_context_similarity_threshold: 0.8
```

These thresholds are deterministic engineering defaults, not learned parameters.

## OCR Clustering Rules

Two OCR records can belong to the same temporal cluster only when all conditions hold:

- normalized text similarity is at least `0.92`;
- the current record is no more than `2,500 ms` after the previous cluster member;
- normalized bounding-box IoU is at least `0.5`.

Text normalization uses Unicode NFKC, case folding, punctuation removal, and whitespace normalization. The source Evidence is never deleted or rewritten. Clusters only provide a canonical view and member index.

The representative Evidence is selected deterministically:

1. prefer an Evidence record with confidence;
2. prefer higher confidence;
3. prefer more complete normalized text;
4. prefer the earlier timestamp as the final tie-breaker.

## Cross-Modal Relations

Stage 5 emits candidate relations, not semantic verdicts.

`supports` requires:

- one Speech and one OCR Evidence record;
- temporal distance within `1,500 ms`;
- normalized text similarity at least `0.8`.

`possible_conflict` requires:

- nearby Speech and OCR Evidence;
- both contain numeric values;
- the numeric sets differ;
- text with numbers replaced by placeholders remains at least `0.8` similar.

Numeric conflict detection is evaluated before general text support so that `30 percent off` and `20 percent off` cannot be incorrectly classified as support.

No relation means only that the deterministic conditions were not met. It does not mean the modalities contradict each other or that either source is wrong.

## Command

```bash
.venv/bin/advista-agent ledger /path/to/ad.mp4
```

Use `--force` to rebuild Stage 5 artifacts while still reusing existing Stage 3 and Stage 4 outputs.

## Artifacts

```text
outputs/runs/ingest_<asset-hash>/
├── ledger/
│   ├── evidence.jsonl
│   ├── clusters.jsonl
│   ├── relations.jsonl
│   └── ledger.json
├── stage_5_metrics.json
└── manifest.json
```

Artifact responsibilities:

- `evidence.jsonl`: immutable source Evidence copied into one deterministic time order;
- `clusters.jsonl`: OCR temporal clusters and representative/member IDs;
- `relations.jsonl`: candidate support and numeric-conflict links;
- `ledger.json`: compact indexes, modality counts, duration, and active rules;
- `stage_5_metrics.json`: source counts, duplicate count, relation counts, cache identity, and latency.

## Provenance Validation

Every source Evidence record must satisfy:

- unique Evidence ID;
- matching Asset ID;
- time range inside the video duration;
- existing Artifact path under the run directory;
- Artifact SHA-256 matching the recorded hash.

For Speech Evidence, the Artifact is `speech/transcript.json`. For OCR Evidence, the Artifact is the exact keyframe image.

Cache reads revalidate source provenance and all cluster/relation references. A modified source artifact, rule threshold, pipeline version, or schema version changes the Stage 5 cache identity or causes validation failure.

## Verification Results

Verified on 2026-08-10 using the retained Demo Run:

| Metric | Result |
|---|---:|
| Speech Evidence | 1 |
| OCR Evidence | 62 |
| Unified source Evidence | 63 |
| OCR temporal clusters | 46 |
| Folded adjacent OCR duplicates | 16 |
| Support relations | 0 |
| Possible numeric conflicts | 0 |
| Forced rebuild time | 0.026 s |
| GPU processes | 0 |

Examples of correctly clustered repeated subtitles include:

- `They said it would`: 2 source Evidence records;
- `make you look younger`: 2 records;
- `Replace the heavy formulas`: 3 records;
- `with the Real-Life Filter technology`: 3 records;
- `Thousands are making the`: 3 records.

The Demo Speech Evidence contains only the low-confidence phrase `Like a seed`, so no cross-modal relation met the conservative thresholds. The empty relation file is therefore an expected result.

Thirty-four unit tests passed before final cleanup. Tests cover OCR clustering, temporal separation, cross-modal support, numeric-conflict priority, missing-upstream failure, schemas, caches, provenance, and all previous stages.

## Known Limitations

- SequenceMatcher is lexical and does not detect paraphrases.
- OCR grouping compares nearby text and spatial overlap; moving animated captions may not cluster.
- Multi-line subtitles remain separate regions unless each region repeats across frames.
- Relations use source Evidence rather than cluster-level semantic reasoning.
- Numeric conflict rules do not understand units, currencies, or promotion conditions beyond lexical context.
- No relation quality can be claimed without a manually labeled relation dataset.

These limitations are intentional. Semantic fusion belongs to the later Qwen reasoning stage and must preserve these deterministic source references.

## Stage Result

Stage 5 creates the stable evidence substrate required by later reasoning:

```text
Speech Evidence + OCR Evidence
  -> Ordered Evidence
  -> OCR Clusters
  -> Candidate Relations
  -> Evidence Ledger
```

The next stage may use Qwen3.5-9B to produce evidence-grounded advertising insights, but every generated conclusion must cite Ledger Evidence or Cluster IDs and must not alter source evidence.
