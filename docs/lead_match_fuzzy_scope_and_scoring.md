# Lead Match Fuzzy Scope and Scoring

This document is the simple operating guide for the semantic fuzzy matcher. The source of truth for thresholds is `config/lead_pos_business_rules.json`; the active Cloud Run implementation is `lead_match_runtime/job_runner.py`.

## Scope

Fuzzy matching is a recall step for leads and POS rows that were not already claimed by exact or deterministic matching. Exact matching is the primary layer. Fuzzy matching is the secondary layer and must only process the residual population that did not qualify in exact matching.

It is scoped by warehouse:

```text
lead.warehouse_number = pos.warehouse_number
```

Runtime can limit the warehouse set with `WAREHOUSE_SCOPE` or `WAREHOUSE`, for example `569` or `115,569`. Warehouse numbers are runtime scope only; they are not scoring rules.

## Fields Used

The semantic matcher embeds only business identity:

- `business_name`
- `full_address`, built from `address_line_one`, `city`, `state`, `zip_code`

It must not embed or score on member-personal or transaction-amount fields:

- `first_name`
- `last_name`
- `membership_number`
- `account_number`
- `order_amount`

## Candidate Rules

The matcher applies these gates before writing a fuzzy result:

1. Same warehouse is required.
2. Lead and POS embeddings must exist.
3. `combined_field_score` must be at least `65`.
4. Only the top `20` nearest POS candidates per lead are considered.
5. `final_score` must be at least `78`.
6. Existing exact or deterministic matches are excluded from fuzzy reassignment.

The exact-match guard checks both:

- `match_decision_detail.match_type IN ('Exact', 'Deterministic')`
- `transaction.match_type IN ('Exact', 'Deterministic')`

The default exact-type list also includes common primary-layer labels:

```text
Exact
Deterministic
Exact Match
Direct Match
Close Match
```

The minimum exact qualification score defaults to `80`.

This guard applies in two places:

- Before embeddings are generated, so exact-qualified leads/POS are not newly embedded for fuzzy matching.
- Before fuzzy scoring, so any previously embedded exact-qualified records are still excluded from fuzzy candidate selection.

The guard is global across match runs. It does not depend on the current fuzzy `match_run_id`.

## Score Formula

The active fuzzy score is:

```text
final_score = (4 * full_address_score + 3 * business_name_score) / 7
```

Component scores are cosine similarity percentages:

- `combined_field_score`: used for retrieval and recall gating.
- `full_address_score`: weighted at `4`.
- `business_name_score`: weighted at `3`.

The current Cloud Run SQL path does not apply email/phone boosts, even though helper functions exist for that future option.

## Penalty Policy

There is no separate negative penalty score in the current fuzzy matcher.

Instead, the algorithm uses gates and routing:

- Low combined identity similarity is filtered out by the `65` recall gate.
- Low weighted precision score is filtered out by the `78` qualify minimum.
- Exact and deterministic matches are skipped, not overwritten.
- Near-tie POS conflicts are routed to `Manual Review`.

Manual review is the penalty-like behavior today. If two leads compete for the same POS and the top score is within `3` points of the next score, the chosen row keeps its `final_score` but gets:

```text
match_type = Manual Review
```

This keeps the numeric score explainable while preventing automatic confidence on ambiguous rows.

## Lifecycle Classification

Fiscal timing is not a score penalty.

If the POS transaction is before the lead fiscal period/week, the report classifies it as:

```text
Closed - Existing
```

Otherwise it is classified as:

```text
Closed - Match
```

Manual review rows are reported as `Potential`.

## Write Scope

The active Cloud Run fuzzy step writes scored rows to:

```text
leadmgmt.match_decision_detail
```

It writes:

- `match_run_id`
- `lead_id`
- `pos_id`
- `warehouse_number`
- `match_type`
- `final_score`
- `combined_field_score`
- `full_address_score`
- `business_name_score`
- `weight_formula`
- `embedding_model`

It does not currently write back to:

- `lead.match_result`
- `transaction.lead_id`
- `transaction.match_type`
- `transaction.match_score`

Those writebacks should be a separate controlled step if needed.

## Maintenance Rules

Keep these rules stable unless there is labeled validation data:

1. Exact and deterministic matches stay authoritative.
2. Fuzzy is a recall add-on for exact misses.
3. Do not compare exact-match points and semantic similarity as the same score type.
4. Do not embed member-personal or amount fields.
5. Keep one POS transaction assigned to at most one lead.
6. Route near-ties to manual review instead of subtracting hidden score penalties.
7. Keep `raw component scores`, `final_score`, and `match_type` visible in reports.

## If A Penalty Score Is Added Later

Do not hide penalties inside `final_score`.

Use explicit fields instead:

```text
raw_score
penalty_score
final_score
manual_review_reason
```

That keeps the algorithm auditable. Any new penalty must also update:

- `config/lead_pos_business_rules.json`
- `lead_match_runtime/job_runner.py`
- report columns and CSV output
- smoke tests or validation checks
- this document
