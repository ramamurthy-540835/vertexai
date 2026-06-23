# Fuzzy Match Business Report

Use `scripts/generate_fuzzy_match_business_report.py` to generate the stakeholder-facing Costco fuzzy/exact match report from validated run artifacts.

Deterministic:

```bash
python3 scripts/generate_fuzzy_match_business_report.py \
  --report-dir reports/lead_match/ctoteam/115/codex-20260623031813-115 \
  --cleanup-old-reports
```

Gemini:

```bash
REPORT_LLM_PROVIDER=gemini \
REPORT_REASONING_MODEL=gemini-3.5-flash \
VERTEX_PROJECT_ID=ctoteam \
VERTEX_LOCATION=us-central1 \
python3 scripts/generate_fuzzy_match_business_report.py \
  --report-dir reports/lead_match/ctoteam/115/codex-20260623031813-115 \
  --cleanup-old-reports
```

xAI / Grok:

```bash
REPORT_LLM_PROVIDER=xai \
REPORT_REASONING_MODEL=grok-4.3 \
VERTEX_PROJECT_ID=ctoteam \
VERTEX_LOCATION=us-central1 \
python3 scripts/generate_fuzzy_match_business_report.py \
  --report-dir reports/lead_match/ctoteam/115/codex-20260623031813-115 \
  --cleanup-old-reports
```

The old filename `scripts/generate_narayan_fuzzy_report.py` remains available as a compatibility wrapper.
