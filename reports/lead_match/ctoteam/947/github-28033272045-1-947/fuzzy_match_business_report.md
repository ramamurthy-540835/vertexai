# Costco Lead-to-POS Fuzzy Logic Business Report

## Executive Summary

- Warehouse `947` for match run `github-28033272045-1-947`.
- Leads processed: `310`; POS rows processed: `8100`.
- Exact rows: `159`; fuzzy rows: `2888`; manual review rows: `416`.
- Exact is deterministic and authoritative at score `100`; fuzzy runs only on residual unmatched records.
- The run produced `3304` non-exact rows with no scores at or above `100`.
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
| Warehouse | 947 |
| Match run ID | github-28033272045-1-947 |
| Leads | 310 |
| POS rows | 8100 |
| Total match rows | 3463 |
| Exact rows | 159 |
| Fuzzy rows | 2888 |
| Manual Review rows | 416 |
| Primary transactions | 222 |

## Band Breakdown

| Band | Score Range | Rows | Lifecycle / Status | Notes |
| :-- | :-- | --: | :-- | :-- |
| Matching High | 90-99.999 | 551 | Closed - Match | Strong AI inference; still not Exact |
| Potential Medium | 85-89.999 | 2439 | Potential | Likely candidate, needs confirmation |
| Potential Low | 70-84.999 | 314 | Potential | Possible candidate, needs review |

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
- lead_id: `LEAD508C534600000035`
- pos_id: `POS8D0FC85E00000697`
- lead business: Anderson Reyes and Fitzgerald
- POS business: Anderson Reyes and Fitzgerald
- address score: `99.64`
- business score: `100.00`
- arithmetic: `(4 * 99.64 + 3 * 100.00) / 7 = 99.79`
- final score: `99.79`
- interpretation: Matching High

### Potential Medium
- lead_id: `LEAD508C534600000101`
- pos_id: `POS8D0FC85E00000226`
- lead business: North Kitchen Supply
- POS business: Summit Kitchen
- address score: `87.79`
- business score: `87.12`
- arithmetic: `(4 * 87.79 + 3 * 87.12) / 7 = 87.50`
- final score: `87.50`
- interpretation: Potential Medium

### Potential Low
- lead_id: `LEAD508C534600000122`
- pos_id: `POS8D0FC85E00002823`
- lead business: Sullivan Murphy and Joyce
- POS business: Murphy LLC
- address score: `84.17`
- business score: `86.07`
- arithmetic: `(4 * 84.17 + 3 * 86.07) / 7 = 84.99`
- final score: `84.99`
- interpretation: Potential Low

## Recommended Stakeholder Reply

Hi team,

We completed a full run for warehouse 947 and validated the fuzzy outputs against the current business rules.

Confirmed: Vertex/fuzzy does not produce `Exact`. Exact remains deterministic only and is scored at `100`. Fuzzy scores are capped below `100`; in this run there were `0` fuzzy/non-exact rows at or above `100`.

Current first-draft marking:
- Deterministic score `100` -> `Exact / Complete`
- Fuzzy score `90-99.999` -> `Matching High`
- Fuzzy score `85-89.999` -> `Potential Medium`
- Fuzzy score `70-84.999` -> `Potential Low`
- Below `70` -> `No Match` / no row

Run results:
- Total match rows: `3463`
- Exact: `159`
- Fuzzy: `2888`
- Manual Review: `416`
- Matching High rows: `551`
- Potential Medium rows: `2439`
- Potential Low rows: `314`

On manual confirmation: not all fuzzy results should be marked Complete. High fuzzy matches can be actioned as `Matching High / Closed - Match` under the approved business rule, but they remain traceable as AI/fuzzy-sourced. Medium and Low fuzzy matches should go to review/action queues, or stay as `Potential` if business policy requires confirmation.

For the attribute-level walkthrough, each fuzzy row now includes component scores: `combined_field_score`, `full_address_score`, and `business_name_score`. The current deployed scoring formula is:

```text
(4 * full_address_score + 3 * business_name_score) / 7
```

This means address currently has 57.1% weight and business name has 42.9% weight. `combined_field_score` is retained as the semantic recall/evidence score.

We can walk through the examples in this report with stakeholders in the meeting.

## Validation Checks

- Summary rows match CSV rows: 3463 vs 3463
- Exact count / range: 159 / 100.00 to 100.00
- Non-exact range: 79.64 to 99.79
- Non-exact >=100: 0
- Below 70: 0
- Component scores present: 3304

## Appendix: Optional Reasoning Prompt

Use this prompt only when you want an external model to draft a reply from the validated artifacts.

```text
You are helping draft a concise stakeholder reply about a Costco lead-to-POS fuzzy matching run.
Use only the facts below. Do not invent values.
Exact matching runs first. Fuzzy/Vertex AI runs only on the residual unmatched records.
Exact score 100 is the only authoritative Exact / Complete path.
Fuzzy output must never be labeled Exact.

Summary facts:
- Warehouse: 947
- Match run ID: github-28033272045-1-947
- Project: ctoteam
- Leads: 310
- POS rows: 8100
- Total match rows: 3463
- Exact rows: 159
- Fuzzy rows: 2888
- Manual Review rows: 416

Current deployed formula:
(4 * full_address_score + 3 * business_name_score) / 7

Sample rows:
| Band | Lead ID | POS ID | Match Type | Lifecycle | Lead Business | POS Business | Combined | Address | Business | Math | Stored |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- | ---: |
| Matching High | `LEAD508C534600000035` | `POS8D0FC85E00000697` | Fuzzy | Closed - Match | Anderson Reyes and Fitzgerald | Anderson Reyes and Fitzgerald | 99.73 | 99.64 | 100.00 | `(4*99.64 + 3*100.00) / 7 = 99.79` | 99.79 |
| Potential Medium | `LEAD508C534600000101` | `POS8D0FC85E00000226` | Fuzzy | Potential | North Kitchen Supply | Summit Kitchen | 89.00 | 87.79 | 87.12 | `(4*87.79 + 3*87.12) / 7 = 87.50` | 87.50 |
| Potential Low | `LEAD508C534600000122` | `POS8D0FC85E00002823` | Fuzzy | Potential | Sullivan Murphy and Joyce | Murphy LLC | 86.54 | 84.17 | 86.07 | `(4*84.17 + 3*86.07) / 7 = 84.99` | 84.99 |

Draft a professional answer from an architecture perspective.
Confirm exact vs fuzzy behavior, manual Complete handling, weightage, and the residual-record flow.
Keep it suitable for email or Teams and avoid raw JSON unless explicitly provided.
```

## Footer

Generated UTC: `2026-06-23T18:08:02.384097+00:00`
Generated from deterministic report artifacts: summary.json and matches.csv. No external model was required.
