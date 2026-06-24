# Costco Lead-to-POS Fuzzy Logic Business Report

## Executive Summary

- Warehouse `115` for match run `codex-20260623031813-115`.
- Leads processed: `630`; POS rows processed: `18900`.
- Exact rows: `285`; fuzzy rows: `6799`; manual review rows: `1712`.
- Exact is deterministic and authoritative at score `100`; fuzzy runs only on residual unmatched records.
- The run produced `8511` non-exact rows with no scores at or above `100`.
- Main conclusion: fuzzy coverage is active and traceable, while exact remains the only proven score-100 path.

## Business Rule Decision

Exact score 100 is deterministic and authoritative. Vertex/fuzzy is not deterministic Exact. Fuzzy runs only on residual records after exact matching.
Fuzzy `90-99.999` = `Matching High / Closed - Match`.
Fuzzy `85-89.999` = `Potential Medium / Potential`.
Fuzzy `70-84.999` = `Potential Low / Potential`.
Below `70` = `No Match / no row`.
High fuzzy matches may be actioned by business rule, but they must remain traceable as AI/fuzzy-sourced.

## Run Results

| Metric | Value |
| :-- | --: |
| Project | ctoteam |
| Warehouse | 115 |
| Match run ID | codex-20260623031813-115 |
| Leads | 630 |
| POS rows | 18900 |
| Total match rows | 8796 |
| Exact rows | 285 |
| Fuzzy rows | 6799 |
| Manual Review rows | 1712 |
| Primary transactions | 431 |

## Band Breakdown

| Band | Score Range | Rows | Lifecycle / Status | Notes |
| :-- | :-- | --: | :-- | :-- |
| Matching High | 90-99.999 | 3728 | Closed - Match | Strong AI inference; still not Exact |
| Potential Medium | 85-89.999 | 4755 | Potential | Likely candidate, needs confirmation |
| Potential Low | 70-84.999 | 28 | Potential | Possible candidate, needs review |

## Scoring Model / Weightage

The deployed fuzzy score is computed as:

```text
(4 * full_address_score + 3 * business_name_score) / 7
```

Address weight: `57.1%`
Business name weight: `42.9%`
combined_field_score is used as semantic recall gate/evidence, not directly in the final score for this run.

## Example Walkthroughs

### Matching High
- lead_id: `LEADE98BBA4000000136`
- pos_id: `POS1E061DA600004082`
- lead business: TEST - 06 - Garrison, Beck and Jacobs
- POS business: TEST - 06 - Garrison, Beck and Jacobs
- address score: `99.42`
- business score: `100.00`
- arithmetic: `(4 * 99.42 + 3 * 100.00) / 7 = 99.67`
- final score: `99.67`
- interpretation: Matching High

### Potential Medium
- lead_id: `LEADE98BBA4000000054`
- pos_id: `POS1E061DA600007345`
- lead business: TEST - 06 - Cook-Price
- POS business: TEST - 06 - Cook LLC Pvt Ltd
- address score: `85.00`
- business score: `90.82`
- arithmetic: `(4 * 85.00 + 3 * 90.82) / 7 = 87.50`
- final score: `87.50`
- interpretation: Potential Medium

### Potential Low
- lead_id: `LEADE98BBA4000000280`
- pos_id: `POS1E061DA600004687`
- lead business: TEST - 06 - Trevino, Carter and Thornton
- POS business: TEST - 06 - Mcdonald, Hancock and Holt
- address score: `81.67`
- business score: `89.42`
- arithmetic: `(4 * 81.67 + 3 * 89.42) / 7 = 84.99`
- final score: `84.99`
- interpretation: Potential Low

## Recommended Stakeholder Reply

Hi team,

We completed a full run for warehouse 115 and validated the fuzzy outputs against the current business rules.

Confirmed: Vertex/fuzzy does not produce `Exact`. Exact remains deterministic only and is scored at `100`. Fuzzy scores are capped below `100`; in this run there were `0` fuzzy/non-exact rows at or above `100`.

Current first-draft marking:
- Deterministic score `100` -> `Exact / Complete`
- Fuzzy score `90-99.999` -> `Matching High`
- Fuzzy score `85-89.999` -> `Potential Medium`
- Fuzzy score `70-84.999` -> `Potential Low`
- Below `70` -> `No Match` / no row

Run results:
- Total match rows: `8796`
- Exact: `285`
- Fuzzy: `6799`
- Manual Review: `1712`
- Matching High rows: `3728`
- Potential Medium rows: `4755`
- Potential Low rows: `28`

On manual confirmation: not all fuzzy results should be marked Complete. High fuzzy matches can be actioned as `Matching High / Closed - Match` under the approved business rule, but they remain traceable as AI/fuzzy-sourced. Medium and Low fuzzy matches should go to review/action queues, or stay as `Potential` if business policy requires confirmation.

For the attribute-level walkthrough, each fuzzy row now includes component scores: `combined_field_score`, `full_address_score`, and `business_name_score`. The current deployed scoring formula is:

```text
(4 * full_address_score + 3 * business_name_score) / 7
```

This means address currently has 57.1% weight and business name has 42.9% weight. `combined_field_score` is retained as the semantic recall/evidence score.

We can walk through the examples in this report with stakeholders in the meeting.

## Validation Checks

- Summary rows match CSV rows: 8796 vs 8796
- Exact count / range: 285 / 100.00 to 100.00
- Non-exact range: 83.60 to 99.67
- Non-exact >=100: 0
- Below 70: 0
- Component scores present: 8511

## Appendix: Optional Reasoning Prompt

Use this prompt only when you want an external model to draft a reply from the validated artifacts.

```text
You are helping draft a concise stakeholder reply about a Costco lead-to-POS fuzzy matching run.
Use only the facts below. Do not invent values.
Exact matching runs first. Fuzzy/Vertex AI runs only on the residual unmatched records.
Exact score 100 is the only authoritative Exact / Complete path.
Fuzzy output must never be labeled Exact.

Summary facts:
- Warehouse: 115
- Match run ID: codex-20260623031813-115
- Project: ctoteam
- Leads: 630
- POS rows: 18900
- Total match rows: 8796
- Exact rows: 285
- Fuzzy rows: 6799
- Manual Review rows: 1712

Current deployed formula:
(4 * full_address_score + 3 * business_name_score) / 7

Sample rows:
| Band | Lead ID | POS ID | Match Type | Lifecycle | Lead Business | POS Business | Combined | Address | Business | Math | Stored |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- | ---: |
| Matching High | `LEADE98BBA4000000136` | `POS1E061DA600004082` | Fuzzy | Closed - Match | TEST - 06 - Garrison, Beck and Jacobs | TEST - 06 - Garrison, Beck and Jacobs | 99.39 | 99.42 | 100.00 | `(4*99.42 + 3*100.00) / 7 = 99.67` | 99.67 |
| Potential Medium | `LEADE98BBA4000000054` | `POS1E061DA600007345` | Fuzzy | Potential | TEST - 06 - Cook-Price | TEST - 06 - Cook LLC Pvt Ltd | 90.75 | 85.00 | 90.82 | `(4*85.00 + 3*90.82) / 7 = 87.50` | 87.50 |
| Potential Low | `LEADE98BBA4000000280` | `POS1E061DA600004687` | Fuzzy | Potential | TEST - 06 - Trevino, Carter and Thornton | TEST - 06 - Mcdonald, Hancock and Holt | 90.32 | 81.67 | 89.42 | `(4*81.67 + 3*89.42) / 7 = 84.99` | 84.99 |

Draft a professional answer from an architecture perspective.
Confirm exact vs fuzzy behavior, manual Complete handling, weightage, and the residual-record flow.
Keep it suitable for email or Teams and avoid raw JSON unless explicitly provided.
```

## Footer

Generated UTC: `2026-06-23T06:32:15.786295+00:00`
Generated from deterministic report artifacts: summary.json and matches.csv. No external model was required.
