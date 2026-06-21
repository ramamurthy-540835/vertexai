# Lead Match Semantic Workflow Runtime Assessment

Date: 2026-06-21

Scope:
- GitHub Actions workflow: `.github/workflows/lead_match_semantic_workflow.yml`
- Cloud Workflows definition: `deploy/lead_match_workflow.yaml`
- Runtime entrypoint: `lead_match_runtime/job_runner.py`
- Schema/index script: `schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql`
- Local monitor script: `scripts/run_lead_match_workflow_monitor.sh`

This began as an assessment and was followed by implementation fixes in the workflow, runtime, monitor script, and schema files.

## Executive Summary

The original GitHub Actions run appeared stuck in the `Trigger lead match workflow` step because the workflow used `gcloud workflows run`. That command is synchronous: it starts a Google Cloud Workflow execution and waits for the execution to finish. The Actions log line `Waiting for execution [...] to complete...` was expected behavior for that command, not a separate GitHub Actions wait loop.

That made the `wait_for_completion` input ineffective. The workflow has now been changed to use `gcloud workflows execute`, so the trigger step starts the GCP execution asynchronously and the existing conditional wait step controls whether GitHub Actions waits.

Separately, the called Cloud Workflow can legitimately take hours because it runs four Cloud Run Jobs serially:
1. Lead embeddings
2. POS embeddings
3. Fuzzy match
4. Report

The POS embedding job is the most likely workload bottleneck when a warehouse has many POS rows without existing embeddings. The runtime has now been updated with retry/backoff, bounded field-level embedding concurrency, and batch timing logs.

## Findings

### P0: `gcloud workflows run` blocks the GitHub trigger step

Evidence:
- `.github/workflows/lead_match_semantic_workflow.yml` calls `gcloud workflows run` in the `Trigger lead match workflow` step.
- Google Cloud CLI documentation defines `gcloud workflows run` as the command that executes a workflow and waits for completion.
- The Actions log confirms this with `Waiting for execution [1d913344-3b14-4b3d-84d8-76da8361f830] to complete...`.

Impact:
- The step named `Trigger lead match workflow` is actually both trigger and wait.
- The separate `Wait for completion` step cannot run until the Cloud Workflow has already finished.
- The `wait_for_completion=false` input does not provide async behavior.
- GitHub runner minutes are consumed for the full GCP workload duration.

Recommended fix:
- Replace `gcloud workflows run` with `gcloud workflows execute` in GitHub Actions and local monitor scripts when the intent is async trigger.
- Keep the existing poll loop only when `wait_for_completion=true`.
- If synchronous behavior is desired, remove the redundant manual wait step and rename the trigger step honestly.

Reference:
- `gcloud workflows run`: https://docs.cloud.google.com/sdk/gcloud/reference/workflows/run
- `gcloud workflows execute`: https://docs.cloud.google.com/sdk/gcloud/reference/workflows/execute

### P0: No explicit timeout guard for the long-running GitHub job

Evidence:
- `.github/workflows/lead_match_semantic_workflow.yml` has no `timeout-minutes` at job or step level.
- The current run has already exceeded 3 hours while still inside one shell step.

Impact:
- A stuck Cloud Workflow or Cloud Run Job can hold the GitHub runner until platform default timeout.
- Failures are delayed and expensive.
- Developers cannot quickly distinguish "still processing" from "hung".

Recommended fix:
- Add a job-level timeout aligned to the expected SLA, for example 180 or 240 minutes during development.
- Add a shorter timeout around the trigger/poll path if async execution is used.
- Add a clear failure message that tells the developer which GCP execution to inspect.

### P1: Cloud Workflow runs independent embedding stages serially

Evidence:
- `deploy/lead_match_workflow.yaml` runs `runLeadEmbeddings`, waits, then runs `runPosEmbeddings`, waits, then runs `runFuzzyMatch`, waits, then runs `runReport`.
- Lead embedding generation and POS embedding generation do not depend on each other, except that both must complete before fuzzy matching.

Impact:
- End-to-end runtime is `lead embedding duration + POS embedding duration + match duration + report duration`.
- If lead and POS embedding jobs are independent, serial orchestration wastes wall-clock time.

Recommended fix:
- Run lead and POS embedding jobs in parallel in Cloud Workflows.
- Join both embedding branches before fuzzy match.
- Preserve serial ordering only for fuzzy match and report.

Expected result:
- End-to-end time becomes roughly `max(lead embedding duration, POS embedding duration) + match duration + report duration`.

### P1: Embedding runtime is sequential and can generate thousands of Vertex API calls

Evidence:
- `lead_match_runtime/job_runner.py` sets `DEFAULT_BATCH_SIZE` to 25.
- For each batch, lead embeddings call Vertex three times: combined field, full address, business name.
- POS embeddings use the same three-call pattern.
- A previous warehouse 115 report in the repo shows 630 lead rows and 18,900 POS rows. At batch size 25, that shape requires about 78 lead embedding calls and 2,268 POS embedding calls when embeddings are missing.

Impact:
- A warehouse with many new POS rows can spend hours in the embedding phase.
- The current implementation does not use the older component-level concurrency and retry/backoff patterns present under `lead_match_codebase/src/...`.
- Any transient 429/quota/network issue can fail the Cloud Run Job instead of slowing and recovering.

Recommended fix:
- Increase batch size only after checking Vertex model request limits and quota.
- Add retry/backoff for 429, `RESOURCE_EXHAUSTED`, and transient 5xx errors.
- Add controlled concurrency for embedding batches, with configurable worker count.
- Emit per-batch latency and rows/sec metrics to Cloud Logging.
- Consider embedding all three text fields through a normalized batching layer to reduce overhead while preserving field mapping.

### P1: Fuzzy match may be slow if HNSW vector indexes are absent

Evidence:
- `CLOUD_SQL_PGVECTOR_SCHEMA.md` documents required HNSW indexes on `leads_embeddings.combined_embedding` and `pos_embeddings.combined_embedding`.
- `schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql` creates B-tree indexes but does not create those HNSW indexes.
- `lead_match_runtime/job_runner.py` uses `CROSS JOIN LATERAL` with vector distance ordering against `pos_embeddings`.

Impact:
- If the live database was built from the checked-in schema script, fuzzy matching can degrade into repeated vector scans.
- For warehouse 115 scale, 630 leads x 18,900 POS rows is about 11.9 million candidate distance comparisons before thresholding.
- Larger warehouses can push this into multi-hour Cloud SQL workload.

Recommended fix:
- Verify live indexes with `pg_indexes`.
- Add HNSW vector indexes to the authoritative schema migration.
- Run `ANALYZE` after creating indexes and after large embedding loads.
- Capture `EXPLAIN (ANALYZE, BUFFERS)` for the fuzzy-match query in a controlled warehouse scope.

Minimum index candidates:

```sql
CREATE INDEX IF NOT EXISTS idx_leads_embeddings_combined_hnsw
ON leadmgmt.leads_embeddings
USING hnsw (combined_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_pos_embeddings_combined_hnsw
ON leadmgmt.pos_embeddings
USING hnsw (combined_embedding vector_cosine_ops);
```

### P1: Cloud Run Job wait logic can report success too early for multi-task jobs

Evidence:
- `deploy/lead_match_workflow.yaml` considers a Cloud Run Job execution done when `succeededCount + failedCount > 0`.
- It returns success when `succeededCount > 0`.

Impact:
- This is safe only for single-task jobs.
- If any Cloud Run Job is later configured with multiple tasks, the workflow can proceed after the first successful task while other tasks are still running or have failed.
- This can start fuzzy matching before all embeddings are complete.

Recommended fix:
- Wait for terminal execution condition rather than first completed task.
- Validate all tasks succeeded, not just one.
- Include task count, succeeded count, failed count, and condition details in errors.

### P2: Preflight READY marker can become stale

Evidence:
- The semantic workflow only checks for `gs://.../preflight/.../READY`.
- The READY marker is not tied to a specific workflow revision, runtime image digest, schema/index version, or Cloud Run Job configuration.

Impact:
- Developers can launch a semantic run against stale or mismatched infrastructure.
- Missing vector indexes, old job images, or wrong environment variables are not caught by the semantic workflow.

Recommended fix:
- Replace a bare READY marker with a JSON readiness manifest.
- Include commit SHA, Cloud Workflow revision, Cloud Run Job image digest, schema/index validation results, service account, region, and generated timestamp.
- Refuse to run if the manifest is older than a defined age or if the current code SHA does not match.

### P2: Summary points to preflight report paths, not final run report paths

Evidence:
- GitHub summary variables `REPORT_URI` and `RESULT_URI` are built under `preflight/lead_match/...`.
- Runtime report generation writes under `reports/lead_match/{project}/{warehouse}/{matchRunId}` by default.

Impact:
- A developer reading the GitHub summary may inspect the preflight report instead of the actual semantic run output.
- This makes debugging longer because the summary does not provide the final `summary.json`, `matches.csv`, or `report.md` path.

Recommended fix:
- Add final report URI computation to the GitHub workflow using `MATCH_RUN_ID`.
- Pass `REPORT_PREFIX` explicitly to the report job if custom paths are desired.
- Surface the final GCS report paths in the GitHub step summary.

### P2: Local monitor script repeats the same blocking trigger issue

Evidence:
- `scripts/run_lead_match_workflow_monitor.sh` also calls `gcloud workflows run`.
- It then enters a manual polling loop after the blocking call.

Impact:
- Local monitoring has the same trigger/wait confusion as GitHub Actions.
- The script cannot show progressive workflow state until the workflow has already completed.

Recommended fix:
- Change the local monitor trigger to `gcloud workflows execute`.
- Keep the polling loop for actual monitoring.

### P2: Missing operational telemetry for stage-level diagnosis

Evidence:
- The Cloud Workflow returns execution names only after all stages finish.
- GitHub Actions currently logs only the outer workflow execution.
- The runtime logs row counts and some batch progress, but not stage durations, rows/sec, API call latency, retry counts, or Cloud SQL query plans.

Impact:
- During a 3-hour run, the operator cannot easily tell whether the active bottleneck is lead embeddings, POS embeddings, fuzzy SQL, report generation, quota backoff, or Cloud SQL contention.

Recommended fix:
- Add start/end timestamps around every Cloud Workflow stage.
- Emit Cloud Run execution names immediately after each job starts.
- Emit structured logs from the runtime with `stage`, `warehouse`, `matchRunId`, `rows_total`, `rows_done`, `batch`, `duration_ms`, and `rows_per_second`.
- Add a GitHub summary section that includes per-stage duration after completion.

## Most Likely Explanation For The Current 3+ Hour Run

The Actions step is not stuck in GitHub checkout/auth/setup. It is waiting inside `gcloud workflows run`.

The actual long work is happening in GCP, probably in one of these places:
1. POS embedding generation for warehouse 569 if many POS rows lack embeddings.
2. Fuzzy match SQL if HNSW vector indexes are missing or not used.
3. A Cloud Run Job execution retry/backoff or quota issue that is not visible in the GitHub log.

Because the trigger command is synchronous, the GitHub log will stay on the trigger step until the GCP Workflow finishes or fails.

## Immediate Triage Commands

Use these from an authenticated environment with access to project `ctoteam`.

Inspect the Cloud Workflow execution:

```bash
gcloud workflows executions describe 1d913344-3b14-4b3d-84d8-76da8361f830 \
  --project=ctoteam \
  --location=us-central1 \
  --format='yaml(name,state,startTime,endTime,duration,argument,result,error)'
```

Find recent Cloud Run Job executions for each stage:

```bash
gcloud run jobs executions list \
  --project=ctoteam \
  --region=us-central1 \
  --job=lead-match-lead-embeddings \
  --limit=5

gcloud run jobs executions list \
  --project=ctoteam \
  --region=us-central1 \
  --job=lead-match-pos-embeddings \
  --limit=5

gcloud run jobs executions list \
  --project=ctoteam \
  --region=us-central1 \
  --job=lead-match-fuzzy-match \
  --limit=5

gcloud run jobs executions list \
  --project=ctoteam \
  --region=us-central1 \
  --job=lead-match-report \
  --limit=5
```

Check whether warehouse 569 still needs embeddings:

```sql
SELECT COUNT(*) AS lead_rows
FROM leadmgmt.lead
WHERE warehouse_number = 569;

SELECT COUNT(*) AS lead_embeddings
FROM leadmgmt.leads_embeddings
WHERE warehouse_number = 569;

SELECT COUNT(*) AS pos_rows
FROM leadmgmt.transaction
WHERE warehouse_number = 569;

SELECT COUNT(*) AS pos_embeddings
FROM leadmgmt.pos_embeddings
WHERE warehouse_number = 569;
```

Verify vector indexes exist:

```sql
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'leadmgmt'
  AND tablename IN ('leads_embeddings', 'pos_embeddings')
ORDER BY tablename, indexname;
```

## Fix Backlog

Priority 1:
- Change GitHub and local monitor trigger from `gcloud workflows run` to `gcloud workflows execute`.
- Add explicit GitHub `timeout-minutes`.
- Add final report URI to GitHub summary.

Priority 2:
- Run lead and POS embedding jobs in parallel inside Cloud Workflows.
- Fix Cloud Run Job wait logic to require terminal execution success.
- Add stage-level logging and durations.

Priority 3:
- Add HNSW vector indexes to the authoritative schema migration.
- Validate query plans for fuzzy matching.
- Add runtime retry/backoff and configurable concurrency for Vertex embedding calls.

Priority 4:
- Replace stale READY marker with a readiness manifest.
- Add uniqueness constraints or idempotent upsert strategy for embedding tables.
- Add operational dashboards for Cloud Run Job duration, Vertex API errors, Cloud SQL CPU, and query latency.

## Files To Change Later

- `.github/workflows/lead_match_semantic_workflow.yml`
- `scripts/run_lead_match_workflow_monitor.sh`
- `deploy/lead_match_workflow.yaml`
- `lead_match_runtime/job_runner.py`
- `schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql`
- `.github/workflows/lead_match_preflight_ops.yml`
