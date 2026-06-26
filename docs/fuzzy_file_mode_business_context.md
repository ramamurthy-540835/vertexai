# Lead-to-POS Matching Pipeline — Business Context

## What This Workflow Does

The **Lead Match Full Pipeline** workflow (`fuzzy_file_mode.yml`) runs the complete lead-to-POS matching pipeline for a given Costco warehouse. It connects to Cloud SQL, runs both matching engines in sequence, and produces a similarity CSV with all match results.

**Trigger:** Manual dispatch from GitHub Actions with warehouse number (default: 115).

## Two-Engine Architecture

### Engine 1: Exact Match (runs first)

Deterministic 5-field equality check: normalized `business_name + address + city + state + zip`.

| Field | What it checks |
|-------|---------------|
| Score | Always **100** |
| Result | **Match** (auto-close) |
| Lifecycle | **Closed - Match** |
| Action | Lead auto-closed. POS marked `is_processed = true`. |

If the business name and address match exactly (after normalization — uppercase, whitespace collapse, street abbreviation standardization), the lead is automatically closed. No human review needed.

### Engine 2: Fuzzy / Semantic Match (runs on leftovers)

Runs only on leads and POS records that exact match did NOT claim. Uses Vertex AI embeddings (gemini-embedding-001) to compute semantic similarity between business names and addresses.

| Field | What it checks |
|-------|---------------|
| Score range | **70 – 99.999** |
| Result | **Potential** (manual review) |
| Lifecycle | **Potential** |
| Action | Routed to ServiceNow for human review. Lead status NOT changed by fuzzy. |

Fuzzy **never** auto-closes a lead. Fuzzy **never** produces score 100. That is owned exclusively by exact.

### Score Bands

```
┌───────────┬─────────────────────────────────────┐
│   Score   │            Decision                  │
├───────────┼─────────────────────────────────────┤
│ 100       │ Match (exact only, auto-close)       │
├───────────┼─────────────────────────────────────┤
│ 70–99.999 │ Potential (fuzzy, manual review)      │
├───────────┼─────────────────────────────────────┤
│ < 70      │ No Match (rejected, no row written)   │
└───────────┴─────────────────────────────────────┘
```

## How Fuzzy Scoring Works

For each lead-POS candidate pair, the system evaluates **six matching sets** — different combinations of business name and address variants (including OMS/vendor-system variants):

| Set | Name Source | Address Source |
|-----|------------|----------------|
| 1 | POS business_name | POS address |
| 2 | OMS company name | POS address |
| 3 | POS business_name | OMS address |
| 4 | OMS company name | OMS address |
| 5 | POS business_name | OMS secondary address |
| 6 | OMS secondary company | OMS secondary address |

**Formula per set:**
```
set_score = (4 × address_similarity + 3 × name_similarity) / 7
```

The **best set** (highest score) wins. Then deterministic boosts are applied:
- Email exact match: **+5 points**
- Phone exact match: **+5 points**
- Final score capped at **99.999**

Email and phone are **never embedded** — they are deterministic confirmers only. Disagreement is neutral (no penalty), because a POS email can be a member's personal email.

## Business Rules

### `is_processed` — Transaction Consumed Once

Once a POS transaction is matched (by either engine), `is_processed` is set to `true`. That transaction is **excluded from all future matching pools**. A lead matched this week will not have its POS re-entered next week.

This prevents double-counting and ensures each transaction is attributed to exactly one lead.

### Fiscal Rules (Applied Before Scoring)

Every lead-POS pair is classified chronologically before fuzzy scoring:

| Classification | Condition | Action |
|----------------|-----------|--------|
| **Closed - Existing (CE)** | POS is before the lead AND within 6 fiscal periods | Lead marked CE. Stub row created. Lead removed from active set. |
| **OAF (Out-of-Fiscal-Window)** | POS is before the lead AND older than 6 periods | Pair silently dropped. Lead stays active for other matches. |
| **Normal** | POS is at or after the lead | Proceeds to fuzzy scoring. |

**Period gap formula:** `gap = (lead_FY - pos_FY) × 13 + (lead_FP - pos_FP)`

**Example:** Lead FY2026 P10 vs POS FY2026 P4 → gap = 6 → CE (Closed-Existing).
Lead FY2026 P10 vs POS FY2026 P3 → gap = 7 → OAF (dropped).

### Primary Transaction (NMI — New Matched Interaction)

The **earliest qualifying transaction** per lead (by fiscal year, period, week) with score ≥ 70 is the NMI (first conversion). Subsequent matched transactions for the same lead are Influenced Revenue only.

### Resolution

- One lead can match many POS records (one-to-many).
- One POS can match only one lead (one-to-one, highest score wins).
- If two leads compete for the same POS within **3 points**, it's routed to **Manual Review** (no auto-decision).

## Pipeline Steps

```
1. Preflight       → Validate business rules JSON + DB connectivity
2. Exact Match      → Deterministic 5-field match (score=100, auto-close)
3. Lead Embeddings  → Generate 768-dim vectors for unmatched leads (Vertex AI)
4. POS Embeddings   → Generate 768-dim vectors for unmatched POS (7 variants)
5. Ensure Indexes   → Create/maintain HNSW indexes for fast similarity search
6. Fuzzy Match      → Semantic matching on residual records (score 70-99.999)
7. Report           → Generate matches.csv + summary.json to GCS
```

Each step runs as a **Cloud Run Job** connected to **Cloud SQL** (ctoteam project, leadmgmt schema). The workflow orchestrates them sequentially via Google Cloud Workflows.

## Output

The pipeline produces a `matches.csv` in GCS:

```
gs://lead-match-ctoteam/reports/lead_match/ctoteam/{warehouse}/{run_id}/matches.csv
```

**Columns include:** lead_id, pos_id, match_type (Exact/Fuzzy/Manual Review), lifecycle_state, final_score, primary_transaction, lead/pos business names, fiscal period, order_amount, and more.

## Testing with Warehouse 115

To test the full pipeline on warehouse 115:

1. Go to **GitHub Actions** → **Lead Match Full Pipeline (Exact + Fuzzy)**
2. Click **Run workflow**
3. Set: `warehouse = 115`, `dry_run = true` (first run)
4. Wait for all 7 steps to complete
5. Check the matches.csv in GCS
6. If results look correct, re-run with `dry_run = false` to write back to Cloud SQL

**Dry run** previews match decisions without writing to business tables (lead status, transaction is_processed). The match_decision_detail table is still written for audit purposes.

## What This Does NOT Do

- Does NOT touch SPT or PRD environments (ctoteam only)
- Does NOT auto-close leads from fuzzy (only exact auto-closes)
- Does NOT upload mock data to GCS
- Does NOT change the business rules JSON
- Does NOT re-match already-processed POS transactions

## Configuration Source of Truth

All thresholds, weights, scoring formulas, and field mappings come from one file:

```
lead_match_runtime/lead_to_pos_match_rules.json
```

No hardcoded values in the code. To change a threshold, update the JSON.
