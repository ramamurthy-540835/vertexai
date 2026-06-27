# Costco Lead-to-POS Fuzzy Logic Business Report

## Executive Summary

- Warehouse `569` for match run `github-28293475922-1-569`.
- Leads processed: `73`; POS rows processed: `671`.
- Exact rows: `559`; fuzzy rows: `30`; manual review rows: `7`.
- Exact is deterministic and authoritative at score `100`; fuzzy runs only on residual unmatched records.
- The run produced `63` non-exact rows with no scores at or above `100`.
- Main conclusion: fuzzy coverage is active and traceable, while exact remains the only proven score-100 path.

## Business Rule Decision

Exact score 100 is deterministic and authoritative. Vertex/fuzzy is not deterministic Exact. Fuzzy runs only on residual records after exact matching.
Fuzzy `90-99.999` = `High / Potential`.
Fuzzy `85-89.999` = `Medium / Potential`.
Fuzzy `70-84.999` = `Low / Potential`.
Below `70` = `No Match / no row`.
High fuzzy matches may be actioned by business rule, but they must remain traceable as AI/fuzzy-sourced.

## Run Results

| Metric | Value |
| :-- | --: |
| Project | ctoteam |
| Warehouse | 569 |
| Match run ID | github-28293475922-1-569 |
| Leads | 73 |
| POS rows | 671 |
| Total match rows | 622 |
| Exact rows | 559 |
| Fuzzy rows | 30 |
| Manual Review rows | 7 |
| Primary transactions | 69 |

## Band Breakdown

| Band | Score Range | Rows | Lifecycle / Status | Notes |
| :-- | :-- | --: | :-- | :-- |
| High | 90-99.999 | 0 | Potential |  |
| Medium | 85-89.999 | 0 | Potential |  |
| Low | 70-84.999 | 0 | Potential |  |

## Scoring Model / Weightage

The deployed fuzzy score is computed as:

```text
(4 * address_score + 3 * name_score) / 7
```

Address weight: `57.1%`
Business name weight: `42.9%`
combined_field_score is used as semantic recall gate/evidence, not directly in the final score for this run.

## Example Walkthroughs



## Recommended Stakeholder Reply

Hi team,

We completed a full run for warehouse 569 and validated the fuzzy outputs against the current business rules.

Confirmed: Vertex/fuzzy does not produce `Exact`. Exact remains deterministic only and is scored at `100`. Fuzzy scores are capped below `100`; in this run there were `0` fuzzy/non-exact rows at or above `100`.

Current first-draft marking:
- Deterministic score `100` -> `Exact / Complete`
- Fuzzy score `90-99.999` -> `High`
- Fuzzy score `85-89.999` -> `Medium`
- Fuzzy score `70-84.999` -> `Low`
- Below `70` -> `No Match` / no row

Run results:
- Total match rows: `622`
- Exact: `559`
- Fuzzy: `30`
- Manual Review: `7`
- High rows: `0`
- Medium rows: `0`
- Low rows: `0`

On manual confirmation: not all fuzzy results should be marked Complete. High fuzzy matches can be actioned as `Matching High / Closed - Match` under the approved business rule, but they remain traceable as AI/fuzzy-sourced. Medium and Low fuzzy matches should go to review/action queues, or stay as `Potential` if business policy requires confirmation.

For the attribute-level walkthrough, each fuzzy row now includes component scores: `combined_field_score`, `full_address_score`, and `business_name_score`. The current deployed scoring formula is:

```text
(4 * address_score + 3 * name_score) / 7
```

This means address currently has 57.1% weight and business name has 42.9% weight. `combined_field_score` is retained as the semantic recall/evidence score.

We can walk through the examples in this report with stakeholders in the meeting.

## Validation Checks

- Summary rows match CSV rows: 622 vs 622
- Exact count / range: 559 / n/a to n/a
- Non-exact range: n/a to n/a
- Non-exact >=100: 0
- Below 70: 0
- Component scores present: 0

## Appendix: Optional Reasoning Prompt

Use this prompt only when you want an external model to draft a reply from the validated artifacts.

```text
You are helping draft a concise stakeholder reply about a Costco lead-to-POS fuzzy matching run.
Use only the facts below. Do not invent values.
Exact matching runs first. Fuzzy/Vertex AI runs only on the residual unmatched records.
Exact score 100 is the only authoritative Exact / Complete path.
Fuzzy output must never be labeled Exact.

Summary facts:
- Warehouse: 569
- Match run ID: github-28293475922-1-569
- Project: ctoteam
- Leads: 73
- POS rows: 671
- Total match rows: 622
- Exact rows: 559
- Fuzzy rows: 30
- Manual Review rows: 7

Current deployed formula:
(4 * address_score + 3 * name_score) / 7

Sample rows:
| Band | Lead ID | POS ID | Match Type | Lifecycle | Lead Business | POS Business | Combined | Address | Business | Math | Stored |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | ---: | ---: | ---: | :--- | ---: |

Draft a professional answer from an architecture perspective.
Confirm exact vs fuzzy behavior, manual Complete handling, weightage, and the residual-record flow.
Keep it suitable for email or Teams and avoid raw JSON unless explicitly provided.
```

## Footer

Generated UTC: `2026-06-27T15:40:59.005484+00:00`
Generated from deterministic report artifacts: summary.json and matches.csv. No external model was required.
