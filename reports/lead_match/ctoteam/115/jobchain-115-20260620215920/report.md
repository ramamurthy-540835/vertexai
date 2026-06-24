# Lead Match Run Report

- Project: `ctoteam`
- Schema: `leadmgmt`
- Warehouse: `115`
- Match run ID: `jobchain-115-20260620215920`
- Generated UTC: `2026-06-20T22:45:41.543152+00:00`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`
- Cloud SQL connection: `ctoteam:us-central1:lead-mgmt-db`
- Cloud SQL backend PID: `26521`

## Counts

- Leads: `630`
- POS transactions: `18900`
- Lead embeddings: `630`
- POS embeddings: `18900`
- Match rows: `4953`
- Primary transactions: `479`

## Match Types

```json
{
  "Fuzzy": 3934,
  "Manual Review": 1019
}
```

## Lifecycle States

```json
{
  "Closed - Match": 3934,
  "Potential": 1019
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

