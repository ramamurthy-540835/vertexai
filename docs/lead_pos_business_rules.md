# Lead-to-POS Business Rules

This matcher is driven by `config/lead_pos_business_rules.json`.

For the fuzzy-match algorithm scope, score formula, and penalty policy, see `docs/lead_match_fuzzy_scope_and_scoring.md`.

Warehouse `115` is only a runtime test scope. It is not a business rule. The business rule is always:

```text
lead.warehouse_number = pos.warehouse_number
```

The runtime scope can be:

```bash
WAREHOUSE_SCOPE=ALL
WAREHOUSE_SCOPE=115
WAREHOUSE_SCOPE=115,116,117
```

The schema comes from `DB_SCHEMA` or the business-rule config default. SQL must not hardcode client schemas.

Semantic embeddings use only business identity:

- `business_name`
- `full_address`, built from `address_line_one`, `city`, `state`, `zip_code`

The engine must not embed member-personal or transaction-amount fields:

- `first_name`
- `last_name`
- `membership_number`
- `order_amount`
- `account_number`

Exact matching remains authoritative when it qualifies. Semantic matching is a recall add-on for records exact matching missed. Exact points and semantic similarity are not compared as raw numbers.

There is no 6-month attribution rule. Attribution is based on warehouse blocking and fiscal ordering:

- Transaction on or after the lead fiscal period: `Closed - Match`
- Strong transaction before the lead fiscal period/week: `Closed - Existing`

For POS conflicts, one POS transaction can belong to at most one lead. The strongest lead wins unless the top two scores are within the ambiguity delta, in which case the row goes to manual review.
