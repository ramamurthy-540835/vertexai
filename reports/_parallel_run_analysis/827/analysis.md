# Parallel Run Analysis — Warehouse 827

**Run:** `github-28029672690-1-827`
**Generated:** 2026-06-24T05:55:43.970651+00:00

---

## Match Counts

| Metric | Value |
|--------|-------|
| Leads | 250 |
| POS transactions | 7,500 |
| Lead embeddings | 170 |
| POS embeddings | 7,351 |
| Total match rows | 2,874 |
| Exact | 149 |
| Fuzzy | 2,357 |
| Manual Review | 368 |
| Primary transactions | 184 |

## Confidence Bands (non-exact)

| Band | Range | Count |
|------|-------|-------|
| Matching High | 90 – 99.999 | 404 |
| Potential Medium | 85 – 89.999 | 1,984 |
| Potential Low | 70 – 84.999 | 337 |

## Lifecycle Split

**Closed - Match** (553 total): **149 Exact** (proven) + **404 Matching High fuzzy** (AI-inferred)
**Potential**: 2,321

## Score Statistics (non-exact)

| Stat | Value |
|------|-------|
| Min | 79.35 |
| Max | 99.74 |
| Mean | 87.874 |
| Median | 88.18 |
| Std Dev | 2.475 |
| Peak bin | [89, 90) (527 rows) |

## Review Workload

Rows in review queue (70 – 89.999): **2,321** (80.76% of all match rows)

## Data Integrity Check

| Metric | Leads | POS |
|--------|-------|-----|
| Total records | 250 | 7,500 |
| Embedded | 170 | 7,351 |
| Un-embedded (actual) | 80 | 149 |
| Exact-claimed (expected skip) | 80 | 149 |
| Unexplained gap | 0 | 0 |
| Status | Clean | Clean |

---

## Distribution Analysis (Gemini 3.5 Flash)

### 1. Distribution Interpretation

The score distribution for Warehouse 827 is highly concentrated and left-skewed, with a very tight spread. 
* **Peak Bin**: The distribution peaks sharply in the `[89, 90)` bin with 527 rows, closely followed by the `[88, 89)` bin with 512 rows.
* **Central Tendency**: The mean score is **87.87** and the median is **88.18**, showing that the bulk of the data sits in the high-80s.
* **Spread**: The standard deviation is extremely narrow at **2.475**, indicating that almost all fuzzy matches are clustered within a small scoring band.

---

### 2. Post-Identification Signals

* **Threshold Sensitivity**: The peak bin `[89, 90)` (527 rows) sits immediately below the **90.0** cutoff for the Matching (High) band. Because 1,039 rows (36.1% of all rows) fall between 88.0 and 90.0, even a minor 1-to-2 point shift in scores will cause massive operational swings, moving hundreds of records between the "Potential (Medium)" and "Matching (High)" bands.
* **Tail Quality**: The tail in the Potential (Low) range (`70–84.999`) is thin, containing only 337 rows (11.7% of total rows), with almost no volume below 81. This thin tail indicates high-quality, clean input data with very few low-scoring edge cases.
* **Score Clustering**: There is heavy mass concentration in the `85–91` range, with 1,984 rows in the Potential (Medium) band. This clustering is expected due to the weighted precision formula $\frac{4 \times \text{address} + 3 \times \text{name}}{7}$. Operationally, this concentration means that the vast majority of AI-inferred matches are grouped tightly together just below the auto-match threshold.
* **Review Workload**: The human review queue—consisting of all Potential and Manual Review rows scoring `70–89.999`—stands at **2,321 rows**, representing **80.76%** of the total dataset. This represents a significant operational workload.

---

### 3. Lifecycle Split

To ensure accurate reporting, the **Closed-Match** category (totaling 553 rows) must be split into its deterministic and AI-inferred components:
* **Closed-Match (Deterministic Exact)**: **149 rows** (Scored exactly 100; rule-proven and authoritative).
* **Closed-Match (Matching High Fuzzy)**: **404 rows** (Scored 90–99.999; high-confidence AI-inferred).

The remaining **2,321 rows** are in the **Potential** lifecycle state and require manual review or business confirmation.

---

### 4. Data Integrity

The data integrity status is **clean** with **0 flags** raised. 
* The exact claimed leads (80) and exact claimed POS (149) match the actual unembedded leads (80) and POS (149) perfectly.
* Lead and POS gaps are both at 0, indicating complete alignment between the deterministic engine and the embedding pipeline.

---

### 5. Recommended Actions

* **Calibrate Thresholds**: Calibrate the scoring thresholds against a human-labeled validation set to optimize the boundary between Potential (Medium) and Matching (High), mitigating the high operational sensitivity around the 90.0 cutoff.
* **Audit Embedding Pipeline**: Periodically audit the embedding pipeline for flagged gaps to ensure ongoing data integrity, even though the current run is clean.
