# Lead-to-POS Matching Algorithm: Deep Dive

> **Audience:** Business stakeholders, data science reviewers, and client teams.
> **Purpose:** Explain how leads are matched to POS (Point-of-Sale) transactions,
> how scores are computed, and what decision logic determines the final outcome.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [High-Level Pipeline](#2-high-level-pipeline)
3. [Stage 1 -- Exact Matching](#3-stage-1----exact-matching)
4. [Stage 2 -- Semantic Fuzzy Matching](#4-stage-2----semantic-fuzzy-matching)
   - [4a. Data Normalization](#4a-data-normalization)
   - [4b. Embedding Generation](#4b-embedding-generation)
   - [4c. Candidate Retrieval (Recall Gate)](#4c-candidate-retrieval-recall-gate)
   - [4d. Precision Scoring Formula](#4d-precision-scoring-formula)
   - [4e. Deterministic Boosts](#4e-deterministic-boosts)
5. [Confidence Bands & Decision Thresholds](#5-confidence-bands--decision-thresholds)
6. [Ambiguity Detection & Manual Review Routing](#6-ambiguity-detection--manual-review-routing)
7. [POS-to-Lead Resolution (De-duplication)](#7-pos-to-lead-resolution-de-duplication)
8. [Lifecycle State Classification](#8-lifecycle-state-classification)
9. [Primary Transaction Selection](#9-primary-transaction-selection)
10. [Override Policy -- How Exact & Fuzzy Interact](#10-override-policy----how-exact--fuzzy-interact)
11. [Worked Examples](#11-worked-examples)
12. [Key Thresholds & Parameters (Reference Table)](#12-key-thresholds--parameters-reference-table)
13. [Glossary](#13-glossary)

---

## 1. Executive Summary

The Lead-to-POS matching system attributes Point-of-Sale transactions to
Business Development (BD) leads. Its goal is to answer: *"Did a lead that our
BD team tracked convert into an actual purchase?"*

The system runs a **two-stage pipeline**:

| Stage | Method | Strength |
|-------|--------|----------|
| **Exact Match** | Deterministic field-by-field comparison | High precision -- zero false positives when all fields align |
| **Semantic Fuzzy Match** | AI-powered vector similarity (embeddings) | High recall -- catches matches even when names or addresses have typos, abbreviations, or formatting differences |

Matches are scored on a **0--100 scale**, classified into confidence bands
(**High / Medium / Review / No Match**), and ambiguous cases are automatically
routed for **manual review** rather than being silently accepted or rejected.

---

## 2. High-Level Pipeline

```
                    +-----------------------+
                    |   Input: Leads + POS  |
                    |   (same warehouse)    |
                    +-----------+-----------+
                                |
                    +-----------v-----------+
                    |  Stage 1: Exact Match |
                    |  (deterministic)      |
                    +-----------+-----------+
                                |
              +-----------------+-----------------+
              |                                   |
     Matched (score=100)                  Not Matched
     State: Closed-Match                          |
     or Closed-Existing                           |
              |                   +---------------v---------------+
              |                   |  Stage 2: Semantic Fuzzy Match |
              |                   |  (AI embedding similarity)     |
              |                   +---------------+---------------+
              |                                   |
              |                   +---------------v---------------+
              |                   |  Confidence Band Assignment    |
              |                   |  High / Medium / Review / None |
              |                   +---------------+---------------+
              |                                   |
              |                   +---------------v---------------+
              |                   |  Ambiguity Check               |
              |                   |  (delta <= 3 -> Manual Review) |
              |                   +---------------+---------------+
              |                                   |
              +----------------+------------------+
                               |
                   +-----------v-----------+
                   | Final Match Decisions  |
                   | + Lifecycle States     |
                   | + Primary Transaction  |
                   +-----------------------+
```

---

## 3. Stage 1 -- Exact Matching

**What it does:** Compares leads to POS transactions using strict field-by-field
equality after normalization.

**Fields compared (all must match):**

| Field | Normalization Applied |
|-------|---------------------|
| Business Name | Uppercase, trim, collapse whitespace |
| Address Line 1 | Uppercase, standardize abbreviations (STREET->ST, AVENUE->AVE, etc.) |
| City | Uppercase |
| State | Uppercase, first 2 characters |
| Zip Code | First 5 digits only |

**Additional requirements:**
- Lead and POS must share the **same warehouse number**
- All five fields must be **non-empty** on both sides

**Outcome when matched:**
- Score = **100** (perfect match)
- Match type = **"Exact"**
- Leads matched here are **excluded** from the fuzzy stage (no double-counting)

**Why this matters:** Exact matching is fast, deterministic, and has zero false
positives. It handles the straightforward cases before the more nuanced fuzzy
matching takes over.

---

## 4. Stage 2 -- Semantic Fuzzy Matching

For leads not resolved by exact matching, the system uses **AI-powered semantic
similarity**. This is fundamentally different from traditional fuzzy matching
(e.g., Levenshtein edit distance). Here's why we chose this approach and how it
works.

### 4a. Data Normalization

Before any comparison, both lead and POS records go through normalization:

| Field | Rule | Example |
|-------|------|---------|
| **State** | Uppercase, 2-letter code | `california` -> `CA` |
| **Zip Code** | First 5 digits, strip non-digits | `98052-1234` -> `98052` |
| **Phone** | Digits only, 10-digit US format, strip leading 1 | `1-425-555-1234` -> `4255551234` |
| **Business Name** | Trim whitespace, collapse spaces, uppercase | `  Bob's  Pizza ` -> `BOB'S PIZZA` |
| **Address** | Trim, collapse spaces, standardize abbreviations | `123 Main Street` -> `123 MAIN ST` |

**Address abbreviation map:**

| Full Word | Abbreviation |
|-----------|-------------|
| STREET | ST |
| AVENUE | AVE |
| ROAD | RD |
| DRIVE | DR |
| LANE | LN |
| BOULEVARD | BLVD |

### 4b. Embedding Generation

Each record produces **three vector embeddings** -- numerical representations
that capture the semantic meaning of the text.

| Embedding Field | Source Data | Purpose |
|----------------|------------|---------|
| `combined_field` | Business name + full address | Used for initial candidate retrieval (recall gate) |
| `full_address` | Address line + city + state + zip | Precision scoring (weight: **4/7 ~ 57%**) |
| `business_name` | Business name only | Precision scoring (weight: **3/7 ~ 43%**) |

**Embedding model configuration:**

| Parameter | Value |
|-----------|-------|
| Model | Google `gemini-embedding-001` |
| Task Type | `SEMANTIC_SIMILARITY` |
| Output Dimensions | 768 |
| L2 Normalization | Enabled |

**Why embeddings instead of edit-distance?** Traditional fuzzy matching counts
character-level differences (e.g., "BOB'S PIZZA" vs "BOBS PIZZA" = edit
distance 1). Embeddings capture *meaning* -- they understand that "123 MAIN ST"
and "123 MAIN STREET" refer to the same location, or that "COSTCO WHOLESALE
#115" and "COSTCO WAREHOUSE 115" are semantically similar, even though their
character overlap is low.

### 4c. Candidate Retrieval (Recall Gate)

Before computing detailed scores, the system needs to narrow down which POS
records to compare each lead against. Comparing every lead to every POS record
would be computationally expensive, so we use a **recall gate**.

```
For each lead:
  1. Use the lead's combined_field embedding
  2. Find the top 20 most similar POS records using HNSW vector index
  3. Filter: keep only candidates with combined_field similarity >= 65%
```

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Nearest neighbor limit** | 20 | Balances recall with performance |
| **Recall gate threshold** | 65% similarity | Generous cutoff to avoid missing valid matches |
| **Index type** | HNSW (Hierarchical Navigable Small World) | Industry-standard approximate nearest neighbor search |
| **Distance metric** | Cosine similarity | Standard for semantic text comparison |

**Key insight:** The recall gate is intentionally lenient (65%) to avoid
discarding good matches early. The precision scoring stage below applies the
stricter thresholds.

### 4d. Precision Scoring Formula

For each candidate that passes the recall gate, the system computes a
**final score** using a weighted average of two component scores:

```
final_score = (4 x full_address_score + 3 x business_name_score) / 7
```

Where:
- `full_address_score` = cosine similarity of address embeddings x 100 (range 0--100)
- `business_name_score` = cosine similarity of name embeddings x 100 (range 0--100)

**Why weight address more heavily (57% vs 43%)?**

Address is a stronger identifier of a specific business *location*. Two
different businesses can share the same name (e.g., "Subway" appears thousands
of times), but a specific address pins down a unique location. The 4:3 weighting
reflects this:

| Scenario | Address Score | Name Score | Final Score | Outcome |
|----------|:------------:|:----------:|:-----------:|---------|
| Same business, same location | 95 | 92 | 93.7 | High confidence match |
| Same name, different location | 40 | 95 | 63.6 | Rejected (below 78) |
| Same location, slight name variation | 95 | 75 | 86.4 | Medium confidence match |
| Different business, different location | 30 | 25 | 27.9 | Rejected |

### 4e. Deterministic Boosts

After the semantic score is computed, deterministic signals can **boost** the
score (but never penalize):

| Signal | Boost | Condition |
|--------|:-----:|-----------|
| Email exact match | **+5 points** | Lead email = POS email (case-insensitive) |
| Phone exact match | **+5 points** | Lead phone = POS phone (normalized to 10 digits) |
| **Score cap** | **100** | Final score never exceeds 100 |

**Design decision -- why boosts only (no penalties)?**

Email and phone disagreement is **not penalized** because POS email/phone often
belongs to the purchasing *member* (personal), while the lead tracks the
*business*. A mismatch does not mean the business is different -- it may simply
mean the individual who made the purchase used their personal contact
information. This asymmetry (agreement confirms; disagreement is neutral) avoids
false negatives.

---

## 5. Confidence Bands & Decision Thresholds

Every match score maps to a **confidence band** that determines the system's
recommended action:

```
  0         78        85        92       100
  |----------|---------|---------|---------|
  | No Match | Review  | Medium  |  High   |
  | REJECT   | HUMAN   | LIKELY  | ACCEPT  |
```

| Band | Score Range | State Assigned | Interpretation |
|------|:----------:|----------------|----------------|
| **High** | 92 -- 100 | Closed - Match | Strong match. Auto-accepted with high confidence. |
| **Medium** | 85 -- 91.99 | Potential | Probable match. May warrant a quick human glance. |
| **Review** | 78 -- 84.99 | Potential | Possible match. Should be reviewed by a human. |
| **No Match** | 0 -- 77.99 | No Match | Insufficient similarity. Candidate is rejected. |

**Qualification threshold = 78:** Any score below 78 is discarded entirely.
This threshold was chosen to balance false positives (accepting wrong matches)
against false negatives (missing real matches). The system errs toward
conservative matching -- it is better to route a borderline case to manual
review than to auto-accept a wrong match.

---

## 6. Ambiguity Detection & Manual Review Routing

**Problem:** What if a POS transaction scores similarly against two different
leads? The system needs to detect this ambiguity rather than silently picking
one.

**Ambiguity rule:**

```
If (best_score - second_best_score) <= 3 points:
    -> Route to Manual Review
    -> Match type = "Manual Review"
    -> State = "Potential"
```

| Scenario | Lead A Score | Lead B Score | Delta | Decision |
|----------|:-----------:|:-----------:|:-----:|----------|
| Clear winner | 91.2 | 82.5 | 8.7 | Lead A wins -> "Semantic Fuzzy" match |
| Ambiguous | 88.4 | 86.1 | 2.3 | Manual Review (delta <= 3) |
| Close but clear | 90.0 | 86.5 | 3.5 | Lead A wins -> "Semantic Fuzzy" match |

**Why 3 points?** At the resolution of semantic similarity scoring, a 3-point
gap represents a marginal difference where the model's confidence in one
candidate over another is not statistically significant. Routing these cases to a
human avoids incorrect automated decisions in the ambiguous zone.

---

## 7. POS-to-Lead Resolution (De-duplication)

The system enforces a **one-to-one** constraint between POS transactions and
leads:

| Direction | Cardinality | Rule |
|-----------|:-----------:|------|
| Lead -> POS | One-to-Many | A single lead can match multiple POS transactions |
| POS -> Lead | **One-to-One** | Each POS transaction is attributed to **at most one** lead |

**Resolution strategy when multiple leads claim the same POS:**
1. Rank all leads by `final_score` (descending)
2. The **highest-scoring lead wins** the POS transaction
3. Check the score delta between 1st and 2nd place for ambiguity (see above)

This prevents double-counting: a single purchase cannot be credited to two
different BD leads.

---

## 8. Lifecycle State Classification

Each match is classified by its **temporal relationship** to the lead:

```
              Lead Created
                  |
  ----|-----------+-----------|-----> Time
      |                       |
  POS before lead         POS after lead
  = "Closed - Existing"  = "Closed - Match"
```

| State | Meaning | Fiscal Condition |
|-------|---------|-----------------|
| **Closed - Match** | New conversion -- the lead generated this sale | POS fiscal point >= Lead fiscal point |
| **Closed - Existing** | Pre-existing customer -- was already buying before BD tracked them | POS fiscal point < Lead fiscal point |
| **Potential** | Match exists but needs human review (medium/review band or ambiguous) | Score in 78--91.99 range or ambiguous |
| **No Match** | No qualifying POS transaction found | Score < 78 |

**Fiscal ordering:** Comparisons use Costco's fiscal calendar hierarchy:
**Year > Period > Week** (not calendar dates).

**Why this matters:** "Closed - Match" represents a genuine lead conversion
(new business). "Closed - Existing" indicates the business was already a
customer before BD engaged them -- important for accurately measuring BD's
impact.

---

## 9. Primary Transaction Selection

When a lead matches multiple POS transactions, one is designated as the
**primary transaction** -- the earliest qualifying match by fiscal ordering.

**Selection logic:**
1. Filter to transactions with `final_score >= 78`
2. Sort by fiscal year, then fiscal period, then week (ascending)
3. The **earliest** qualifying transaction becomes the primary

**Use case:** The primary transaction represents the *first* conversion event
attributable to a lead, which is typically the most important metric for
measuring BD effectiveness.

---

## 10. Override Policy -- How Exact & Fuzzy Interact

The two matching stages produce independent results. The **override policy**
determines which result wins:

```
                        +-----------------+
                        | Exact result?   |
                        +--------+--------+
                                 |
                    +------------+------------+
                    |                         |
                   Yes                       No
                    |                         |
            Use exact result         Use fuzzy result
            (authoritative)          (if available)
```

| Rule | Detail |
|------|--------|
| Exact match is **authoritative** | An exact match always wins over fuzzy. In practice exact matches score 100 (all five fields match). A configurable safety-net threshold (`exact_qualified_min_score = 80`) exists in the rules file but is never reached under normal operation. |
| Scores are **not compared** across types | An exact score is not compared to a fuzzy score -- exact always wins |
| Fuzzy is a **recall add-on** | Fuzzy matching only matters for leads that exact matching could not resolve |

**Design rationale:** Exact matching has zero false positives by definition
(all five fields match character-for-character). There is no scenario where a
fuzzy match should override a verified exact match, regardless of the fuzzy
score. The fuzzy stage exists to catch matches that exact matching misses due
to data inconsistencies.

---

## 11. Worked Examples

### Example A: Clear High-Confidence Match

| | Lead | POS |
|---|------|-----|
| Business Name | Bob's Auto Parts | BOB'S AUTO PARTS |
| Address | 1234 Main Street | 1234 MAIN ST |
| City | Seattle | SEATTLE |
| State | WA | WA |
| Zip | 98052 | 98052 |
| Warehouse | 115 | 115 |

**Exact match stage:** Normalized address = "1234 MAIN ST" on both sides.
All fields match. **Result: Exact match, score = 100, Closed - Match.**

Fuzzy stage is **skipped** for this lead (exact match guard).

---

### Example B: Fuzzy Match with Name Variation

| | Lead | POS |
|---|------|-----|
| Business Name | Pacific Northwest Plumbing | PAC NW PLUMBING LLC |
| Address | 567 Oak Avenue | 567 OAK AVE |
| City | Portland | PORTLAND |
| State | OR | OR |
| Zip | 97201 | 97201 |
| Warehouse | 823 | 823 |

**Exact match stage:** Business name differs ("PACIFIC NORTHWEST PLUMBING" vs
"PAC NW PLUMBING LLC"). **No exact match.**

**Fuzzy match stage:**
- Address embedding similarity: **96.1%** (addresses are semantically identical)
- Name embedding similarity: **81.3%** (abbreviation + LLC suffix differ, but
  semantic meaning is close)
- `final_score = (4 x 96.1 + 3 x 81.3) / 7 = (384.4 + 243.9) / 7 = 89.8`
- Confidence band: **Medium** (85--91.99)
- State: **Potential**

---

### Example C: Manual Review (Ambiguous Candidates)

A POS transaction for "JADE GARDEN" at "200 PIKE ST, SEATTLE, WA 98101" in
warehouse 115 matches two leads:

| Lead | Name Score | Address Score | Final Score |
|------|:----------:|:------------:|:-----------:|
| Lead #1: Jade Garden Restaurant | 88.2 | 94.5 | 91.8 |
| Lead #2: Jade Garden Catering | 85.7 | 94.0 | 90.4 |

**Delta = 91.8 - 90.4 = 1.4 points <= 3**

**Result:** Routed to **Manual Review**. A human must decide which lead this
POS transaction belongs to, because the system cannot distinguish the two with
sufficient confidence.

---

### Example D: Rejected Match (Below Threshold)

| | Lead | POS |
|---|------|-----|
| Business Name | Sunrise Bakery | SUNSET BAKERY |
| Address | 100 First Ave | 999 BROADWAY |
| City | Tacoma | Seattle |

- Address embedding similarity: **22.4%**
- Name embedding similarity: **78.1%**
- `final_score = (4 x 22.4 + 3 x 78.1) / 7 = (89.6 + 234.3) / 7 = 46.3`
- **Below qualification threshold (78). Rejected.**

Despite similar names, the addresses are completely different, and the address
weight (57%) ensures the mismatch is decisive.

---

## 12. Key Thresholds & Parameters (Reference Table)

| Parameter | Value | Purpose |
|-----------|:-----:|---------|
| Recall gate (combined field) | 65% | Minimum similarity to consider a candidate |
| Qualification threshold (final score) | 78 | Minimum score to count as a match |
| High confidence band | 92 -- 100 | Auto-accepted matches |
| Medium confidence band | 85 -- 91.99 | Probable matches |
| Review confidence band | 78 -- 84.99 | Matches requiring human review |
| Ambiguity delta | 3 points | Max score gap to trigger manual review |
| Address weight | 4/7 (57%) | Weight of address in final score |
| Business name weight | 3/7 (43%) | Weight of business name in final score |
| Email boost | +5 points | Bonus for exact email agreement |
| Phone boost | +5 points | Bonus for exact phone agreement |
| Score cap | 100 | Maximum possible score |
| Exact match qualification | 80 | Safety-net minimum for exact scores (always 100 in practice) |
| Nearest neighbor limit | 20 | Max candidates retrieved per lead |
| Embedding dimensions | 768 | Vector size for semantic comparison |

---

## 13. Glossary

| Term | Definition |
|------|-----------|
| **BD (Business Development)** | The team that identifies and tracks potential business leads |
| **Lead** | A business prospect tracked by BD with business name, address, and contact info |
| **POS (Point of Sale)** | A completed purchase transaction at a Costco warehouse |
| **Embedding** | A vector (list of 768 numbers) that represents the semantic meaning of text. Similar texts produce similar vectors. |
| **Cosine Similarity** | A mathematical measure of how similar two vectors are, ranging from 0% (completely different) to 100% (identical meaning) |
| **HNSW** | Hierarchical Navigable Small World -- an efficient algorithm for finding the most similar vectors in a large database |
| **Recall Gate** | A broad initial filter that retrieves candidate matches before applying detailed scoring |
| **Precision Score** | The detailed, weighted score that determines match quality |
| **Confidence Band** | A classification tier (High/Medium/Review/No Match) based on the final score |
| **Fiscal Point** | A position in Costco's fiscal calendar defined by year, period, and week |
| **Primary Transaction** | The earliest qualifying POS match for a lead -- represents the first conversion event |
| **Ambiguity Delta** | The minimum score gap between the top two candidates required to avoid manual review |
| **Deterministic Boost** | A fixed-point bonus added when exact email or phone matches are detected |

---

*Document generated from the lead-to-POS matching codebase. All thresholds
and weights are configurable in `lead_match_runtime/lead_to_pos_match_rules.json`.*
