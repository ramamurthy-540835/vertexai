# Testing Quick Start

## Step 1: Generate Mock Data (with randomization)

```bash
cd /home/appadmin/projects/gcp-vertexai/vertexai-dev

# Generate for warehouse 115 with default seed
python3 mock_data/generate_mock_data.py --warehouse 115

# Or with custom seed for reproducibility
python3 mock_data/generate_mock_data.py --warehouse 115 --seed 42

# Verify output files
ls -lh 115/leads_corrected.xlsx 115/pos_corrected.xlsx
```

## Step 2: Check Match Type Distribution

```bash
# Install pandas if needed
pip install pandas openpyxl

# Quick check of match_type values
python3 << 'EOF'
import pandas as pd
leads = pd.read_excel('115/leads_corrected.xlsx')
print("Lead match_type distribution:")
print(leads['match_type'].value_counts(dropna=False))
print(f"\nTotal leads: {len(leads)}")

pos = pd.read_excel('115/pos_corrected.xlsx')
print("\nPOS match_type distribution:")
print(pos['match_type'].value_counts(dropna=False))
print(f"Total POS rows: {len(pos)}")
EOF
```

## Step 3: Verify Randomization (Run Multiple Times)

```bash
# Generate 3 times with different seeds to see random distribution
for seed in 42 100 200; do
  echo "Seed: $seed"
  python3 mock_data/generate_mock_data.py --warehouse 115 --seed $seed --num-leads 400 > /tmp/gen_$seed.log 2>&1
  python3 << EOF
import pandas as pd
leads = pd.read_excel('115/leads_corrected.xlsx')
exact = (leads['match_type'] == 'Exact').sum()
fuzzy = (leads['match_type'] == '').sum() if any(leads['match_type'].isna()) else 0
print(f"  Exact: {exact}, Fuzzy/Unmatched: {len(leads) - exact}")
EOF
done
```

## Step 4: Load to Cloud SQL (if database available)

```bash
# Set up environment
export DB_HOST=your-cloudsql-ip
export DB_PORT=5432
export DB_NAME=leadmgmt
export DB_USER=postgres
export DB_PASSWORD=your-password

# Apply schema
psql -h $DB_HOST -U $DB_USER -d postgres < schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql

# Load mock data
python3 mock_data/load_mock_data.py --warehouse 115 --input-dir 115
```

## Step 5: Verify in Cloud SQL

```bash
# Check lead table
psql -h $DB_HOST -U $DB_USER -d leadmgmt << 'EOF'
SELECT match_type, COUNT(*) as count 
FROM "leadmgmt"."lead" 
GROUP BY match_type;
EOF

# Check transaction table
psql -h $DB_HOST -U $DB_USER -d leadmgmt << 'EOF'
SELECT match_type, COUNT(*) as count 
FROM "leadmgmt"."transaction" 
GROUP BY match_type;
EOF
```

## Step 6: Run Embedding Jobs

```bash
# In Cloud Console, trigger workflows:
# 1. lead_match_embeddings.yml → lead-embeddings job
# 2. lead_match_embeddings.yml → pos-embeddings job
#
# Verify that records with match_type='Exact' are skipped
# by checking the job logs for the exclusion clause
```

## Expected Results

✅ **match_type column populated** in both leads and transactions  
✅ **Random counts each run** - exact/fuzzy percentages vary ±5%  
✅ **Exact matches skipped** from embedding jobs  
✅ **Fuzzy job identifies** similarity scores in natural 70-99.99% range  
✅ **Unmatched records** below 70% properly classified  

## Troubleshooting

| Issue | Check |
|-------|-------|
| No match_type column | Run schema first: `psql ... < schema/ctoteam_cloudsql_leadmgmt_schema_from_excel.sql` |
| Counts not random | Verify `randomize_scenario_weights()` is called with rng in plan_to_weights() |
| Column mismatch error | Verify load_mock_data.py has 19 fields for lead INSERT |
| Embedding job errors | Check that job_runner.py exclusion clauses reference lead/transaction.match_type |

---

**For detailed implementation info**: See `IMPLEMENTATION_CHANGES_SUMMARY.md`
