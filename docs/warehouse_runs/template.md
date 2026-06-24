# Warehouse <WH>: Match Run Template

This is a template for documenting warehouse-specific match runs, thresholds, and known issues.

## Configuration

- **Warehouse**: <WH>
- **Project**: ctoteam
- **Region**: us-central1
- **Bucket**: lead-match-ctoteam
- **Status**: [NEW | CALIBRATING | STABLE]

## Latest Run

| Property | Value |
|----------|-------|
| Run ID | `github-<run>-<attempt>-<WH>` |
| Generated | YYYY-MM-DD HH:MM UTC |
| Report | gs://lead-match-ctoteam/reports/lead_match/ctoteam/<WH>/... |
| Status | [PENDING | ANALYZING | COMPLETE] |

## Threshold Calibration

**Current Bands**:
- Exact: 100 (deterministic)
- Matching High: 90-99.999 (AI-inferred, auto-match)
- Potential (Medium): 85-89.999 (review queue)
- Potential (Low): 70-84.999 (review queue)
- No Match: 0-69.999 (rejected)

**Calibration Status**: [NOT STARTED | IN PROGRESS | COMPLETE]

**Labeled Validation Set**: [Not yet | <N> samples reviewed]

## Score Distribution Notes

From last comparative_analysis.md:

- **Peak**: Score bin X (Y% of total)
- **Peak Location**: [Within 2 points of cutoff | Well-separated from cutoff]
- **Tail Volume (70-84.999)**: X rows (Y%)
- **Review Workload (70-89.999)**: X rows (Y%)
- **Artifacts**: [None detected | Spike at score X | Empty bin Y]

**Observation**: [e.g., "Peak at 88 suggests 90 cutoff is well-positioned" or "Thin tail indicates clean data; recall gate working well"]

## Known Issues

- [ ] Issue 1: Description and impact
- [ ] Issue 2: Description and impact

## Next Actions

1. [ ] Action 1
2. [ ] Action 2

## Related Warehouses

- See `115.md` for reference calibration
- See `569.md` for alternative threshold approach

## Run History

| Date | Run ID | Matches | Exact | Fuzzy High | Fuzzy Medium | Fuzzy Low | No Match | Notes |
|------|--------|---------|-------|-----------|--------------|----------|----------|-------|
| 2026-06-23 | github-123-1-<WH> | 8,511 | 285 | 1,850 | 2,100 | 1,833 | 2,728 | Initial run |
| | | | | | | | | |

---

**Template last updated**: 2026-06-23
