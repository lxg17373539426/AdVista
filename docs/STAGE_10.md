# Stage 10: Multi-Turn Conversation and Memory

Stage 10 adds evidence-grounded follow-up conversations on top of an existing Agent run. It reads the persisted Evidence Ledger and validated insight artifacts; it does not decode the video or rerun ASR and OCR.

## Implemented

- SQLite persistence for sessions, ordered messages, feedback, and answer versions.
- Multi-turn history supplied to the persistent Qwen3.5-9B service.
- Citation validation against existing speech Evidence IDs and OCR Cluster IDs.
- Explicit `grounded` and `unknown` epistemic states.
- Separate `unsupported_points` for claims that current evidence cannot establish.
- Deterministic normalization of unknown answers to the required `当前证据不足` prefix without adding facts or citations.
- CLI commands for follow-up questions, session inspection, and feedback.

```bash
.venv/bin/advista-agent chat ingest_<asset-hash> "这个广告最核心的卖点是什么？"
.venv/bin/advista-agent session-show ingest_<asset-hash>
.venv/bin/advista-agent feedback ingest_<asset-hash> approve "回答边界清晰" --message-id msg_<id>
```

## Safety Boundary

- Chat requires an existing Agent session, Ledger, OCR clusters, and validated analysis.
- Unknown Evidence IDs are rejected locally.
- Grounded answers require at least one valid citation.
- Unknown answers must contain no citations.
- Conversation writes only to `outputs/memory/conversations.sqlite3`; source analysis artifacts remain read-only.
- Qwen cannot invoke tools, Shell, ASR, OCR, or video parsing from the chat path.

## Verified Demo

The retained run `ingest_eabdf3d87eb58a38feab` completed three persisted follow-up turns, including a grounded selling-point answer and an explicit unknown-information answer. Stage 3 and Stage 4 metric mtimes remained unchanged at `1786272094` and `1786332982`, confirming that chat did not rerun ASR or OCR. User approval feedback was persisted for the third answer.

## Remaining Scope

- Time-range-targeted evidence retrieval and partial reanalysis are not implemented.
- Automatic evidence acquisition and multi-turn replanning are not implemented.
- Long-term user preference memory and Web chat UI remain future work.
