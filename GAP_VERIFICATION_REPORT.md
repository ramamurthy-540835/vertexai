# Schema Gap Verification Report
## Cloud SQL leadmgmt vs. Earlier Identified Issues

**Verification Date:** 2026-06-23 14:30 UTC  
**Status:** 4 ISSUES RESOLVED, 4 ISSUES REMAIN UNFIXED

---

## Summary Grid

| Gap # | Issue | Status | Severity | Action Required |
|-------|-------|--------|----------|-----------------|
| 1 | Missing Foreign Keys (business tables) | ❌ UNFIXED | 🔴 HIGH | Add 5 FK constraints |
| 2 | Nullable key columns | ❌ UNFIXED | 🔴 HIGH | Make 3 columns NOT NULL |
| 3 | warehouse_number type mismatch | ❌ UNFIXED | 🟡 MEDIUM | Standardize to INTEGER |
| 4 | match_configuration dead table | ❌ UNFIXED | 🟡 MEDIUM | Delete or populate |
| 5 | pos_transactions vs transaction duplicate | ❌ UNFIXED | 🟡 MEDIUM | Choose source of truth |
| 6 | lead_id varchar(20) truncation risk | ❌ UNFIXED | 🔴 HIGH | Expand to varchar(30+) |
| 7 | Runtime columns | ✅ FIXED | ✅ | All present |
| 8 | Updated_by in lead table | ✅ FIXED | ✅ | Present in schema |

---

## Detailed Findings

### ❌ GAP 1: Missing Foreign Keys
**Status:** UNFIXED (Only match_decision_detail has FKs)

**What was expected:**
```
✓ lead.account_id → account.account_id
✓ contact.lead_id → lead.lead_id
✓ transaction.lead_id → lead.lead_id
✓ leads_embeddings.lead_id → lead.lead_id
✓ pos_embeddings.pos_id → transaction.pos_id
```

**What exists:**
```
✓ match_decision_detail.lead_id → lead.lead_id (EXISTS)
✓ match_decision_detail.pos_id → transaction.pos_id (EXISTS)
✗ lead.account_id → account.account_id (MISSING)
✗ contact.lead_id → lead.lead_id (MISSING)
✗ transaction.lead_id → lead.lead_id (MISSING)
✗ leads_embeddings.lead_id → lead.lead_id (MISSING)
✗ pos_embeddings.pos_id → transaction.pos_id (MISSING)
```

**Impact:** 
- Orphan leads possible (point to deleted accounts)
- Orphan contacts possible (point to deleted leads)
- Writeback can set invalid lead_id in transactions
- Stale embeddings survive after lead/POS deletion

**Fix Required:**
```sql
ALTER TABLE leadmgmt.lead 
ADD CONSTRAINT fk_lead_account 
FOREIGN KEY (account_id) REFERENCES leadmgmt.account(account_id);

ALTER TABLE leadmgmt.contact 
ADD CONSTRAINT fk_contact_lead 
FOREIGN KEY (lead_id) REFERENCES leadmgmt.lead(lead_id);

ALTER TABLE leadmgmt.transaction 
ADD CONSTRAINT fk_transaction_lead 
FOREIGN KEY (lead_id) REFERENCES leadmgmt.lead(lead_id);

ALTER TABLE leadmgmt.leads_embeddings 
ADD CONSTRAINT fk_leads_embeddings_lead 
FOREIGN KEY (lead_id) REFERENCES leadmgmt.lead(lead_id);

ALTER TABLE leadmgmt.pos_embeddings 
ADD CONSTRAINT fk_pos_embeddings_pos 
FOREIGN KEY (pos_id) REFERENCES leadmgmt.transaction(pos_id);
```

---

### ❌ GAP 2: Nullable Key Columns
**Status:** UNFIXED (3 columns remain nullable)

**Current State:**
```
❌ leads_embeddings.lead_id: NULLABLE (should be NOT NULL - it's the unique index key)
❌ pos_embeddings.pos_id: NULLABLE (should be NOT NULL - it's the unique index key)
❌ contact.lead_id: NULLABLE (should be NOT NULL - contact without lead is useless)
```

**Impact:**
- leads_embeddings can have NULL lead_id (breaks HNSW queries)
- pos_embeddings can have NULL pos_id (breaks HNSW queries)
- contact can exist without a lead (orphan rows)

**Fix Required:**
```sql
ALTER TABLE leadmgmt.leads_embeddings 
ALTER COLUMN lead_id SET NOT NULL;

ALTER TABLE leadmgmt.pos_embeddings 
ALTER COLUMN pos_id SET NOT NULL;

ALTER TABLE leadmgmt.contact 
ALTER COLUMN lead_id SET NOT NULL;
```

---

### ⚠️ GAP 3: warehouse_number Type Mismatch
**Status:** UNFIXED (int vs bigint)

**Current State:**
```
✅ lead.warehouse_number: INTEGER
✅ leads_embeddings.warehouse_number: INTEGER
✅ pos_embeddings.warehouse_number: INTEGER
❌ transaction.warehouse_number: BIGINT
❌ pos_transactions.warehouse_number: BIGINT
```

**Impact:** Implicit type casting during joins (minor performance overhead, but works)

**Recommendation:**
```sql
-- Either standardize ALL to BIGINT (future-proof):
ALTER TABLE leadmgmt.lead 
ALTER COLUMN warehouse_number TYPE BIGINT;

ALTER TABLE leadmgmt.leads_embeddings 
ALTER COLUMN warehouse_number TYPE BIGINT;

ALTER TABLE leadmgmt.pos_embeddings 
ALTER COLUMN warehouse_number TYPE BIGINT;

-- OR revert transaction/pos_transactions to INTEGER:
ALTER TABLE leadmgmt.transaction 
ALTER COLUMN warehouse_number TYPE INTEGER;

ALTER TABLE leadmgmt.pos_transactions 
ALTER COLUMN warehouse_number TYPE INTEGER;
```

Current warehouses fit in INT32, but BIGINT is safer for future scale.

---

### ❌ GAP 4: match_configuration Dead Table
**Status:** UNFIXED (Table is EMPTY)

**Current State:**
```
❌ match_configuration: 0 rows (EMPTY)
```

**Finding:** Runtime reads confidence bands from `lead_to_pos_match_rules.json`, NOT from this table.

**Decision Needed:** 
- Keep as legacy (no harm, just unused), OR
- Populate it as backup configuration, OR
- Drop it

**For Warehouse 569:** Not blocking. Matching will work via rules.json.

---

### ⚠️ GAP 5: pos_transactions vs transaction Duplicate Tables
**Status:** UNFIXED (Both populated with identical data)

**Current State:**
```
pos_transactions: 26,900 rows
transaction: 26,900 rows (IDENTICAL ROW COUNT)
```

**Finding:** Both tables have same data. pos_transactions appears to be a legacy raw staging table.

**Differences:**
- **pos_transactions:** 32 columns (no OMS fields)
- **transaction:** 63 columns (includes 18 OMS audit fields)

**Decision Needed:**
- Is pos_transactions the raw ingest staging table? (Appears yes)
- Can we drop pos_transactions and use transaction as single source? (Recommended)

**For Warehouse 569:** Not blocking. Runtime uses `transaction` table. pos_transactions is unused by matching pipeline.

---

### 🔴 GAP 6: lead_id Truncation Risk
**Status:** UNFIXED (2 tables use varchar(20) which is too narrow)

**Current State:**
```
Warehouse 569 lead_id format: LEAD-20260523-00001 (21 characters)

❌ lead.lead_id: varchar(20) — TRUNCATION RISK
❌ match_decision_detail.lead_id: varchar(20) — TRUNCATION RISK
✅ contact.lead_id: varchar (unlimited)
✅ leads_embeddings.lead_id: varchar (unlimited)
✅ pos_transactions.lead_id: varchar (unlimited)
✅ transaction.lead_id: varchar (unlimited)
```

**Impact:** Lead IDs will be silently truncated to 20 chars, causing match failures.

**Fix Required:**
```sql
ALTER TABLE leadmgmt.lead 
ALTER COLUMN lead_id TYPE varchar(30);

ALTER TABLE leadmgmt.match_decision_detail 
ALTER COLUMN lead_id TYPE varchar(30);
```

---

### ✅ GAP 7: Runtime Columns (FIXED)
**Status:** FIXED - All present

```
✅ transaction.is_processed: boolean NOT NULL — writeback sets true
✅ transaction.process_datetime: timestamp without time zone — writeback sets timestamp
✅ transaction.matching_comments: text — writeback writes run context
✅ lead.updated_by: varchar — present in schema
```

No issues here.

---

### ✅ GAP 8: lead.updated_by (FIXED)
**Status:** FIXED - Column exists

```
✅ lead.updated_by: character varying (nullable)
```

Was reported as "missing from DDL" but exists in actual Cloud SQL schema.

---

## Critical Path for Warehouse 569

### Blocking Issues (Must fix before running matching):
1. **lead_id truncation** (GAP 6) — Will cause ALL matches to fail
   - Fix time: 5 minutes
   - SQL: ALTER TABLE lead ALTER COLUMN lead_id TYPE varchar(30)

### Strongly Recommended (Do before warehouse 569):
2. **Foreign Keys** (GAP 1) — Data integrity
   - Fix time: 15 minutes
   - SQL: Add 5 FK constraints

3. **Nullable key columns** (GAP 2) — HNSW query reliability
   - Fix time: 10 minutes
   - SQL: Make 3 columns NOT NULL

### Non-Blocking (Can defer):
4. warehouse_number type mismatch (GAP 3)
   - No functional impact; can standardize later
5. match_configuration dead table (GAP 4)
   - Rules.json takes precedence; table unused
6. pos_transactions duplicate (GAP 5)
   - Runtime doesn't use it; can deprecate later

---

## Recommended Action Plan

### Phase 1: Critical (Today - before warehouse 569 run)
```sql
-- Fix lead_id truncation risk (BLOCKING)
ALTER TABLE leadmgmt.lead 
ALTER COLUMN lead_id TYPE varchar(30);

ALTER TABLE leadmgmt.match_decision_detail 
ALTER COLUMN lead_id TYPE varchar(30);
```

### Phase 2: High Priority (Before production rollout)
```sql
-- Add foreign key constraints
ALTER TABLE leadmgmt.lead 
ADD CONSTRAINT fk_lead_account 
FOREIGN KEY (account_id) REFERENCES leadmgmt.account(account_id);

ALTER TABLE leadmgmt.contact 
ADD CONSTRAINT fk_contact_lead 
FOREIGN KEY (lead_id) REFERENCES leadmgmt.lead(lead_id);

ALTER TABLE leadmgmt.transaction 
ADD CONSTRAINT fk_transaction_lead 
FOREIGN KEY (lead_id) REFERENCES leadmgmt.lead(lead_id);

ALTER TABLE leadmgmt.leads_embeddings 
ADD CONSTRAINT fk_leads_embeddings_lead 
FOREIGN KEY (lead_id) REFERENCES leadmgmt.lead(lead_id);

ALTER TABLE leadmgmt.pos_embeddings 
ADD CONSTRAINT fk_pos_embeddings_pos 
FOREIGN KEY (pos_id) REFERENCES leadmgmt.transaction(pos_id);

-- Make key columns NOT NULL
ALTER TABLE leadmgmt.leads_embeddings 
ALTER COLUMN lead_id SET NOT NULL;

ALTER TABLE leadmgmt.pos_embeddings 
ALTER COLUMN pos_id SET NOT NULL;

ALTER TABLE leadmgmt.contact 
ALTER COLUMN lead_id SET NOT NULL;
```

### Phase 3: Cleanup (Future sprint)
- Standardize warehouse_number to BIGINT across all tables
- Either populate or drop match_configuration
- Decide on pos_transactions deprecation

---

## Bottom Line for Warehouse 569

**Can you proceed?** YES, IF you fix GAP 6 (lead_id truncation) first.

**Estimated Fix Time:** 5 minutes  
**Data Impact:** None (just column width change)  
**Risk:** Low

Run:
```sql
ALTER TABLE leadmgmt.lead ALTER COLUMN lead_id TYPE varchar(30);
ALTER TABLE leadmgmt.match_decision_detail ALTER COLUMN lead_id TYPE varchar(30);
```

Then proceed with embed-leads → embed-pos → fuzzy-match for warehouse 569.

The other gaps don't block matching but should be fixed before production rollout to all warehouses.

