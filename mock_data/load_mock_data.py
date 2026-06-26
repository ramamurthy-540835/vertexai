import os
import sys
import argparse
import pandas as pd
import numpy as np
import pg8000.dbapi
import uuid
from datetime import datetime, UTC

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_DIR)
from lead_match_runtime.business_rules import load_business_rules, get_schema
_RULES = load_business_rules()
_SCHEMA = get_schema(_RULES)
ENV_FILE = os.path.join(PROJECT_DIR, '.env.local')
ROOT_LEADS_FILE = os.path.join(SCRIPT_DIR, 'leads_corrected.xlsx')
ROOT_POS_FILE = os.path.join(SCRIPT_DIR, 'pos_corrected.xlsx')

COLUMN_RENAMES = {
    "fiscal_year_lead": "fiscal_year",
    "fiscal_period_lead": "fiscal_period",
    "fiscal_year_transaction": "fiscal_year",
    "fiscal_period_transaction": "fiscal_period",
}

def load_env_file(path=ENV_FILE):
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)

def get_db_config():
    load_env_file()
    return {
        'host': os.environ.get('DB_HOST') or os.environ.get('CLOUDSQL_PUBLIC_IP'),
        'port': int(os.environ.get('DB_PORT', '5432')),
        'database': os.environ.get('DB_NAME', 'postgres'),
        'user': os.environ.get('DB_USER', 'postgres'),
        'password': os.environ.get('DB_PASSWORD'),
    }

def resolve_mock_file(filename, warehouse_number=None, input_dir=None):
    candidates = []
    if input_dir:
        candidates.append(os.path.join(input_dir, filename))
    if warehouse_number is not None:
        candidates.append(os.path.join(SCRIPT_DIR, str(warehouse_number), filename))
    candidates.append(os.path.join(SCRIPT_DIR, filename))

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]

def make_id(prefix, run_suffix, idx):
    return f"{prefix}{run_suffix}{idx:08d}"

def normalize_key_value(value):
    if value is None:
        return None
    return str(value).strip().lower()

def account_key_from_values(business_name, address_line_one):
    return (
        normalize_key_value(business_name),
        normalize_key_value(address_line_one),
    )

def lead_key_from_values(lead_source, membership_number, warehouse_number, business_name, address_line_one):
    return (
        normalize_key_value(lead_source),
        normalize_key_value(membership_number),
        normalize_key_value(warehouse_number),
        normalize_key_value(business_name),
        normalize_key_value(address_line_one),
    )

def bulk_insert(cursor, insert_sql, rows, fields_per_row, chunk_size=500):
    if not rows:
        return

    row_placeholder = "(" + ", ".join(["%s"] * fields_per_row) + ")"
    for start in range(0, len(rows), chunk_size):
        chunk = rows[start:start + chunk_size]
        placeholders = ", ".join([row_placeholder] * len(chunk))
        params = [value for row in chunk for value in row]
        cursor.execute(f"{insert_sql} VALUES {placeholders}", params)

def fetch_existing_lead_state(cursor):
    existing_accounts = {}
    existing_leads = set()

    cursor.execute(
        f"""SELECT account_id, business_name, address_line_one
           FROM "{_SCHEMA}"."account";"""
    )
    for account_id, business_name, address_line_one in cursor.fetchall():
        key = account_key_from_values(business_name, address_line_one)
        if key[0]:
            existing_accounts[key] = account_id

    cursor.execute(
        f"""SELECT l.lead_source, l.membership_number, l.warehouse_number,
                  a.business_name, a.address_line_one
           FROM "{_SCHEMA}"."lead" l
           LEFT JOIN "{_SCHEMA}"."account" a ON a.account_id = l.account_id;"""
    )
    for lead_source, membership_number, warehouse_number, business_name, address_line_one in cursor.fetchall():
        existing_leads.add(
            lead_key_from_values(
                lead_source,
                membership_number,
                warehouse_number,
                business_name,
                address_line_one,
            )
        )

    return existing_accounts, existing_leads

def clean_val(val):
    if pd.isna(val) or val is None:
        return None
    if isinstance(val, (float, np.float64, np.float32)):
        if np.isnan(val):
            return None
        # If it's a whole number stored as float, cast to int where appropriate
        if val.is_integer():
            return int(val)
        return float(val)
    if isinstance(val, (int, np.int64, np.int32)):
        return int(val)
    # Convert string dates if needed
    val_str = str(val).strip()
    if val_str.lower() in ['nan', 'none', '']:
        return None
    return val_str

def load_leads(cursor, conn, leads_file, limit=None):
    print(f"\nReading Lead Mock Data: {leads_file}...")
    try:
        df_leads_raw = pd.read_excel(leads_file)
        if limit is not None:
            df_leads_raw = df_leads_raw.head(limit)
        print(f"Loaded {len(df_leads_raw)} lead rows from Excel.")
    except Exception as e:
        print("❌ Failed to read leads file:", e)
        return False

    # Convert all NaN/Null values to None using map (compatible with pandas 3.0)
    df_leads = df_leads_raw.rename(
        columns={k: v for k, v in COLUMN_RENAMES.items() if k in df_leads_raw.columns}
    ).map(clean_val)

    # We will populate 'account', 'lead', and 'contact' tables
    accounts_to_insert = []
    leads_to_insert = []
    contacts_to_insert = []

    # Map to track generated account_id by (business_name, address_line_one) to avoid duplicates
    account_map = {}
    account_idx = 1
    lead_idx = 1
    contact_idx = 1
    
    batch_id = str(uuid.uuid4())
    run_suffix = batch_id.replace('-', '')[:8].upper()
    current_time = datetime.now(UTC)
    existing_accounts, existing_leads = fetch_existing_lead_state(cursor)
    skipped_duplicates = 0

    print("Mapping Lead data into Account, Lead, and Contact tables...")
    for idx, row in df_leads.iterrows():
        biz_name = row.get('business_name')
        addr_one = row.get('address_line_one')
        
        if not biz_name:
            continue

        lead_key = lead_key_from_values(
            row.get('lead_source'),
            row.get('membership_number'),
            row.get('warehouse_number'),
            biz_name,
            addr_one,
        )
        if lead_key in existing_leads:
            skipped_duplicates += 1
            continue
        existing_leads.add(lead_key)

        key = account_key_from_values(biz_name, addr_one)
        if key in existing_accounts:
            act_id = existing_accounts[key]
        elif key not in account_map:
            act_id = make_id("ACCT", run_suffix, account_idx)
            account_map[key] = act_id
            account_idx += 1
            
            # Prepare Account row
            accounts_to_insert.append((
                act_id,                          # account_id
                batch_id,                        # batch_id
                row.get('account_number'),       # account_number
                row.get('type'),                 # type
                biz_name,                        # business_name
                addr_one,                        # address_line_one
                None,                            # address_line_two
                row.get('city'),                 # city
                row.get('state'),                # state
                row.get('zip_code'),             # zip_code
                row.get('phone'),                # phone
                row.get('email'),                # email
                None,                            # industry_code
                row.get('bd_industry'),          # bd_industry
                'mock_loader',                   # updated_by
                current_time                     # updated_date
            ))
        else:
            act_id = account_map[key]

        # Prepare Lead row
        lead_id = make_id("LEAD", run_suffix, lead_idx)
        lead_idx += 1
        leads_to_insert.append((
            lead_id,                            # lead_id
            row.get('lead_source'),             # lead_source
            act_id,                             # account_id
            row.get('account_number'),          # account_number
            'Open',                             # lead_status
            None,                               # confidence_level
            row.get('membership_number'),       # membership_number
            row.get('warehouse_number'),        # warehouse_number
            row.get('fiscal_period'),             # fiscal_period
            row.get('fiscal_year'),              # fiscal_year
            row.get('closed_fiscal_period'),     # closed_fiscal_period
            row.get('closed_fiscal_year'),       # closed_fiscal_year
            batch_id,                           # batch_id
            current_time,                       # load_date
            'mock_loader',                      # updated_by
            current_time,                       # updated_date
            row.get('match_result'),             # match_result
            row.get('week'),                    # week
        ))

        # Prepare Contact row
        contact_id = make_id("CONT", run_suffix, contact_idx)
        contact_idx += 1
        contacts_to_insert.append((
            contact_id,                         # contact_id
            lead_id,                            # lead_id
            None,                               # first_name
            None,                               # last_name
            row.get('email'),                   # email
            row.get('phone'),                   # phone
            row.get('membership_number'),       # membership_number
            None,                               # job_title
            batch_id,                           # batch_id
            'mock_loader',                      # updated_by
            current_time                        # updated_date
        ))

    # Bulk insert leads mock data
    try:
        # Accounts
        print(f"Inserting {len(accounts_to_insert)} accounts...")
        bulk_insert(
            cursor,
            f"""INSERT INTO "{_SCHEMA}"."account" (
                    account_id, batch_id, account_number, type, business_name, address_line_one,
                    address_line_two, city, state, zip_code, phone, email, industry_code,
                    bd_industry, updated_by, updated_date
                )""",
            accounts_to_insert,
            16,
        )
        # Leads
        print(f"Inserting {len(leads_to_insert)} leads...")
        bulk_insert(
            cursor,
            f"""INSERT INTO "{_SCHEMA}"."lead" (
                    lead_id, lead_source, account_id, account_number, lead_status, confidence_level,
                    membership_number, warehouse_number, fiscal_period, fiscal_year,
                    closed_fiscal_period, closed_fiscal_year, batch_id, load_date, updated_by,
                    updated_date, match_result, week
                )""",
            leads_to_insert,
            18,
        )
        # Contacts
        print(f"Inserting {len(contacts_to_insert)} contacts...")
        bulk_insert(
            cursor,
            f"""INSERT INTO "{_SCHEMA}"."contact" (
                    contact_id, lead_id, first_name, last_name, email, phone, membership_number,
                    job_title, batch_id, updated_by, updated_date
                )""",
            contacts_to_insert,
            11,
        )
        conn.commit()
        if skipped_duplicates:
            print(f"Skipped {skipped_duplicates} duplicate lead rows.")
        print("✅ Lead mock data tables loaded successfully!")
        return True
    except Exception as e:
        print("❌ Lead insertion transaction failed:", e)
        conn.rollback()
        return False

def load_pos(cursor, conn, pos_file, limit=None):
    print(f"\nReading POS Mock Data: {pos_file}...")
    try:
        # We read the file in chunks or read completely since memory is adequate
        df_pos_raw = pd.read_excel(pos_file)
        if limit is not None:
            df_pos_raw = df_pos_raw.head(limit)
        print(f"Loaded {len(df_pos_raw)} POS rows from Excel.")
    except Exception as e:
        print("❌ Failed to read POS file:", e)
        return False

    # Convert all NaN/Null values to None using map (compatible with pandas 3.0)
    df_pos = df_pos_raw.rename(
        columns={k: v for k, v in COLUMN_RENAMES.items() if k in df_pos_raw.columns}
    ).map(clean_val)

    pos_tx_to_insert = []
    tx_to_insert = []
    
    pos_idx = 1
    batch_id_pos = str(uuid.uuid4())
    run_suffix = batch_id_pos.replace('-', '')[:8].upper()
    current_time_pos = datetime.now(UTC)

    print("Mapping POS data into pos_transactions and transaction tables...")
    for idx, row in df_pos.iterrows():
        pos_id = make_id("POS", run_suffix, pos_idx)
        pos_idx += 1
        
        # Mapping properties for pos_transactions (32 fields)
        pos_tx_to_insert.append((
            pos_id,                             # pos_id
            row.get('sales_reference_id'),       # sales_reference_id
            row.get('account_number'),          # account_number
            None,                               # lead_id
            None,                               # match_score
            None,                               # match_type
            batch_id_pos,                       # batch_id
            row.get('membership_number'),       # membership_number
            row.get('order_amount'),            # order_amount
            row.get('transaction_count'),       # transaction_count
            row.get('fiscal_period'),           # fiscal_period
            row.get('fiscal_year'),             # fiscal_year
            row.get('week'),                    # week
            row.get('shop_type'),               # shop_type
            row.get('warehouse_number'),        # warehouse_number
            row.get('bd_industry'),              # bd_industry
            row.get('business_name'),           # business_name
            row.get('address_line_one'),        # address_line_one
            row.get('address_line_two'),        # address_line_two
            row.get('city'),                    # city
            row.get('state'),                   # state
            row.get('zip_code'),                # zip_code
            row.get('phone'),                   # phone
            row.get('first_name'),              # first_name
            row.get('last_name'),               # last_name
            row.get('email'),                   # email
            None,                               # sic_code
            row.get('industry_description'),    # sic_description (excel industry_description mapped here)
            None,                               # primary_transaction
            current_time_pos,                   # load_date
            'mock_loader',                      # updated_by
            current_time_pos                    # updated_date
        ))

        # Mapping properties for transaction (63 fields)
        tx_to_insert.append((
            pos_id,                             # pos_id
            row.get('sales_reference_id'),       # sales_reference_id
            row.get('account_number'),          # account_number
            None,                               # lead_id
            None,                               # match_score
            None,                               # match_type
            batch_id_pos,                       # batch_id
            row.get('membership_number'),       # membership_number
            row.get('order_amount'),            # order_amount
            row.get('transaction_count'),       # transaction_count
            row.get('fiscal_period'),           # fiscal_period
            row.get('fiscal_year'),             # fiscal_year
            row.get('week'),                    # week
            row.get('shop_type'),               # shop_type
            row.get('warehouse_number'),        # warehouse_number
            row.get('bd_industry'),              # bd_industry
            row.get('business_name'),           # business_name
            row.get('address_line_one'),        # address_line_one
            row.get('address_line_two'),        # address_line_two
            row.get('city'),                    # city
            row.get('state'),                   # state
            row.get('zip_code'),                # zip_code
            row.get('phone'),                   # phone
            row.get('first_name'),              # first_name
            row.get('last_name'),               # last_name
            row.get('email'),                   # email
            None,                               # sic_code
            row.get('industry_description'),    # industry_description
            current_time_pos,                   # load_date
            'mock_loader',                      # updated_by
            current_time_pos,                   # updated_date
            None,                               # primary_transaction
            row.get('oms_company'),             # oms_company
            row.get('oms_company_2'),           # oms_company_2
            row.get('oms_email_1'),             # oms_email_1
            row.get('oms_email_2'),             # oms_email_2
            row.get('oms_email_3'),             # oms_email_3
            row.get('oms_phone_1'),             # oms_phone_1
            row.get('oms_phone_2'),             # oms_phone_2
            row.get('oms_phone_3'),             # oms_phone_3
            row.get('oms_cell_1'),              # oms_cell_1
            row.get('oms_cell_2'),              # oms_cell_2
            row.get('oms_first_name'),          # oms_first_name
            row.get('oms_middle_name'),         # oms_middle_name
            row.get('oms_last_name'),           # oms_last_name
            row.get('oms_address_line_1'),       # oms_address_line_1
            row.get('oms_city'),                # oms_city
            row.get('oms_state'),               # oms_state
            row.get('oms_zip'),                 # oms_zip
            row.get('oms_address_line_1_v2'),    # oms_address_line_1_v2
            row.get('oms_address_line_2'),       # oms_address_line_2
            row.get('oms_address_line_3'),       # oms_address_line_3
            row.get('oms_address_line_4'),       # oms_address_line_4
            row.get('oms_address_line_5'),       # oms_address_line_5
            row.get('oms_address_line_6'),       # oms_address_line_6
            row.get('oms_city_2'),              # oms_city_2
            row.get('oms_state_2'),             # oms_state_2
            row.get('oms_zip_2'),               # oms_zip_2
            row.get('oms_zip_3'),               # oms_zip_3
            row.get('oms_zip_4'),               # oms_zip_4
            None,                               # matching_comments
            False,                              # is_processed (defined as non-null boolean)
            None                                # process_datetime
        ))

    # Bulk insert POS mock data in chunks to prevent memory/buffer overflows in pg8000
    try:
        # Insert pos_transactions
        print(f"Inserting {len(pos_tx_to_insert)} records into pos_transactions table...")
        bulk_insert(
            cursor,
            f"""INSERT INTO "{_SCHEMA}"."pos_transactions" (
                    pos_id, sales_reference_id, account_number, lead_id, match_score, match_type,
                    batch_id, membership_number, order_amount, transaction_count, fiscal_period,
                    fiscal_year, week, shop_type, warehouse_number, bd_industry, business_name,
                    address_line_one, address_line_two, city, state, zip_code, phone, first_name,
                    last_name, email, sic_code, sic_description, primary_transaction, load_date,
                    updated_by, updated_date
                )""",
            pos_tx_to_insert,
            32,
        )
        
        # Insert transaction
        print(f"Inserting {len(tx_to_insert)} records into transaction table...")
        bulk_insert(
            cursor,
            f"""INSERT INTO "{_SCHEMA}"."transaction" (
                    pos_id, sales_reference_id, account_number, lead_id, match_score, match_type,
                    batch_id, membership_number, order_amount, transaction_count, fiscal_period,
                    fiscal_year, week, shop_type, warehouse_number, bd_industry, business_name,
                    address_line_one, address_line_two, city, state, zip_code, phone, first_name,
                    last_name, email, sic_code, industry_description, load_date, updated_by,
                    updated_date, primary_transaction, oms_company, oms_company_2, oms_email_1,
                    oms_email_2, oms_email_3, oms_phone_1, oms_phone_2, oms_phone_3, oms_cell_1,
                    oms_cell_2, oms_first_name, oms_middle_name, oms_last_name, oms_address_line_1,
                    oms_city, oms_state, oms_zip, oms_address_line_1_v2, oms_address_line_2,
                    oms_address_line_3, oms_address_line_4, oms_address_line_5, oms_address_line_6,
                    oms_city_2, oms_state_2, oms_zip_2, oms_zip_3, oms_zip_4, matching_comments,
                    is_processed, process_datetime
                )""",
            tx_to_insert,
            63,
        )
            
        conn.commit()
        print("✅ POS mock data tables loaded successfully!")
        return True
    except Exception as e:
        print("❌ POS insertion transaction failed:", e)
        conn.rollback()
        return False

def clean_warehouse(cursor, conn, warehouse_number):
    wh = int(warehouse_number)
    print(f"\nCleaning all data for warehouse {wh}...")
    cursor.execute(
        f'DELETE FROM "{_SCHEMA}"."match_decision_detail" WHERE warehouse_number = %s', (wh,)
    )
    print(f"  match_decision_detail: {cursor.rowcount} rows deleted")
    cursor.execute(
        f'DELETE FROM "{_SCHEMA}"."leads_embeddings" WHERE warehouse_number = %s', (wh,)
    )
    print(f"  leads_embeddings: {cursor.rowcount} rows deleted")
    cursor.execute(
        f'DELETE FROM "{_SCHEMA}"."pos_embeddings" WHERE warehouse_number = %s', (wh,)
    )
    print(f"  pos_embeddings: {cursor.rowcount} rows deleted")
    cursor.execute(
        f'DELETE FROM "{_SCHEMA}"."transaction" WHERE warehouse_number = %s', (wh,)
    )
    print(f"  transaction: {cursor.rowcount} rows deleted")
    cursor.execute(
        f'DELETE FROM "{_SCHEMA}"."pos_transactions" WHERE warehouse_number = %s', (wh,)
    )
    print(f"  pos_transactions: {cursor.rowcount} rows deleted")
    cursor.execute(
        f"""DELETE FROM "{_SCHEMA}"."contact"
           WHERE lead_id IN (
               SELECT lead_id FROM "{_SCHEMA}"."lead" WHERE warehouse_number = %s
           )""",
        (wh,),
    )
    print(f"  contact: {cursor.rowcount} rows deleted")
    cursor.execute(
        f'DELETE FROM "{_SCHEMA}"."lead" WHERE warehouse_number = %s', (wh,)
    )
    print(f"  lead: {cursor.rowcount} rows deleted")
    cursor.execute(
        f"""DELETE FROM "{_SCHEMA}"."account"
           WHERE account_id NOT IN (
               SELECT DISTINCT account_id FROM "{_SCHEMA}"."lead"
           )"""
    )
    print(f"  account (orphaned): {cursor.rowcount} rows deleted")
    conn.commit()
    print(f"Warehouse {wh} cleaned.")


def show_summary(cursor):
    print("\n--- Summary of Loaded Data ---")
    try:
        for tbl in ['account', 'lead', 'contact', 'pos_transactions', 'transaction']:
            cursor.execute(f'SELECT count(*) FROM "{_SCHEMA}"."{tbl}";')
            count = cursor.fetchone()[0]
            print(f"Table 'leadmgmt.{tbl}': {count} records successfully loaded.")
    except Exception as e:
        print("❌ Error querying final summary:", e)

def main():
    parser = argparse.ArgumentParser(description="Load Mock Data to Cloud SQL PostgreSQL")
    parser.add_argument('target', nargs='?', choices=['lead', 'pos', 'all'],
                        help="Optional positional target: lead, pos, or all")
    parser.add_argument('--table', type=str, choices=['lead', 'pos', 'all'], default='lead',
                        help="Choose which tables to load: 'lead' (default), 'pos', or 'all'")
    parser.add_argument('--warehouse-number', type=str,
                        help="Warehouse number used to resolve mock_data/<warehouse_number>/ files")
    parser.add_argument('--input-dir', type=str,
                        help="Explicit directory containing leads_corrected.xlsx and pos_corrected.xlsx")
    parser.add_argument('--limit', type=int,
                        help="Load only the first N rows from each selected Excel file")
    parser.add_argument('--summary-only', action='store_true',
                        help="Connect and print table counts without loading data")
    parser.add_argument('--clean', action='store_true',
                        help="Delete existing data for this warehouse before loading (requires --warehouse-number)")
    args = parser.parse_args()
    target = args.target or args.table

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")

    print(f"--- Mock Data Loader Started (Target: {target}) ---")
    warehouse_number = args.warehouse_number or os.environ.get("WAREHOUSE_NUMBER") or os.environ.get("WAREHOUSE")
    leads_file = resolve_mock_file("leads_corrected.xlsx", warehouse_number, args.input_dir)
    pos_file = resolve_mock_file("pos_corrected.xlsx", warehouse_number, args.input_dir)
    print(f"Warehouse scope: {warehouse_number or 'default'}")
    print(f"Lead workbook path: {leads_file}")
    print(f"POS workbook path: {pos_file}")
    db_config = get_db_config()
    missing = [key for key in ('host', 'password') if not db_config.get(key)]
    if missing:
        print(f"❌ Missing database config in {ENV_FILE}: {', '.join(missing)}")
        sys.exit(1)
    
    # 1. Connect to Database
    print(f"Connecting to Cloud SQL PostgreSQL database at {db_config['host']}:{db_config['port']}...")
    try:
        conn = pg8000.dbapi.connect(**db_config)
        cursor = conn.cursor()
        print("Connected successfully!")
    except Exception as e:
        print("❌ Database connection failed:", e)
        sys.exit(1)

    if args.summary_only:
        show_summary(cursor)
        conn.close()
        return

    if args.clean:
        if not warehouse_number:
            print("--clean requires --warehouse-number")
            sys.exit(1)
        clean_warehouse(cursor, conn, warehouse_number)

    success = True
    if target in ['lead', 'all']:
        success_lead = load_leads(cursor, conn, leads_file, args.limit)
        success = success and success_lead

    if target in ['pos', 'all']:
        success_pos = load_pos(cursor, conn, pos_file, args.limit)
        success = success and success_pos

    if success:
        print("\n🎉 Mock data loading completed successfully!")
    else:
        print("\n❌ Mock data loading completed with errors.")

    show_summary(cursor)
    conn.close()

if __name__ == '__main__':
    main()
