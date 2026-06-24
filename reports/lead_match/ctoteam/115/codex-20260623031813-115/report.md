# Lead Match Run Report

- Project: `ctoteam`
- Schema: `leadmgmt`
- Warehouse: `115`
- Match run ID: `codex-20260623031813-115`
- Dry run: `False`
- Generated UTC: `2026-06-23T03:35:31.481665+00:00`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`
- Cloud SQL connection: `ctoteam:us-central1:lead-mgmt-db`
- Cloud SQL backend PID: `140187`

## Counts

- Leads: `630`
- POS transactions: `18900`
- Lead embeddings: `630`
- POS embeddings: `18900`
- Match rows: `8796`
- Primary transactions: `431`

## Match Types

```json
{
  "Exact": 285,
  "Fuzzy": 6799,
  "Manual Review": 1712
}
```

## Lifecycle States

```json
{
  "Closed - Match": 4013,
  "Potential": 4783
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

