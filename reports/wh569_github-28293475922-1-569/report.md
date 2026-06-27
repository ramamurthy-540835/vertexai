# Lead Match Run Report

- Project: `ctoteam`
- Schema: `leadmgmt`
- Warehouse: `569`
- Match run ID: `github-28293475922-1-569`
- Dry run: `False`
- Generated UTC: `2026-06-27T15:38:38.625391+00:00`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`
- Cloud SQL connection: `ctoteam:us-central1:lead-mgmt-db`
- Cloud SQL backend PID: `377144`

## Counts

- Leads: `73`
- POS transactions: `671`
- Lead embeddings: `73`
- POS embeddings: `112`
- Match rows: `622`
- Primary transactions: `69`

## Match Types

```json
{
  "Exact": 559,
  "Fuzzy": 30,
  "Manual Review": 7,
  "Closed - Existing": 26
}
```

## Lifecycle States

```json
{
  "Closed - Match": 559,
  "Potential": 37,
  "Closed - Existing": 26
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

