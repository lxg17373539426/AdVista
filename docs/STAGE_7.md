# Stage 7: Deterministic Critic and Deliverable Reports

## Boundary

Stage 7 implements only:

- deterministic auditing of accepted Stage 6 insights;
- citation expansion from OCR Cluster IDs to representative source Evidence;
- Artifact path, SHA-256, Asset, and time-range checks;
- epistemic-status and single-modality risk findings;
- structured Critic audit output;
- Markdown diagnostic report;
- self-contained HTML diagnostic report with embedded evidence frames;
- deliverable hash validation and caching.

Stage 7 does not call Qwen or any other model. It does not rewrite claims, generate new insights, create ad scripts, implement Agent planning, memory, chat, competitor retrieval, or run a Web server.

If Stage 5 or Stage 6 artifacts are missing, Stage 7 fails instead of running upstream model stages.

## Processing Flow

```text
MarketingAnalysis + Evidence Ledger
  ↓
展开每个 Evidence / Cluster 引用
  ↓
校验源 Evidence、Artifact 和 SHA-256
  ↓
检查时间范围、认识论状态和风险主张证据模态
  ↓
生成 pass / review / fail Finding
  ↓
输出 Critic Audit
  ↓
渲染 Markdown 与自包含 HTML
  ↓
记录交付物 SHA-256 并支持缓存复用
```

## Critic Status

An insight is `fail` when it contains an objectively invalid provenance condition:

- missing or unknown evidence reference;
- missing Artifact;
- Artifact SHA-256 mismatch;
- Evidence outside the video duration;
- no resolvable Evidence.

An insight is `review` when it is structurally valid but requires human judgment:

- audience or creative inference has an unexpected epistemic status;
- health, efficacy, aging, wrinkle, damage, or risk wording is supported by one modality only.

An insight is `pass` when it contains no errors or warnings. Informational findings such as missing calibrated DeepSeek-OCR confidence do not downgrade a pass.

The Critic never deletes, edits, or silently weakens an insight.

## Report Configuration

```yaml
report:
  max_evidence_per_insight: 4
  embed_images: true
  max_embedded_image_bytes: 500000
```

The Markdown report links to source frame files. The HTML report embeds referenced images as Base64 data URIs and uses no CDN, JavaScript, external stylesheet, or network resource.

Only Evidence referenced by accepted insights is included. The report does not copy every video keyframe.

## Command

```bash
.venv/bin/advista-agent report /path/to/ad.mp4
```

Use `--force` to rebuild the audit and reports. Stage 5 and Stage 6 artifacts remain read-only.

## Artifacts

```text
outputs/runs/ingest_<asset-hash>/
├── critic/
│   └── audit.json
├── report/
│   ├── report.md
│   └── report.html
├── stage_7_metrics.json
└── manifest.json
```

`audit.json` contains per-insight evidence expansion and findings. `report.md` is review-friendly and links to local evidence frames. `report.html` is a portable, single-file deliverable.

## Security and Integrity

- All model-derived text is HTML escaped.
- HTML contains a mobile viewport and responsive CSS.
- HTML has no external URL or script dependency.
- Embedded images are restricted to known image suffixes and a configured maximum file size.
- Cache reads verify Markdown and HTML SHA-256 values.
- Source Evidence Artifacts are re-hashed during Critic execution.
- Report rendering never executes content from the model.

## Verification Results

Verified on 2026-08-10 using the retained Demo Run:

| Metric | Result |
|---|---:|
| Accepted Stage 6 insights | 10 |
| Audit status | review |
| Passed insights | 4 |
| Review insights | 6 |
| Failed insights | 0 |
| Embedded evidence images | 35 |
| Markdown size | 17,471 bytes |
| HTML size | 4,846,272 bytes |
| Build time | 0.058 s |
| Repeated execution | Cache hit |

The overall `review` status is expected. The Demo's health, efficacy, wrinkle, aging, and skin-damage claims primarily cite OCR and therefore require original-video or human review.

Forty-seven unit tests passed before final cleanup. Tests cover missing and corrupted evidence, risk warnings, Cluster expansion, HTML escaping, embedded images, viewport presence, external-resource absence, Markdown references, and all previous stages.

## Browser Verification Gap

Playwright navigation requires a compatible local Chromium installation. No browser is installed automatically by the project.

Instead, the stage verifies:

- valid HTML parser completion;
- required viewport metadata;
- escaped model text;
- embedded image data URIs;
- absence of external image URLs;
- UTF-8 file recognition;
- complete report hash validation.

A real Chrome/Chromium visual regression test remains a future environment task.

## Known Limitations

- The deterministic Critic cannot prove semantic entailment between a claim and its citations.
- The high-risk term list is domain-specific and incomplete.
- Multi-source support currently checks modality diversity, not source independence.
- The same frame can be embedded more than once when cited by multiple insights, increasing HTML size.
- HTML is a static report, not an interactive video timeline.
- Human acceptance and recommendation usefulness are not yet measured.

## Stage Result

Stage 7 completes the first inspectable product delivery path:

```text
Validated MarketingAnalysis
  -> Deterministic Critic
  -> Structured Audit
  -> Markdown / HTML Report
```

The next phase should pause before adding Agent autonomy. The narrow next step is an orchestration layer that can execute already-stable stages according to an explicit plan, with human confirmation before expensive OCR/Qwen calls. It should not introduce memory or unrestricted tool loops at the same time.
