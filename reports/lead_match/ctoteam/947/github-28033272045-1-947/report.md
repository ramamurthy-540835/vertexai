# Lead Match Run Report

- Project: `ctoteam`
- Schema: `leadmgmt`
- Warehouse: `947`
- Match run ID: `github-28033272045-1-947`
- Dry run: `False`
- Generated UTC: `2026-06-23T16:25:58.336642+00:00`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`
- Cloud SQL connection: `ctoteam:us-central1:lead-mgmt-db`
- Cloud SQL backend PID: `169033`

## Counts

- Leads: `310`
- POS transactions: `8100`
- Lead embeddings: `216`
- POS embeddings: `7941`
- Match rows: `3463`
- Primary transactions: `222`

## Match Types

```json
{
  "Exact": 159,
  "Fuzzy": 2888,
  "Manual Review": 416
}
```

## Lifecycle States

```json
{
  "Closed - Match": 710,
  "Potential": 2753
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

