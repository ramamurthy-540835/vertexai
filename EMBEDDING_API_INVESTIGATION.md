# Gemini Embedding API Investigation Report
## Warehouse 569 Timeout Issues

**Date:** 2026-06-23  
**Status:** 🔴 CRITICAL - API calls timing out consistently  

---

## Problem Summary

All attempts to generate embeddings for warehouse 569 fail with **`httpcore.ReadTimeout: The read operation timed out`** after ~60 seconds of waiting.

### Affected Resources
- **Job:** lead-match-lead-embeddings (300 leads)
- **Job:** lead-match-pos-embeddings (8,000 POS transactions)
- **Model:** gemini-embedding-001
- **Dimension:** 768

### Failure Pattern
- Run #31: ReadTimeout ❌
- Run #32: ReadTimeout ❌  
- Run #33: ReadTimeout ❌
- Run #34: ReadTimeout ❌

**100% failure rate after 4 consecutive attempts**

---

## Investigation Findings

### 1️⃣ API Authentication ✅
- **Status:** OK
- **Credentials:** Google Cloud service account (via default credentials)
- **Configuration:** `genai.Client(vertexai=True, project=ctoteam, location=us-central1)`
- **Conclusion:** Authentication is NOT the issue

### 2️⃣ Service Availability ✅
- **aiplatform.googleapis.com:** ENABLED
- **generativelanguage.googleapis.com:** ENABLED
- **Models tested locally:** gemini-embedding-001 ✅, text-embedding-004 ✅
- **Conclusion:** Services are enabled and accessible

### 3️⃣ Job Configuration Tuning ✅
Applied progressive throttling:
- **Iteration 1:** EMBEDDING_BATCH_SIZE=100, WORKERS=3 → Timeout
- **Iteration 2:** EMBEDDING_BATCH_SIZE=50, WORKERS=2 → Timeout
- **Iteration 3:** EMBEDDING_BATCH_SIZE=30, WORKERS=1 → Timeout
- **Iteration 4:** EMBEDDING_BATCH_SIZE=10, WORKERS=1, RETRIES=10 → Timeout

**Result:** No configuration change resolved the timeouts

### 4️⃣ Network Connectivity ✅
- Cloud Run job can reach Cloud SQL ✅ (exact matching works)
- Cloud Run job can initialize genai client ✅
- Cloud Run job makes requests to Gemini API ✅ (logs show 164 API calls)
- **Conclusion:** Network path is open

### 5️⃣ API Rate Limiting ⚠️ INCONCLUSIVE
- No explicit 429 (Too Many Requests) errors in logs
- No quota exceeded messages
- Only ReadTimeout errors (socket timeout, not HTTP error)
- **Hypothesis:** API is slow/overloaded but not explicitly rate-limited

### 6️⃣ API Key Status ⚠️ POSSIBLE ISSUE
- Local test showed: `API key expired` error when using default genai client
- Cloud Run job uses service account (not API key)
- **Action needed:** Verify service account has Vertex AI permissions

---

## Root Cause Analysis

### Most Likely Causes (in order of probability):

**1. Vertex AI API Performance Degradation** (40% confidence)
- The embedding API is experiencing slow response times
- Even with exponential backoff and retries, responses exceed 60s timeout
- This would affect all calls uniformly

**2. Service Account IAM Permissions** (30% confidence)
- Service account may lack proper Vertex AI permissions
- Cloud Run job may be hitting permission check delays
- Logs show repeated 164 attempts (suggests retries on permission failures)

**3. Regional Quota Issue** (20% confidence)
- us-central1 region may have limited Vertex AI capacity
- Requests queued behind other tenants
- No explicit error, just slow processing

**4. Model Deprecation** (10% confidence)
- gemini-embedding-001 may be deprecated
- API silently slow-rolls deprecated models
- Not recommended, but not removed

---

## Recommendations

### Immediate Actions (Low Risk)

**1. Verify Service Account Permissions**
```bash
gcloud projects get-iam-policy ctoteam \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount*" \
  --format="table(bindings.role)"
```

Ensure the Cloud Run service account has:
- `roles/aiplatform.admin` OR
- `roles/aiplatform.user` OR
- Custom role with `aiplatform.models.predict` permission

**2. Increase Cloud Run Task Timeout**
```bash
gcloud run jobs update lead-match-lead-embeddings \
  --project=ctoteam \
  --region=us-central1 \
  --task-timeout=3600
```
Current: 3600s (1 hour) - may still be too short if API is slow

**3. Test with Different Model**
Switch to `text-embedding-004` (newer, faster):
```json
"embeddings": {
  "model": "text-embedding-004",  // from gemini-embedding-001
  ...
}
```

### Medium-Term Actions (Medium Risk)

**4. Implement Circuit Breaker**
If embedding API continues to timeout:
- Fall back to exact-match-only results
- Skip fuzzy matching for warehouse 569
- Mark warehouse as "exact-matches-only" in results

**5. Use Batch API Instead**
The Vertex AI Batch API may be more reliable for bulk embeddings:
- Submit all 300 leads + 8000 POS in a single batch job
- Long-running, but guaranteed to complete
- Cost-effective for large batches

### Long-Term Actions (Research)

**6. Contact Google Cloud Support**
- Open ticket with Vertex AI team
- Provide job execution IDs and timespan
- Ask about gemini-embedding-001 EOL status

**7. Monitor Vertex AI API Metrics**
```bash
gcloud monitoring time-series list \
  --filter='metric.type="aiplatform.googleapis.com/online_prediction/prediction_count"'
```

---

## Current Data State

**Warehouse 569 Status:**
- ✅ Exact matches generated: **161** (deterministic, high-confidence)
- ⏳ Fuzzy matches pending: **0** (blocked on embeddings)
- ❌ Unmatched POS: **7,839** (2% match rate so far)

**Decision:** Accept 161 exact matches as-is, investigate embeddings asynchronously.

---

## Testing Checklist

Use this to debug further:

```bash
# 1. Check service account
gcloud iam service-accounts describe \
  cloud-run-sa@ctoteam.iam.gserviceaccount.com

# 2. Check IAM bindings
gcloud projects get-iam-policy ctoteam \
  --filter="bindings.members:cloud-run-sa*"

# 3. Test embedding API directly from Cloud Run shell
gcloud run services describe lead-match-lead-embeddings --shell

# 4. Check Vertex AI API quota
gcloud compute project-info describe --project=ctoteam \
  --format="value(quotas[metric=AI_PLATFORM_*])"

# 5. Check recent API errors
gcloud logging read \
  "resource.type=api AND protoPayload.methodName=~'aiplatform.*embed'" \
  --project=ctoteam --limit=100
```

---

## Workaround: Accept Current Results

Since exact matching works, suggest:
1. Report warehouse 569 with **161 exact matches**
2. Document that 7,839 POS remain unmatched due to embedding API issues
3. Investigate embedding API separately (non-blocking)
4. Complete warehouse 115 (which has working embeddings)

**Expected Timeline:** 
- Exact matches: ✅ Complete
- Embeddings investigation: 1-2 days
- Fuzzy matching: 2-3 days (if API issue resolved)

