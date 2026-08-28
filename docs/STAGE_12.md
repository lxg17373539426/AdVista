# Stage 12: Local Web Workspace

Stage 12 adds a dependency-free local Web workspace for the existing Agent artifacts. It is implemented with Python's standard-library HTTP server so the current environment can run it without installing FastAPI or Uvicorn.

## Start

```bash
.venv/bin/advista-agent serve
```

Open `http://127.0.0.1:8080`. The service binds to localhost by default and uses `configs/default.yaml` for the port, upload size limit, and worker count.

## Workflow

- Upload a supported video and choose a goal and deliverable.
- Poll the background job status while the bounded Agent executes.
- Inspect run availability and the normalized timeline.
- Continue an existing Agent conversation using the persisted SQLite memory.
- Download report HTML/Markdown, analysis JSON, and creative package JSON.

## Safety Boundary

- Uploads use server-generated filenames under `outputs/uploads/`.
- Supported video suffixes are checked before ingestion.
- Uploads are limited by `web.max_upload_bytes`.
- Run IDs use the strict `ingest_<20 lowercase hex>` form.
- Served artifacts are selected by fixed export enums and checked to remain inside the run directory.
- The default server is local-only. Authentication, authorization, TLS, quotas, and rate limits are required before non-local deployment.

## Current Scope

The first version provides upload, background progress, run overview, timeline, chat, feedback plumbing, and artifact export. It does not include multi-user authentication, remote deployment, visual video annotation, or a browser-based report editor.
