# Lead Match Run Report

- Project: `ctoteam`
- Schema: `leadmgmt`
- Warehouse: `827`
- Match run ID: `github-28029672690-1-827`
- Dry run: `False`
- Generated UTC: `2026-06-23T14:53:54.925511+00:00`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`
- Cloud SQL connection: `ctoteam:us-central1:lead-mgmt-db`
- Cloud SQL backend PID: `165754`

## Counts

- Leads: `250`
- POS transactions: `7500`
- Lead embeddings: `170`
- POS embeddings: `7351`
- Match rows: `2874`
- Primary transactions: `184`

## Match Types

```json
{
  "Exact": 149,
  "Fuzzy": 2357,
  "Manual Review": 368
}
```

## Lifecycle States

```json
{
  "Closed - Match": 553,
  "Potential": 2321
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

