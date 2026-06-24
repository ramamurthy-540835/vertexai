# Lead Match Run Report

- Project: `ctoteam`
- Schema: `leadmgmt`
- Warehouse: `115`
- Match run ID: `github-27947943623-1-115`
- Dry run: `False`
- Generated UTC: `2026-06-22T11:13:33.351317+00:00`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`
- Cloud SQL connection: `ctoteam:us-central1:lead-mgmt-db`
- Cloud SQL backend PID: `105134`

## Counts

- Leads: `630`
- POS transactions: `18900`
- Lead embeddings: `630`
- POS embeddings: `18900`
- Match rows: `9440`
- Primary transactions: `630`

## Match Types

```json
{
  "Fuzzy": 7802,
  "Manual Review": 1638
}
```

## Lifecycle States

```json
{
  "Closed - Match": 7802,
  "Potential": 1638
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

