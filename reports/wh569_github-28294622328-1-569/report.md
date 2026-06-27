# Lead Match Run Report

- Project: `ctoteam`
- Schema: `leadmgmt`
- Warehouse: `569`
- Match run ID: `github-28294622328-1-569`
- Dry run: `False`
- Generated UTC: `2026-06-27T16:24:41.203058+00:00`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`
- Cloud SQL connection: `ctoteam:us-central1:lead-mgmt-db`
- Cloud SQL backend PID: `380590`

## Counts

- Leads: `73`
- POS transactions: `671`
- Lead embeddings: `73`
- POS embeddings: `112`
- Match rows: `563`
- Primary transactions: `0`

## Match Types

```json
{
  "Fuzzy": 52,
  "Manual Review": 31,
  "Closed - Existing": 480
}
```

## Lifecycle States

```json
{
  "Potential": 83,
  "Closed - Existing": 480
}
```

## Cloud SQL Sessions

```json
{
  "active": 1,
  "idle": 2,
  "unknown": 6
}
```

