# Lead Match Workflow — Deep Structural Audit

**Date:** 2026-06-22  
**Scope:** Bugs, failure paths, and performance issues in the Cloud Workflow, GitHub Actions workflow, and runtime code

---

## P0 — Critical (Will Stop the Workflow)

### 1. Cloud Workflow: No timeout on polling loop

**File:** `deploy/lead_match_workflow.yaml:162-188`

The `waitForRunJobExecution` polling loop has no timeout. If a Cloud Run job hangs (container stuck, OOM without exit), the workflow polls every 15 seconds forever — until Cloud Workflows' own 24-hour limit kills it.

**What broke:** This is exactly what happened on the 529 run — the polling logic couldn't detect completion, so it looped for 12+ hours until GitHub's 240-min timeout killed the GH Actions job, but the Cloud Workflow execution itself kept running.

**Fix:** Add a max-wait check at the top of the loop:

```yaml
waitForRunJobExecution:
  params: [executionName]
  steps:
    - initWait:
        assign:
          - pollStartedAt: ${sys.now()}
          - maxWaitSeconds: 3600
    - poll:
        call: googleapis.run.v2.projects.locations.jobs.executions.get
        args:
          name: ${executionName}
        result: execution
    - checkTimeout:
        switch:
          - condition: ${sys.now() - pollStartedAt > maxWaitSeconds}
            next: fail
    - checkCompletion:
        switch: [...]
```

---

### 2. Cloud Workflow: `checkCompletion` — first condition checks non-existent top-level fields

**File:** `deploy/lead_match_workflow.yaml:172`

```yaml
- condition: ${execution.succeededCount != null AND ...}
```

The Cloud Run v2 `executions.get` API returns `succeededCount` under `execution.status.succeededCount`, not at the top level. The first condition (`execution.succeededCount != null`) will always be false.

**Impact:** Not a blocker because the second condition (line 174) checks the correct nested path and works. But the first condition is dead code that adds confusion. The same applies to lines 176, 180 (top-level `failedCount`, `cancelledCount`).

**Fix:** Remove the dead top-level conditions. Keep only the `execution.status.*` / `execution.spec.*` checks:

```yaml
- checkCompletion:
    switch:
      - condition: ${execution.status != null AND execution.status.succeededCount != null AND execution.spec != null AND execution.spec.taskCount != null AND execution.status.succeededCount >= execution.spec.taskCount}
        next: returnSuccess
      - condition: ${execution.status != null AND execution.status.failedCount != null AND execution.status.failedCount > 0}
        next: fail
      - condition: ${execution.status != null AND execution.status.cancelledCount != null AND execution.status.cancelledCount > 0}
        next: fail
```

---

### 3. Cloud Workflow: `returnResult` references fields that don't exist in `jobResult`

**File:** `deploy/lead_match_workflow.yaml:155-160`

```yaml
- returnResult:
    return:
      taskCount: ${default(map.get(jobResult, "taskCount"), 1)}
      succeededCount: ${default(map.get(jobResult, "succeededCount"), 0)}
```

But `jobResult` is the return value of `waitForRunJobExecution`, which now returns only `{executionName, execution}`. So `map.get(jobResult, "taskCount")` is always null, and `default()` always returns the fallback (1, 0, 0, 0).

**Impact:** The `done` step returns artificial counts. Not a functional blocker but misleading for monitoring.

**Fix:** Read from the nested execution object:

```yaml
taskCount: ${default(map.get(default(map.get(jobResult, "execution"), map.new()), "taskCount"), default(map.get(default(map.get(default(map.get(jobResult, "execution"), map.new()), "spec"), map.new()), "taskCount"), 1))}
```

Or simpler: have `waitForRunJobExecution` return the counts directly.

---

### 4. Cloud Workflow: Non-existent Cloud Run job fails with cryptic error

**File:** `deploy/lead_match_workflow.yaml:129-136`

If `lead-match-ensure-indexes` (or any job) doesn't exist in Cloud Run, the `googleapis.run.v2.projects.locations.jobs.run` call returns a 404. The workflow crashes with an API error, no try/catch.

**What broke:** This blocked the very first 569 run — the `ensure-indexes` job didn't exist yet.

**Fix:** Add error handling or make `runEnsureIndexes` optional with a try/except.

---

### 5. GitHub Actions: `gcloud workflows executions describe` fails under `set -e`

**File:** `.github/workflows/lead_match_semantic_workflow.yml:328-332`

```bash
set -euo pipefail
while true; do
  STATE=$(gcloud workflows executions describe ... --format="value(state)")
```

If the gcloud API call fails transiently (network blip, rate limit), `set -e` kills the entire step immediately. The 569 run could fail from a single API hiccup during the 15-second polling loop.

**Fix:**

```bash
STATE=$(gcloud workflows executions describe ... --format="value(state)" 2>/dev/null) || {
  echo "Transient error polling workflow state, retrying..."
  sleep 5
  continue
}
```

---

### 6. GitHub Actions: `deploy_workflow` permission denied — no fallback

**File:** `.github/workflows/lead_match_semantic_workflow.yml:150-158`

The service account lacks `workflows.workflows.update`. The step fails, killing the entire run. There's no fallback to use the already-deployed workflow.

**What broke:** Both 569 runs failed because this step is mandatory when `deploy_workflow=true`.

**Fix:** Either grant the permission, or make the step non-fatal:

```yaml
- name: Deploy Cloud Workflow definition
  if: github.event.inputs.deploy_workflow == 'true'
  continue-on-error: true
  run: |
    gcloud workflows deploy ...
```

---

## P1 — High (Can Cause Silent Data Issues)

### 7. Embedding API response count not validated

**File:** `lead_match_runtime/job_runner.py:277-295`

`embed_text_request()` returns `[vector_literal(e.values) for e in response.embeddings]` without checking that `len(response.embeddings) == len(texts)`. If the API returns fewer embeddings (partial failure), `zip()` in `embed_texts()` silently drops the missing ones, leaving `None` in the results array. Those leads/POS get NULL embeddings and are silently excluded from matching.

**Fix:** Add validation after the API call:

```python
if len(response.embeddings) != len(texts):
    raise RuntimeError(
        f"API returned {len(response.embeddings)} embeddings for {len(texts)} texts ({label})"
    )
```

---

### 8. Database connections not closed on exception

**File:** `lead_match_runtime/job_runner.py:574-618, 621-665, 668-870`

`generate_lead_embeddings()`, `generate_pos_embeddings()`, and `run_fuzzy_match()` call `conn = connect()` but only close on the happy path. If any exception occurs, the connection leaks. In Cloud Run, leaked connections accumulate on retries.

**Fix:** Wrap each function body in `try/finally`:

```python
def generate_lead_embeddings():
    conn = connect()
    try:
        # ... all logic ...
    finally:
        conn.close()
```

---

### 9. No ON CONFLICT clause in embedding inserts

**File:** `lead_match_runtime/job_runner.py:591-603, 638-650`

The `INSERT INTO leads_embeddings` and `INSERT INTO pos_embeddings` statements have no `ON CONFLICT` clause. If the unique index exists and a re-run tries to insert an already-embedded lead, the insert fails with a constraint violation error instead of updating.

**Fix:** Add `ON CONFLICT (lead_id) DO NOTHING` (or `DO UPDATE` to refresh embeddings).

---

### 10. GitHub Actions: Cloud Run job create uses `--set-env-vars` vs `--update-env-vars`

**File:** `.github/workflows/lead_match_semantic_workflow.yml:145-159`

When a job doesn't exist, the `create` path uses `--set-env-vars`. When it exists, the `update` path uses `--update-env-vars`. The `create` path also needs `--max-retries=0` and `--task-timeout=1800`, but the created job won't get Cloud SQL connector secrets that other jobs have (like `DB_NAME`, `DB_PASSWORD`, `CLOUDSQL_CONNECTION_NAME`).

**Impact:** The newly created `lead-match-ensure-indexes` job will fail because it doesn't have database connection secrets. These are typically set during initial manual job creation and are preserved by `--update-env-vars`, but `--set-env-vars` on a fresh create only has the pipeline env vars.

**Fix:** Either create all jobs manually once and only use `update`, or add the required secrets to the create command.

---

## P2 — Medium (Improvements)

### 11. No warning when fuzzy match processes 0 leads

**File:** `lead_match_runtime/job_runner.py:726`

If all leads are excluded by exact-match guards, the loop exits silently with `processed_leads=0`. No warning is logged.

### 12. Cloud Build polling has no timeout

**File:** `.github/workflows/lead_match_semantic_workflow.yml:115-131`

The build polling loop has no max-wait. A queued Cloud Build hangs the step until the 240-min job timeout.

### 13. `SET LOCAL statement_timeout` placement

**File:** `lead_match_runtime/job_runner.py:748-749`

`SET LOCAL` only works within a transaction. pg8000's default autocommit=False means a transaction is implicitly open, so this works. However, the `SET LOCAL` is inside the `while` loop and re-executes for every batch. It should be set once before the loop, or use session-level `SET statement_timeout` instead.

---

## Summary

| # | Severity | Component | Issue | Status |
|---|---|---|---|---|
| 1 | **P0** | Cloud Workflow | No timeout on polling loop | **Active — causes 12-hour hangs** |
| 2 | **P0** | Cloud Workflow | Dead top-level field checks in `checkCompletion` | Harmless (second condition works) |
| 3 | **P0** | Cloud Workflow | `returnResult` returns wrong counts | Cosmetic but misleading |
| 4 | **P0** | Cloud Workflow | No error handling for missing Cloud Run job | **Caused first 569 failure** |
| 5 | **P0** | GitHub Actions | `set -e` kills polling on transient gcloud error | Latent risk |
| 6 | **P0** | GitHub Actions | `deploy_workflow` permission denied, no fallback | **Caused second 569 failure** |
| 7 | **P1** | Runtime | Embedding response count not validated | Silent data loss risk |
| 8 | **P1** | Runtime | Connection not closed on exception | Resource leak |
| 9 | **P1** | Runtime | No ON CONFLICT in embedding inserts | Fails on re-run |
| 10 | **P1** | GitHub Actions | Job `create` missing DB secrets | **Will block ensure-indexes job** |
| 11 | **P2** | Runtime | Silent exit on 0 leads | Debugging gap |
| 12 | **P2** | GitHub Actions | Cloud Build polling no timeout | Latent risk |
| 13 | **P2** | Runtime | Repeated `SET LOCAL` per batch | Minor waste |
