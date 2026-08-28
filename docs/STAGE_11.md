# Stage 11: Creative Deliverables

Stage 11 adds a bounded creative tool. It uses the existing Evidence Ledger and validated marketing analysis to generate structured creative material without re-running video parsing, ASR, or OCR.

## Deliverables

- Hooks with an angle and Evidence references.
- A short-form script with ordered scenes, narration, visual direction, on-screen text, and CTA.
- Ordered storyboard frames with shot and camera direction.
- At least two A/B variants with a hypothesis, change, and success metric.
- Explicit `unsupported_points` for facts the existing evidence cannot establish.

The complete package is stored under `outputs/runs/<run-id>/creative/` as `package.json`, `hooks.json`, `script.json`, `storyboard.json`, and `ab_plan.json`.

## Usage

```bash
.venv/bin/advista-agent creative /path/to/ad.mp4
```

For Agent planning:

```bash
.venv/bin/advista-agent agent-plan --goal "生成广告 Hook、脚本、分镜和 A/B 方案" --deliverable creative
```

The resulting plan is `ingest -> timeline -> speech + ocr -> ledger -> insights -> creative`; existing stages can return cache hits.

## Validation Boundary

- Every creative item must cite an existing speech Evidence ID or OCR Cluster ID.
- Unknown IDs are rejected locally before the artifact is persisted.
- Script and storyboard order values must be unique.
- At least two A/B variants are required.
- Creative direction is a proposal, not proof that an unobserved scene exists.
- Truncated text fragments are rejected.
- Qwen chooses content, but local Pydantic and grounding checks decide whether it can be delivered.

## Demo

The retained Demo Run generated two Hooks, nine script scenes, eleven storyboard frames, and two A/B variants. The first generation took 26.51 seconds; the second invocation returned a Stage 11 cache hit. Stage 3 and Stage 4 artifact mtimes remained unchanged.

## Remaining Scope

Stage 11 generates text and structured plans only. It does not render video, synthesize voice, generate images, or run an experiment.
