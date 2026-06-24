# Parallel Run Analysis — Warehouse 947

**Run:** `github-28033272045-1-947`
**Generated:** 2026-06-24T05:55:43.970651+00:00

---

## Match Counts

| Metric | Value |
|--------|-------|
| Leads | 310 |
| POS transactions | 8,100 |
| Lead embeddings | 216 |
| POS embeddings | 7,941 |
| Total match rows | 3,463 |
| Exact | 159 |
| Fuzzy | 2,888 |
| Manual Review | 416 |
| Primary transactions | 222 |

## Confidence Bands (non-exact)

| Band | Range | Count |
|------|-------|-------|
| Matching High | 90 – 99.999 | 551 |
| Potential Medium | 85 – 89.999 | 2,439 |
| Potential Low | 70 – 84.999 | 314 |

## Lifecycle Split

**Closed - Match** (710 total): **159 Exact** (proven) + **551 Matching High fuzzy** (AI-inferred)
**Potential**: 2,753

## Score Statistics (non-exact)

| Stat | Value |
|------|-------|
| Min | 79.64 |
| Max | 99.79 |
| Mean | 88.093 |
| Median | 88.29 |
| Std Dev | 2.416 |
| Peak bin | [89, 90) (663 rows) |

## Review Workload

Rows in review queue (70 – 89.999): **2,753** (79.5% of all match rows)

## Data Integrity Check

| Metric | Leads | POS |
|--------|-------|-----|
| Total records | 310 | 8,100 |
| Embedded | 216 | 7,941 |
| Un-embedded (actual) | 94 | 159 |
| Exact-claimed (expected skip) | 94 | 159 |
| Unexplained gap | 0 | 0 |
| Status | Clean | Clean |

---

## Distribution Analysis (Gemini 3.5 Flash)

### 1. Distribution Interpretation

The score distribution for Warehouse 947 is highly concentrated and negatively skewed, with a single prominent peak. 
* **Peak and Central Tendency**: The peak of the distribution sits in the `[89, 90)` bin, containing 663 rows. The central tendency is tightly bound, with a **mean of 88.093** and a **median of 88.29**.
* **Spread**: The standard deviation is exceptionally narrow at **2.416**, indicating that the vast majority of fuzzy matches are clustered within a tight scoring band. The minimum score observed is **79.64** (well above the floor of 70), and the maximum fuzzy score is **99.79**.

---

### 2. Post-Identification Signals

* **Threshold Sensitivity**: The distribution is highly sensitive around the 90.0 cutoff. The peak bin `[89, 90)` (663 rows) sits immediately below the Matching (High) boundary. A minor score shift of less than 1 point would transition hundreds of records between the Potential (Medium) and Matching (High) bands, significantly altering downstream match rates.
* **Tail Quality**: The lower tail (`70–84.999`) is very thin, containing only 314 rows (9.1% of total rows), with zero records scoring below 79.64. This thin tail indicates that the recall gate is successfully filtering out low-quality, noisy candidates before precision scoring.
* **Score Clustering**: There is a heavy concentration of mass just below the high-confidence threshold, with 2,439 rows (70.4%) falling into the `85–89.999` range. This clustering is expected due to the weighted precision formula:
  $$\text{Score} = \frac{4 \times \text{address} + 3 \times \text{name}}{7}$$
  Operationally, this means the vast majority of AI-inferred matches reside in a narrow band of high-similarity but non-identical business identities.
* **Review Workload**: The potential human review queue (comprising Potential Low, Potential Medium, and Manual Review rows scoring `70–89.999`) stands at **2,753 rows**, representing **79.5% of the total workload**. This creates a substantial operational bottleneck for manual verification teams.

---

### 3. Lifecycle Split

To ensure reporting transparency, the match lifecycle must distinguish between rule-proven deterministic matches and high-confidence AI-inferred matches:

* **Closed-Match Total**: **710 rows**
  * *Deterministic Exact / Complete (Score 100)*: **159 rows** (rule-proven, authoritative)
  * *Matching High Fuzzy (Score 90–99.999)*: **551 rows** (high-confidence AI-inferred)
* **Potential Total**: **2,753 rows**
  * *Potential Medium (Score 85–89.999)*: **2,439 rows** (awaiting business confirmation)
  * *Potential Low (Score 70–84.999)*: **314 rows** (possible candidates requiring review)

---

### 4. Data Integrity

The data integrity status for Warehouse 947 is **clean** with no active flags. 
* Unembedded leads (94) and unembedded POS records (159) perfectly match the exact claimed counts.
* Both the lead gap and POS gap are **0**, confirming that the deterministic engine has cleanly claimed its matches and passed the correct residual records to the semantic pipeline.

---

### 5. Recommended Actions

* **Calibrate Thresholds**: Calibrate the scoring thresholds against a human-labeled validation set. This will help determine if the high-volume `85–89.999` band contains high-fidelity matches that can be safely promoted, or if the 90.0 threshold is correctly positioned to prevent false positives.
* **Audit Embedding Pipeline**: Periodically audit the embedding pipeline to ensure that the zero-gap integrity status is maintained in future runs.
