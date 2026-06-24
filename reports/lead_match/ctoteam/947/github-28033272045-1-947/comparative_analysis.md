### Lead-to-POS Match Scoring Analysis

This report analyzes the match score distribution of 3,463 lead-to-POS records to evaluate match quality, identify system anomalies, and recommend operational next steps.

---

### 1. Distribution Interpretation

```
Score Range   | Row Count (Total: 3,463)
----------------------------------------
70 - 79.99    | 1
80 - 84.99    | 313
85 - 89.99    | 2,439  <-- Bulk of data (Potential Medium)
90 - 99.99    | 392
100           | 159    <-- Exact Match spike
```

* **Shape**: The distribution is highly concentrated and left-skewed, characterized by a dominant, sharp peak in the high-80s, a rapid drop-off in the 90s, and a secondary spike at 100.
* **Central Tendency**: The mean (88.64) and median (88.42) are extremely close, reflecting a tight concentration of scores. The standard deviation is low (3.43), indicating that most scores are compressed into a narrow window between 85 and 92.
* **Peak**: The distribution peaks aggressively at the `[89, 90)` bin with **663 rows (19.15% of the total dataset)**.

---

### 2. Post-Identification Findings

* **Signal 1: High Threshold Sensitivity**  
  The peak bin `[89, 90)` (663 rows) sits directly adjacent to the 90.0 cutoff. Because of this compression, small score shifts will have massive operational impacts: shifting the "Matching (High)" threshold from 90 to 89 would immediately reclassify 19.15% of the dataset.
* **Signal 2: Thin Tail/Edge Quality (70 - 84.99)**  
  The tail volume is thin, containing only **314 rows (9.07%)**. The absolute minimum score is 79.64, with virtually no records scoring below 81. This indicates highly pre-filtered, clean input data with almost no completely irrelevant matches.
* **Signal 3: Clear System Artifacts**  
  The `artifact_flag` is **true**. The peak bin `[89, 90)` contains 19.15% of all rows, exceeding the 15% threshold for a single-bin spike. Additionally, there is a pronounced secondary spike at the `[100, 101)` bin (**159 rows**), representing perfect deterministic matches. 
* **Signal 4: Excessive Review Workload**  
  The manual review queue (scores between 70 and 89.99) encompasses **2,753 rows (79.50% of the dataset)**. Under current rules, four out of five matches require human intervention.

---

### 3. Recommended Actions

* **Mitigate Threshold Sensitivity & Address Workload**:  
  With 79.5% of records stuck in manual review, the current threshold of 90 for "Matching (High)" is operationally bottlenecked. We should audit the precision of records in the `[88, 90)` range. If precision is high, lowering the auto-match threshold to 88 would safely promote **1,261 rows (36.4% of the dataset)** to auto-match, cutting the manual review workload nearly in half.
* **Auto-Reject the Thin Tail**:  
  Because the tail below 85 is small (9.07%), we can implement an auto-reject cutoff at **< 83.0** to eliminate low-value manual reviews. This safely discards the lowest-performing ~120 records without risking high-quality matches.
* **Investigate the 89-90 Artifact**:  
  Investigate the matching logic to find out why nearly 20% of the dataset is scoring exactly between 89 and 90. This usually indicates a heavily weighted partial-match rule (e.g., "Exact Phone Match but Missing Address") that acts as a hard scoring ceiling. 

---

### 4. Caveat

These bands and thresholds represent **starting statistical priors** based on the shape of this specific dataset. Final production thresholds must be validated and calibrated against a representative, human-labeled dataset to ensure acceptable precision and recall.