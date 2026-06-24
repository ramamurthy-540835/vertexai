# Parallel Run Analysis — Warehouse 115

**Run:** `codex-20260623031813-115`
**Generated:** 2026-06-24T05:55:43.970651+00:00

---

## Match Counts

| Metric | Value |
|--------|-------|
| Leads | 630 |
| POS transactions | 18,900 |
| Lead embeddings | 630 |
| POS embeddings | 18,900 |
| Total match rows | 8,796 |
| Exact | 285 |
| Fuzzy | 6,799 |
| Manual Review | 1,712 |
| Primary transactions | 431 |

## Confidence Bands (non-exact)

| Band | Range | Count |
|------|-------|-------|
| Matching High | 90 – 99.999 | 3,728 |
| Potential Medium | 85 – 89.999 | 4,755 |
| Potential Low | 70 – 84.999 | 28 |

## Lifecycle Split

**Closed - Match** (4,013 total): **285 Exact** (proven) + **3,728 Matching High fuzzy** (AI-inferred)
**Potential**: 4,783

## Score Statistics (non-exact)

| Stat | Value |
|------|-------|
| Min | 83.6 |
| Max | 99.67 |
| Mean | 90.454 |
| Median | 89.61 |
| Std Dev | 3.167 |
| Peak bin | [88, 89) (1,646 rows) |

## Review Workload

Rows in review queue (70 – 89.999): **4,783** (54.38% of all match rows)

## Data Integrity Check

| Metric | Leads | POS |
|--------|-------|-----|
| Total records | 630 | 18,900 |
| Embedded | 630 | 18,900 |
| Un-embedded (actual) | 0 | 0 |
| Exact-claimed (expected skip) | 40 | 285 |
| Unexplained gap | 0 | 0 |
| Status | Clean | Clean |

---

## Distribution Analysis (Gemini 3.5 Flash)

### 1. Distribution Interpretation

The match score distribution for Warehouse 115 is unimodal and negatively (left) skewed, with a heavy concentration of scores immediately below the primary classification threshold. 

* **Central Tendency:** The mean score is **90.454** and the median is **89.61**, reflecting a strong concentration of high-similarity candidates.
* **Spread:** The standard deviation is tight at **3.167**, with scores spanning a minimum of **83.6** to a maximum of **99.67**.
* **Peak Bin:** The distribution peaks sharply in the **`[88, 89)`** bin with **1,646 rows**, closely followed by the **`[89, 90)`** bin with **1,539 rows**. Together, these two bins represent **36.2%** of the entire dataset.

---

### 2. Post-Identification Signals

* **Threshold Sensitivity:** The peak of the distribution sits immediately below the **90.0** cutoff for the *Matching (High)* band. Because **3,185 rows** sit within just two points of this threshold (`[88, 90)`), the system is highly sensitive to minor score fluctuations. A tiny shift in embedding similarity would migrate thousands of rows between *Potential (Medium)* and *Matching (High)*, drastically altering automated closure rates.
* **Tail Quality:** The tail in the **70–84.999** range is exceptionally thin, containing only **28 rows** (with zero rows recorded below 83.0). This indicates a highly clean dataset with virtually no low-scoring, noisy matches passing through the recall gate.
* **Score Clustering:** Mass concentrates heavily between **87.0 and 91.0** (accounting for **5,312 rows**, or **60.4%** of the total). This clustering is expected under the weighted precision formula—$\frac{4 \times \text{address} + 3 \times \text{name}}{7}$—which mathematically pulls final scores toward central values when one attribute scores high and the other is moderate. Operationally, this places the vast majority of candidates directly around the decision boundary.
* **Review Workload:** The human review queue—comprising all *Potential* and *Manual Review* rows (scores 70–89.999)—totals **4,783 rows** (**54.38%** of all matched rows). This represents a substantial operational workload requiring manual business or ServiceNow confirmation.

---

### 3. Lifecycle Split

To maintain reporting integrity, the **Closed-Match** state must be split between deterministic matches and high-confidence AI matches:

* **Closed-Match Total: 4,013 rows (45.62%)**
  * *Deterministic Exact / Complete (Score 100):* **285 rows**
  * *Matching High Fuzzy (Score 90–99.999):* **3,728 rows**
* **Potential Total (Review Queue): 4,783 rows (54.38%)**
  * *Potential Medium (Score 85–89.999):* **4,755 rows**
  * *Potential Low (Score 70–84.999):* **28 rows**

---

### 4. Data Integrity

The dataset status is **clean** with no active data integrity flags:
* **Lead & POS Gaps:** Both are **0**, indicating perfect alignment between expected and actual unembedded records.
* **Unembedded Records:** There are **0** actual unembedded leads and POS records, matching the expected counts of 40 and 285 respectively. 
* The analysis is based on fully complete and reliable pipeline data.

---

### 5. Recommended Actions

* **Calibrate Thresholds Against a Labeled Validation Set:** Conduct a human-in-the-loop audit on a representative sample of the **`[88, 90)`** score range. Calibrating the threshold boundaries against a gold-standard validation set will help determine if the high-density *Potential (Medium)* rows can be safely promoted, thereby reducing the **54.38%** manual review workload.
* **Audit Embedding Pipeline for Flagged Gaps:** Perform routine maintenance audits on the upstream embedding pipeline to ensure that the zero-gap status is maintained as new lead and POS records are ingested.
