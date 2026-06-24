# Warehouse 115 Fuzzy Matching Validation and Narayan Reply

## Local Report Artifacts

- `summary.json`
- `matches.csv`
- `report.md`

GCS source:
`gs://lead-match-ctoteam/reports/lead_match/ctoteam/115/codex-20260623031813-115/`

Local folder:
`reports/lead_match/ctoteam/115/codex-20260623031813-115/`

## Validation Summary

- Project: `ctoteam`
- Warehouse: `115`
- Match run ID: `codex-20260623031813-115`
- Leads: `630`
- POS rows: `18,900`
- Lead embeddings: `630`
- POS embeddings: `18,900`
- Total match rows: `8,796`
- Exact rows: `285`
- Fuzzy rows: `6,799`
- Manual Review rows: `1,712`

Score checks:

- Exact min/max/count: `100.0 / 100.0 / 285`
- Non-exact min/max/count: `83.6 / 99.67 / 8,511`
- Non-exact rows `>=100`: `0`
- Rows `<70`: `0`
- Fuzzy rows on exact-claimed leads: `0`
- MDD Exact but transaction non-Exact: `0`

Fuzzy band counts from `matches.csv`:

- Matching High, `90-99.999`: `3,728`
- Potential Medium, `85-89.999`: `4,755`
- Potential Low, `70-84.999`: `28`
- Out of band: `0`

## Important Rule Clarification

Vertex/fuzzy never produces `Exact`. Exact remains deterministic and rule-proven only.

For the current first draft:

- Deterministic score `100` -> `Exact / Complete`
- Fuzzy score `90-99.999` -> `Matching High / Closed - Match`
- Fuzzy score `85-89.999` -> `Potential Medium`
- Fuzzy score `70-84.999` -> `Potential Low`
- Below `70` -> rejected / no row

High fuzzy matches can be treated as high-confidence AI matches for workflow acceleration, but they are still not deterministic `Exact`.

## Current Implemented Fuzzy Formula

The current deployed run uses:

```text
final_score = (4 * full_address_score + 3 * business_name_score) / 7
```

`combined_field_score` is captured in the CSV and used as the semantic recall gate/evidence, but it is not part of the final weighted score in this run.

## Dataset Walkthrough Samples

### Matching High Example

- lead_id: `LEADE98BBA4000000136`
- pos_id: `POS1E061DA600004082`
- match_type: `Fuzzy`
- lifecycle_state: `Closed - Match`
- Lead business: `TEST - 06 - Garrison, Beck and Jacobs`
- Lead address: `513 Elizabeth Forest, New Frank, OH, 45003`
- POS business: `TEST - 06 - Garrison, Beck and Jacobs`
- POS address: `513 Elizabeth Forest, New Frank, OR, 45003`
- combined_field_score: `99.3878`
- full_address_score: `99.4171`
- business_name_score: `100.0000`

Arithmetic:

```text
(4 * 99.4171 + 3 * 100.0000) / 7 = 99.67
```

Result: `99.67`, Matching High.

### Potential Medium Example

- lead_id: `LEADE98BBA4000000100`
- pos_id: `POS1E061DA600004765`
- match_type: `Fuzzy`
- lifecycle_state: `Potential`
- Lead business: `TEST - 06 - Solomon LLC`
- Lead address: `6898 Cynthia Mountain Apt. 704, Reynoldsmouth, PA, 93152`
- POS business: `TEST - 06 - Randolph LLC Inc`
- POS address: `0438 Erickson Land Apt. 807, North Kevinton, pa, 05796`
- combined_field_score: `91.4806`
- full_address_score: `88.0445`
- business_name_score: `92.5871`

Arithmetic:

```text
(4 * 88.0445 + 3 * 92.5871) / 7 = 89.99
```

Result: `89.99`, Potential Medium.

### Potential Low Example

- lead_id: `LEADE98BBA4000000280`
- pos_id: `POS1E061DA600004687`
- match_type: `Fuzzy`
- lifecycle_state: `Potential`
- Lead business: `TEST - 06 - Trevino, Carter and Thornton`
- Lead address: `092 Wilson Rest, Macdonaldfurt, MI, 49184`
- POS business: `TEST - 06 - Mcdonald, Hancock and Holt`
- POS address: `51329 Ortiz Shore, Woodsmouth, IL, 72216`
- combined_field_score: `90.3152`
- full_address_score: `81.6694`
- business_name_score: `89.4229`

Arithmetic:

```text
(4 * 81.6694 + 3 * 89.4229) / 7 = 84.99
```

Result: `84.99`, Potential Low.

## Draft Reply

Hi Narayan,

We completed a full warehouse 115 run and validated the fuzzy outputs against the current banding rules.

Confirmed: Vertex/fuzzy does not produce `Exact`. Exact remains deterministic only and is scored at `100`. Fuzzy scores are capped below `100`; in this run there were `0` fuzzy rows at or above `100`.

For the first draft, we are using:

- `100` deterministic -> Exact / Complete
- `90-99.999` fuzzy -> Matching High / Closed - Match
- `85-89.999` fuzzy -> Potential Medium
- `70-84.999` fuzzy -> Potential Low
- `<70` -> No Match / no row

On whether someone manually marks them complete: only deterministic matches are `Exact`. High fuzzy matches can be treated as high-confidence AI matches for the first draft, but they remain traceable as `Fuzzy`, not `Exact`. Medium and low fuzzy matches should go to review/action queues. If the business wants every fuzzy match manually confirmed before becoming closed, we can keep all fuzzy bands as Potential; that is a business operating decision, not a Vertex scoring limitation.

For the attribute-level walkthrough, the report now has component scores per row: `combined_field_score`, `full_address_score`, and `business_name_score`. In the current run, the deployed formula is:

```text
final_score = (4 * full_address_score + 3 * business_name_score) / 7
```

This gives address a 57.1 percent weight and business name a 42.9 percent weight. `combined_field_score` is retained as the semantic recall-gate/evidence score.

We can walk through examples from WH115 in the meeting. For example, one Matching High row had address score `99.4171` and business name score `100.0000`:

```text
(4 * 99.4171 + 3 * 100.0000) / 7 = 99.67
```

That lands in Matching High. Medium and Low examples are also available in the report folder so we can review how the score changes when address/name similarity drops.

