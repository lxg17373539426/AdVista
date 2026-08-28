# Qwen Inference Optimization

## Comparison Contract

The historical reference is the frozen MAC V3 answer stage:

- 3,108 questions and 3,108 distinct videos;
- one NVIDIA GPU;
- Qwen3.5-9B BF16 through a persistent vLLM service;
- eight client workers and eight server sequences;
- 3,131 successful HTTP requests including format repairs;
- 6,468.47 seconds, or 0.4805 items/second;
- historical official score 0.8086.

The 0.8086 score belongs to the frozen answer artifact. The hidden 3,108 labels and official evaluator are not available locally, so it cannot be recomputed. Optimization quality gates therefore retain the exact Prompt and generation contract and compare format validity, repair rate, point-count agreement, normalized agreement, and token-level F1 against the frozen V3 answers.

## Benchmark Tool

`scripts/mac3108_benchmark.py` reads the original videos and frozen ASR, visual, and music evidence without changing the original AdVista or MAC-Agent projects. It records:

- wall-clock throughput;
- per-item, client-queue, and service latency;
- prompt and completion tokens;
- HTTP transport attempts and model format repairs;
- format pass rate;
- agreement proxies against the frozen 0.8086 answer artifact.

Each completed item is atomically checkpointed. A full interrupted run can be continued with the same experiment name and `--resume`.

```bash
.venv/bin/python scripts/mac3108_benchmark.py \
  --experiment full3108_bf16_c16_graph_prefix1 \
  --full --concurrency 16
```

## Service Configuration

The retained BF16 candidate uses:

- vLLM 0.19.1;
- one NVIDIA H20;
- BF16 weights;
- 32,768 maximum model length;
- 16 maximum sequences;
- CUDA Graph enabled;
- continuous batching and asynchronous scheduling;
- chunked prefill with 8,192 batched tokens;
- prefix caching enabled;
- 32 video frames, unchanged from the historical baseline;
- 0.90 GPU memory utilization.

Start it with:

```bash
QWEN_EXPERIMENT=bf16_c16_graph_prefix1 \
QWEN_MAX_NUM_SEQS=16 \
QWEN_ENABLE_PREFIX_CACHING=1 \
bash scripts/start_qwen_benchmark_server.sh
```

## Sample Screening

A deterministic 128-item sample spanning video-duration bins was used before the full run.

| Candidate | Items/s | Format | Token F1 proxy | Decision |
|---|---:|---:|---:|---|
| BF16, 8 seq, graph, no prefix | 0.4683 | 100% | not initially recorded | current-environment baseline |
| BF16, 8 seq, graph, prefix | 0.4844 | 100% | 0.7311 | retained component |
| BF16, 12 seq, graph, prefix | 0.5062 | 100% | 0.7249 | viable |
| BF16, 16 seq, graph, prefix | 0.5182 | 100% | 0.7348 | retained candidate |
| BF16, 20 seq, graph, prefix | 0.5197 | 100% | 0.7367 | rejected: 0.29% gain with worse request latency |
| BF16, 12 seq, eager, prefix | 0.4717 | 100% | not initially recorded | rejected |
| BF16, 8 seq, 16K prefill batch | 0.4823 | 100% | not initially recorded | rejected |
| BF16, 8 seq, 24 video frames | 0.5404 | 100% | 0.6551 | rejected: quality proxy loss |
| FP8, 8 seq, 32 frames | 0.6878 | 100% | 0.5608 | rejected: quality proxy loss |

The 16-sequence BF16 candidate improved sample throughput by 10.64% over the current-environment BF16 baseline while preserving the 32-frame input and quality proxies. Prefix-cache hit rate was only about 5-6%, as expected because every video is different.

## Agent Integration

Stage 6 now supports explicit runtimes:

- `openai`: use a persistent OpenAI-compatible vLLM service;
- `subprocess`: retain the original isolated cold-start worker.

The default configuration uses `openai`. A service failure is reported explicitly and never silently starts a second model process. Runtime type, endpoint, and served model are included in the Stage 6 cache identity.

## Full-Run Status

The complete 3,108-item retained-candidate run writes to:

```text
outputs/benchmarks/mac3108/full3108_bf16_c16_graph_prefix1/
```

The retained BF16 candidate completed the full task:

| Metric | Historical V3 | Optimized BF16 |
|---|---:|---:|
| Items | 3,108 | 3,108 |
| Wall time | 6,468.47 s | 5,903.32 s |
| Minutes | 107.81 | 98.39 |
| Items/second | 0.4805 | 0.5265 |
| Successful requests | 3,131 | 3,137 |
| Failed items | 0 | 0 |
| Format pass rate | 100% | 100% |
| Historical official score | 0.8086 | not locally recomputable |
| Frozen-answer token F1 proxy | not recorded | 0.7364 |
| Frozen-answer point-count agreement | not recorded | 92.79% |

The optimized run is 1.0957x faster than the historical wall time, a 9.57% throughput improvement and a 565.15-second reduction. During the measured GPU window, mean GPU utilization was 96.71%, mean allocated GPU memory was 91,490 MiB, and mean power was 331.69 W.

Five video-loading warnings were observed under 16-way video concurrency. They were successful HTTP requests but loaded fewer than the expected 32 frames. A low-concurrency recheck of the lowest-token candidates completed successfully; vLLM usage metadata does not expose frame count, so warning-to-request attribution is not available. This limitation affects the legacy direct-video benchmark. The new Agent Stage 6 consumes text Evidence Ledgers and therefore does not perform concurrent video decoding in Qwen.

## Agent Latency

The retained Demo Stage 6 was rerun through the persistent service with the same 4,196 prompt tokens and one accepted structured attempt:

| Runtime | Inference latency | Completion tokens |
|---|---:|---:|
| Isolated subprocess cold start | 100.18 s | 2,116 |
| Persistent vLLM service | 15.43 s | 2,180 |

This is a 6.49x wall-clock latency improvement for the Agent insight stage. The generated 10-insight analysis passed all existing Schema, citation, confidence, and grounding guards; the downstream Critic remained `review` with 4 pass, 6 review, and 0 fail insights.
