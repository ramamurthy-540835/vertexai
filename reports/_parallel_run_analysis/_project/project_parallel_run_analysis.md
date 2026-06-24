# Project Parallel Run Analysis

**Generated:** 2026-06-24T05:55:43.970651+00:00

---

## Warehouse Comparison

| Warehouse | Leads | POS | Exact | Fuzzy | Manual Review | Matching High | Potential Med | Potential Low | Peak Bin | Mean Score | Review Queue % | Data Integrity |
|-----------|-------|-----|-------|-------|---------------|---------------|--------------|---------------|----------|------------|----------------|----------------|
| 115 | 630 | 18,900 | 285 | 6,799 | 1,712 | 3,728 | 4,755 | 28 | [88, 89) | 90.454 | 54.38% | Clean |
| 827 | 250 | 7,500 | 149 | 2,357 | 368 | 404 | 1,984 | 337 | [89, 90) | 87.874 | 80.76% | Clean |
| 947 | 310 | 8,100 | 159 | 2,888 | 416 | 551 | 2,439 | 314 | [89, 90) | 88.093 | 79.5% | Clean |

## Lifecycle Split by Warehouse

| Warehouse | Closed-Match Total | Exact (proven) | Matching-High Fuzzy (AI) | Potential |
|-----------|-------------------|----------------|--------------------------|-----------|
| 115 | 4,013 | 285 | 3,728 | 4,783 |
| 827 | 553 | 149 | 404 | 2,321 |
| 947 | 710 | 159 | 551 | 2,753 |

## Fleet Summary

| Metric | Value |
|--------|-------|
| Total warehouses analyzed | 3 |
| Total leads | 1,190 |
| Total POS transactions | 34,500 |
| Total match rows | 15,133 |
| Total Exact (proven) | 593 |
| Total AI-fuzzy (Fuzzy + Manual Review) | 14,540 |
| Exact as % of matches | 3.9% |
| Total primary transactions | 837 |

---

## Cross-Warehouse Comparative Analysis (Gemini 3.5 Flash)

### 1. Distribution Comparison
* **Peaks and Means**: The distributions do not sit in the same place. Warehouse 115 is shifted higher, with a mean above the 90-point threshold and a wider standard deviation. In contrast, Warehouses 827 and 947 are highly similar to each other, with lower means (~88) and tighter standard deviations.
* **Shapes and Skewness**: All three warehouses exhibit left-skewed distributions with scores capped near the fuzzy ceiling. However, their clustering behavior differs. Warehouse 115 has a flatter, more distributed spread above 90, whereas 827 and 947 exhibit tight clustering just below the High Matching threshold, peaking in the `[89, 90)` bin. Warehouse 115 also has virtually no representation in the Low Potential band (`70–84`), while 827 and 947 have notable tails in this range.

### 2. Review Workload Variation
The review-queue percentage varies dramatically across the fleet:
* **Warehouse 115** has a moderate operational burden, with just over half of its volume (~54%) falling into the Potential bands requiring manual review.
* **Warehouses 827 and 947** face an extremely high operational burden, with approximately 80% and 81% of their respective volumes trapped in the manual review queue. 

Warehouses 827 and 947 carry the highest operational burden by a wide margin.

### 3. Threshold Portability
The current global 90/85/70 bands do not yield uniform operational results across the warehouses:
* For **Warehouse 115**, the 90 threshold successfully auto-promotes a large portion of matches to the High Matching band, keeping the review queue manageable.
* For **Warehouses 827 and 947**, the 90 threshold acts as a bottleneck. Because their scores cluster tightly in the `[89, 90)` bin (just below the High Matching cutoff), the fixed bands funnel the vast majority of their transactions into the Potential Medium band, causing severe review-queue bloat. 

Applying these bands globally without adjustment creates highly unequal operational overhead.

### 4. Conclusion
**Recommendation**: We recommend calibrating thresholds against a human-labeled validation set.

**Why**: While the scoring formula and band boundaries are fixed, the resulting operational impact is highly divergent. Warehouses 827 and 947 suffer from severe review-queue bottlenecks due to score clustering just below the 90-point threshold. Calibrating these thresholds against a human-labeled validation set is necessary to establish the true precision and recall of the 90 and 85 boundaries for each warehouse, allowing us to optimize the manual review workload safely.
