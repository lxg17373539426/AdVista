# Stage 8: Persistent Pipeline Orchestration

## Boundary

Stage 8 turns the existing Stage 1-7 builders into one recoverable product workflow. It implements:

- one command for deterministic Stage 1-7 execution;
- atomic per-run orchestration state;
- explicit `pending`, `running`, `completed`, `failed`, and `waiting_confirmation` states;
- restart from the failed stage while retaining valid upstream work;
- controlled stage revalidation and cache bypass;
- a human approval gate for Critic `review` results;
- a final summary containing state, report paths, errors, attempts, cache hits, and durations.

It does not add dynamic Qwen planning, arbitrary tool loops, memory, multi-turn chat, a Web UI, or a persistent Qwen service. Existing stage builders and their cache contracts remain authoritative.

## Flow

```text
run VIDEO
  -> ingest
  -> timeline
  -> speech
  -> OCR
  -> Evidence Ledger
  -> Qwen insights
  -> Critic and report
       -> pass: completed
       -> review: waiting_confirmation
       -> fail: failed
```

Only one stage is marked `running` at a time. This intentionally keeps GPU-backed ASR, OCR, and Qwen work serial on the configured single H20.

## Commands

Run the complete workflow:

```bash
.venv/bin/advista-agent run /path/to/ad.mp4
```

Resume a failed run from its failed stage:

```bash
.venv/bin/advista-agent resume ingest_<asset-hash>
```

Approve a report paused for review:

```bash
.venv/bin/advista-agent resume ingest_<asset-hash> --approve-review
```

Revalidate one stage and its downstream dependents while allowing valid content caches:

```bash
.venv/bin/advista-agent run /path/to/ad.mp4 --from-stage ledger
```

Bypass a selected stage cache and revalidate its dependents:

```bash
.venv/bin/advista-agent resume ingest_<asset-hash> --force-stage insights
```

`--force-stage` may be repeated. Downstream stages are reset and then use their own content-aware cache rules; only explicitly forced stages bypass their cache.

## Persisted State

State is atomically written to:

```text
outputs/runs/<run-id>/orchestration/state.json
```

Each stage records:

- status and attempt count;
- cache-hit result;
- start and completion timestamps;
- elapsed time;
- bounded failure text.

The top-level state records the source and config paths, pipeline version, current stage, Critic status, approval state, report paths, and terminal error.

## Human Gate

Critic behavior is not weakened by orchestration:

- `pass` completes automatically;
- `review` writes the report but pauses in `waiting_confirmation`;
- `fail` terminates the pipeline and points to `critic/audit.json`.

`--approve-review` is accepted only for a waiting `review` run. It records approval and changes the pipeline delivery state to `completed`; it does not modify `critic/audit.json`, regenerate insights, or rerun a model.

## Recovery

When a stage raises an error, the state is written before the error reaches the CLI. Completed upstream stages remain completed. `resume` resets the failed stage and all downstream stages to pending, then executes from that point. Stage-level cache validation still protects against stale or corrupted artifacts.

## Verification

The Stage 8 test suite covers:

- review pause and approval without model reruns;
- failure persistence and restart from the failed stage;
- forced-stage dependent reset;
- rejection of unknown stage names.

The complete project suite contains 52 passing tests. CLI help, Python compilation, and `doctor` were also verified on the configured one-H20 environment.

## Next Optimization Boundary

The next focused stage should benchmark and optimize inference without mixing those changes into orchestration. The first candidate is a persistent Qwen vLLM service, followed by TTFT and tokens/s measurement, prefix caching, CUDA Graph evaluation, continuous batching, and BF16/FP8/AWQ quality-performance comparisons.
