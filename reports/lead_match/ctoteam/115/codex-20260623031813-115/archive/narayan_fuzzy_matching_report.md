# Narayan Fuzzy Matching Report

- Generated UTC: `2026-06-23T03:51:38.889157+00:00`
- Project: `ctoteam`
- Warehouse: `115`
- Match run ID: `codex-20260623031813-115`
- Embedding model: `gemini-embedding-001`
- Embedding dimension: `768`

## Validation

```json
{
  "summary_match_rows": 8796,
  "csv_rows": 8796,
  "match_type_counts": {
    "Exact": 285,
    "Fuzzy": 6799,
    "Manual Review": 1712
  },
  "lifecycle_state_counts": {
    "Closed - Match": 4013,
    "Potential": 4783
  },
  "exact_count": 285,
  "exact_min": 100.0,
  "exact_max": 100.0,
  "non_exact_count": 8511,
  "non_exact_min": 83.6,
  "non_exact_max": 99.67,
  "non_exact_ge_100": 0,
  "below_70": 0,
  "component_scores_present": 8511,
  "band_counts": {
    "Matching High": 3728,
    "Potential Medium": 4755,
    "Potential Low": 28
  }
}
```

## Dataset Examples

| Band | Lead ID | POS ID | Match Type | Lifecycle | Lead Business | POS Business | Combined | Address | Business | Math | Stored |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- | ---: |
| Matching High | `LEADE98BBA4000000136` | `POS1E061DA600004082` | Fuzzy | Closed - Match | TEST - 06 - Garrison, Beck and Jacobs | TEST - 06 - Garrison, Beck and Jacobs | 99.39 | 99.42 | 100.00 | `(4*99.42 + 3*100.00) / 7 = 99.67` | 99.67 |
| Potential Medium | `LEADE98BBA4000000054` | `POS1E061DA600007345` | Fuzzy | Potential | TEST - 06 - Cook-Price | TEST - 06 - Cook LLC Pvt Ltd | 90.75 | 85.00 | 90.82 | `(4*85.00 + 3*90.82) / 7 = 87.50` | 87.50 |
| Potential Low | `LEADE98BBA4000000280` | `POS1E061DA600004687` | Fuzzy | Potential | TEST - 06 - Trevino, Carter and Thornton | TEST - 06 - Mcdonald, Hancock and Holt | 90.32 | 81.67 | 89.42 | `(4*81.67 + 3*89.42) / 7 = 84.99` | 84.99 |

## Recommended Reply

Hi Narayan,

We completed a full run for warehouse 115 and validated the fuzzy outputs against the current banding rules.

Confirmed: Vertex/fuzzy does not produce `Exact`. Exact remains deterministic only and is scored at `100`. Fuzzy scores are capped below `100`; in this run there were `0` fuzzy/non-exact rows at or above `100`.

Current first-draft marking:
- Deterministic score `100` -> `Exact / Complete`
- Fuzzy score `90-99.999` -> `Matching High / Closed - Match`
- Fuzzy score `85-89.999` -> `Potential Medium`
- Fuzzy score `70-84.999` -> `Potential Low`
- Below `70` -> `No Match` / no row

Run results:
- Total match rows: `8796`
- Exact: `285`
- Fuzzy: `6799`
- Manual Review: `1712`
- Matching High fuzzy rows: `3728`
- Potential Medium fuzzy rows: `4755`
- Potential Low fuzzy rows: `28`

On manual confirmation: only deterministic matches are `Exact`. High fuzzy matches can be treated as high-confidence AI matches for workflow acceleration, but they remain traceable as `Fuzzy`, not deterministic `Exact`. Medium and Low fuzzy matches should go to review/action queues. If the business wants every fuzzy match manually confirmed before becoming a closed match, we can configure that as a business workflow rule.

For the attribute-level walkthrough, each fuzzy row now includes component scores: `combined_field_score`, `full_address_score`, and `business_name_score`. The current deployed scoring formula is:

```text
final_score = (4 * full_address_score + 3 * business_name_score) / 7
```

This means address currently has 57.1% weight and business name has 42.9% weight. `combined_field_score` is retained as the semantic recall/evidence score.

We can walk through the examples in this report with Arun in the meeting.

## Prompt Used For Optional Reasoning Model

```text
You are helping draft a concise stakeholder reply to Narayan about a Costco lead-to-POS fuzzy matching run.
Use only the facts below. Do not invent values. Be clear that fuzzy never becomes deterministic Exact.

Summary:
{
  "project": "ctoteam",
  "schema": "leadmgmt",
  "warehouse": "115",
  "match_run_id": "codex-20260623031813-115",
  "dry_run": false,
  "generated_at": "2026-06-23T03:35:31.481665+00:00",
  "cloudsql_connection_name": "ctoteam:us-central1:lead-mgmt-db",
  "cloudsql_backend_pid": 140187,
  "cloudsql_session_state_counts": {
    "active": 1,
    "idle": 2,
    "unknown": 6
  },
  "embedding_model": "gemini-embedding-001",
  "embedding_dimension": 768,
  "lead_rows": 630,
  "pos_rows": 18900,
  "lead_embedding_rows": 630,
  "pos_embedding_rows": 18900,
  "match_rows": 8796,
  "match_type_counts": {
    "Exact": 285,
    "Fuzzy": 6799,
    "Manual Review": 1712
  },
  "lifecycle_state_counts": {
    "Closed - Match": 4013,
    "Potential": 4783
  },
  "primary_transaction_count": 431,
  "fuzzy_score_band": {
    "floor": 70.0,
    "ceiling": 99.999
  },
  "report_uris": {
    "summary_json": "gs://lead-match-ctoteam/reports/lead_match/ctoteam/115/codex-20260623031813-115/summary.json",
    "matches_csv": "gs://lead-match-ctoteam/reports/lead_match/ctoteam/115/codex-20260623031813-115/matches.csv",
    "report_md": "gs://lead-match-ctoteam/reports/lead_match/ctoteam/115/codex-20260623031813-115/report.md"
  }
}

Validation:
{
  "summary_match_rows": 8796,
  "csv_rows": 8796,
  "match_type_counts": {
    "Exact": 285,
    "Fuzzy": 6799,
    "Manual Review": 1712
  },
  "lifecycle_state_counts": {
    "Closed - Match": 4013,
    "Potential": 4783
  },
  "exact_count": 285,
  "exact_min": 100.0,
  "exact_max": 100.0,
  "non_exact_count": 8511,
  "non_exact_min": 83.6,
  "non_exact_max": 99.67,
  "non_exact_ge_100": 0,
  "below_70": 0,
  "component_scores_present": 8511,
  "band_counts": {
    "Matching High": 3728,
    "Potential Medium": 4755,
    "Potential Low": 28
  }
}

Current deployed formula:
final_score = (4 * full_address_score + 3 * business_name_score) / 7

Sample rows:
| Band | Lead ID | POS ID | Match Type | Lifecycle | Lead Business | POS Business | Combined | Address | Business | Math | Stored |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- | ---: |
| Matching High | `LEADE98BBA4000000136` | `POS1E061DA600004082` | Fuzzy | Closed - Match | TEST - 06 - Garrison, Beck and Jacobs | TEST - 06 - Garrison, Beck and Jacobs | 99.39 | 99.42 | 100.00 | `(4*99.42 + 3*100.00) / 7 = 99.67` | 99.67 |
| Potential Medium | `LEADE98BBA4000000054` | `POS1E061DA600007345` | Fuzzy | Potential | TEST - 06 - Cook-Price | TEST - 06 - Cook LLC Pvt Ltd | 90.75 | 85.00 | 90.82 | `(4*85.00 + 3*90.82) / 7 = 87.50` | 87.50 |
| Potential Low | `LEADE98BBA4000000280` | `POS1E061DA600004687` | Fuzzy | Potential | TEST - 06 - Trevino, Carter and Thornton | TEST - 06 - Mcdonald, Hancock and Holt | 90.32 | 81.67 | 89.42 | `(4*81.67 + 3*89.42) / 7 = 84.99` | 84.99 |

Draft a professional answer from Ram's architecture perspective:
- Confirm exact vs fuzzy behavior.
- Explain whether someone manually marks fuzzy as complete.
- Explain weightage and whether fuzzy >100 can become exact.
- Include 2-3 dataset examples with arithmetic.
- Keep it suitable for email or Teams.
```
