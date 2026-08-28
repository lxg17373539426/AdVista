# Stage 6: Evidence-Grounded Advertising Insights

## Boundary

Stage 6 implements only:

- text-only Qwen3.5-9B reasoning over the completed Stage 5 Evidence Ledger;
- structured selling-point, pain-point, audience, creative-structure, conversion-path, brand-exposure, and risk insights;
- mandatory Speech Evidence ID or OCR Cluster ID citations;
- Pydantic and JSON Schema validation;
- deterministic confidence, citation, dimension-count, and unknown-statement guards;
- model-, prompt-, ledger-, schema-, and generation-aware caching;
- raw response, validated analysis, metrics, and manifest provenance.

Stage 6 does not re-read video or keyframe pixels. It does not run ASR or OCR, implement Agent planning, tool selection, memory, multi-turn chat, competitor retrieval, report UI, or script rewriting.

If Stage 5 artifacts are missing, Stage 6 fails explicitly instead of automatically running upstream model stages.

## Processing Flow

```text
Stage 5 Evidence Ledger
  ↓
读取 Speech Evidence + OCR Clusters + Candidate Relations
  ↓
压缩为允许引用的结构化文本上下文
  ↓
Qwen3.5-9B 结构化营销推理
  ↓
JSON Schema / Pydantic 校验
  ↓
引用 ID、维度数量、置信度和 unknowns 守卫
  ↓
由已验证 Claim 确定性生成 Executive Summary
  ↓
写入 Analysis、Raw Response、Metrics 和 Manifest
```

## Model Role

Qwen3.5-9B performs domain reasoning only:

- extracting advertised selling points;
- mapping product claims to consumer pain points;
- inferring an evidence-supported audience without unsupported demographics;
- identifying creative narrative structure;
- identifying CTA and conversion path;
- summarizing brand exposure;
- surfacing risky or absolute advertising claims.

It receives no video, image, audio, or external product knowledge in this stage.

## Deployment

Stage 6 uses the existing vLLM 0.19.1 runtime through a persistent local OpenAI-compatible service:

- vLLM 0.19.1;
- PyTorch 2.10.0;
- Transformers 5.13.0;
- one NVIDIA H20;
- BF16;
- maximum model length 8,192;
- thinking disabled;
- CUDA Graph execution;
- continuous batching with up to 16 sequences;
- chunked prefill and prefix caching.

The AdInsight-RL checkpoint is configured locally and served as `AdInsight-RL`, with architecture `Qwen3_5ForConditionalGeneration`. The original isolated subprocess runtime remains available through explicit configuration but is no longer the default.

## Input Compression

The model receives:

- non-duplicated Speech Evidence records;
- OCR Cluster records rather than every OCR frame duplicate;
- candidate Stage 5 relations;
- explicit allowed citation ID lists.

For the retained Demo, 62 OCR records were compressed to 46 OCR clusters before Qwen inference. The model input contained 4,196 tokens instead of raw video or repeated visual evidence.

## Output Schema

Each insight contains:

```json
{
  "insight_id": "SP_001",
  "dimension": "selling_point",
  "claim": "...",
  "evidence_refs": ["ocr_cluster_0031"],
  "confidence": 0.82,
  "epistemic_status": "stated_by_ad",
  "reasoning_summary": "..."
}
```

Allowed dimensions:

- `selling_point`;
- `pain_point`;
- `audience`;
- `creative_structure`;
- `conversion_path`;
- `brand_exposure`;
- `risk`.

## Grounding Guards

The analysis is rejected when:

- an insight has no citation;
- a citation is not an allowed Speech Evidence or OCR Cluster ID;
- an insight ID is duplicated;
- a dimension exceeds the configured maximum;
- confidence exceeds `0.85`;
- Claim or reasoning text writes citation IDs outside `evidence_refs`;
- an unknown statement does not begin with `当前 Ledger 无证据表明`;
- the output Asset ID differs from the Ledger Asset ID;
- output JSON or Pydantic Schema is invalid.

The prompt additionally forbids unsupported age, gender, income, occupation, geography, skin type, and similar demographic inference.

## Summary Safety

The model's free-form executive summary is preserved in `raw_response.json` for audit but is not trusted as the final summary. The final `executive_summary` is deterministically constructed from validated claims in this order:

1. creative structure;
2. pain point;
3. selling point;
4. conversion path.

This prevents unsupported terms from bypassing per-insight citations.

## Structured Output Recovery

vLLM JSON Schema decoding produced malformed JSON during the first integration attempt. Stage 6 therefore supports one same-model syntax-repair pass within the already-loaded process:

- only the invalid draft is supplied;
- the repair prompt permits syntax repair only;
- no Ledger context or new evidence is provided;
- the repaired result must still pass every grounding guard.

The final accepted run required one attempt and no repair. All attempts are stored in `raw_response.json`.

## Command

```bash
.venv/bin/advista-agent insights /path/to/ad.mp4
```

Use `--force` to ignore an otherwise valid Stage 6 cache entry. Upstream Ledger artifacts are still read-only.

## Artifacts

```text
outputs/runs/ingest_<asset-hash>/
├── insights/
│   ├── raw_response.json
│   └── analysis.json
├── stage_6_metrics.json
└── manifest.json
```

`raw_response.json` records model attempts and runtime versions. `analysis.json` contains only the accepted, validated analysis with the deterministic summary.

## Cache Identity

The Stage 6 cache key includes:

- Qwen config and model-index SHA-256;
- all checkpoint shard names, sizes, and mtimes;
- vLLM, PyTorch, and Transformers versions;
- Stage 5 Evidence, Cluster, Relation, and Ledger SHA-256 values;
- Prompt SHA-256;
- max model length, output tokens, temperature, and per-dimension limit;
- Stage 6 pipeline and schema versions.

Cache reads revalidate the complete MarketingAnalysis and all citations.

## Verification Results

Verified on 2026-08-10 using the retained Demo Run:

| Metric | Result |
|---|---:|
| Prompt tokens | 4,196 |
| Completion tokens | 2,116 |
| Model attempts | 1 |
| Accepted insights | 10 |
| Original cold-start inference latency | 100.18 s |
| Persistent-service inference latency | 15.43 s |
| Selling points | 2 |
| Pain points | 3 |
| Audience insights | 1 |
| Creative structure | 1 |
| Conversion path | 1 |
| Brand exposure | 1 |
| Risk insights | 1 |
| Unknowns | 5 |
| Repeated execution | Cache hit |

The accepted audience insight did not introduce gender, age, income, or other unsupported demographic attributes. All accepted insights cite valid OCR Cluster IDs and use confidence values no greater than `0.85`.

Forty-two unit tests passed before final cleanup.

## Rejected Iterations

Two intermediate outputs were correctly rejected during implementation:

1. The first output contained malformed JSON despite schema-constrained decoding.
2. A later output assigned `0.90` confidence to an inferred pain point, exceeding the configured grounding limit.

These failures were not written as accepted `analysis.json` artifacts. They motivated stricter Schema and citation controls rather than permissive local rewriting.

## Known Limitations

- Insight quality is validated structurally and by citation presence, not against a human-labeled marketing benchmark.
- A valid citation does not prove that the model interpreted the evidence correctly.
- The current Demo has weak ASR, so most insights rely on OCR clusters.
- Qwen still receives lexical evidence only; visual demonstrations, product use, emotion, music, and object detection are absent.
- The persistent service occupies most of the configured H20 while running, so GPU-backed ASR and OCR should remain serialized with Qwen work.
- Confidence values are model estimates capped by Schema, not calibrated probabilities.

## Stage Result

Stage 6 produces the first evidence-grounded marketing interpretation layer:

```text
Evidence Ledger
  -> Qwen3.5 Structured Reasoning
  -> Citation and Confidence Guards
  -> MarketingAnalysis
```

The next stage should not immediately add every Agent feature. A narrow next step is a Critic and Deliverable layer that validates claim coverage and creates a Markdown/HTML diagnostic report from the accepted analysis without changing source Evidence.
