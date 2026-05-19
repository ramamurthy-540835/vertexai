-- ============================================================
-- ALTER STATEMENTS
-- ============================================================

Alter table "$SCHEMA_NAME".account 
ALTER COLUMN state TYPE VARCHAR(50);

Alter table "$SCHEMA_NAME".account 
ALTER COLUMN business_name TYPE VARCHAR(150);

Alter table "$SCHEMA_NAME".contact 
ALTER COLUMN phone TYPE VARCHAR(100);

Alter table "$SCHEMA_NAME".error_audit
ADD COLUMN batch_id uuid;

Alter table "$SCHEMA_NAME".pos_embeddings
ADD COLUMN week int;

Alter table "$SCHEMA_NAME".match_configuration 
ADD COLUMN match_result VARCHAR(10);

UPDATE "$SCHEMA_NAME".match_configuration
SET match_result =
    CASE WHEN confidence_level = 'High' THEN 'Match'
         ELSE 'Potential'
    END;

UPDATE "$SCHEMA_NAME".match_configuration 
SET match_result = 'No Match' WHERE confidence_level = 'No Match';

ALTER TABLE "$SCHEMA_NAME".lead
ADD COLUMN match_result VARCHAR(10);

Alter table "$SCHEMA_NAME".transaction
RENAME COLUMN nmi_flag TO primary_transaction;

Alter table "$SCHEMA_NAME".transaction
RENAME COLUMN sic_description TO industry_description;

ALTER TABLE "$SCHEMA_NAME".transaction
ALTER COLUMN pos_id
SET DEFAULT 'POS' || LPAD(nextval('"$SCHEMA_NAME".transaction_pos_id_seq')::text, 8, '0');

ALTER TABLE "$SCHEMA_NAME".transaction
    ADD COLUMN IF NOT EXISTS oms_company           VARCHAR(200),
    ADD COLUMN IF NOT EXISTS oms_company_2         VARCHAR(200),
    ADD COLUMN IF NOT EXISTS oms_email_1           VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_email_2           VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_email_3           VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_phone_1           VARCHAR(40),
    ADD COLUMN IF NOT EXISTS oms_phone_2           VARCHAR(40),
    ADD COLUMN IF NOT EXISTS oms_phone_3           VARCHAR(40),
    ADD COLUMN IF NOT EXISTS oms_cell_1            VARCHAR(40),
    ADD COLUMN IF NOT EXISTS oms_cell_2            VARCHAR(40),
    ADD COLUMN IF NOT EXISTS oms_first_name        VARCHAR(100),
    ADD COLUMN IF NOT EXISTS oms_middle_name       VARCHAR(100),
    ADD COLUMN IF NOT EXISTS oms_last_name         VARCHAR(100),
    ADD COLUMN IF NOT EXISTS oms_address_line_1    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_city              VARCHAR(80),
    ADD COLUMN IF NOT EXISTS oms_state             VARCHAR(50),
    ADD COLUMN IF NOT EXISTS oms_zip               VARCHAR(20),
    ADD COLUMN IF NOT EXISTS oms_address_line_1_v2 VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_address_line_2    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_address_line_3    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_address_line_4    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_address_line_5    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_address_line_6    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS oms_city_2            VARCHAR(80),
    ADD COLUMN IF NOT EXISTS oms_state_2           VARCHAR(50),
    ADD COLUMN IF NOT EXISTS oms_zip_2             VARCHAR(20),
    ADD COLUMN IF NOT EXISTS oms_zip_3             VARCHAR(20),
    ADD COLUMN IF NOT EXISTS oms_zip_4             VARCHAR(20);

DROP INDEX IF EXISTS "$SCHEMA_NAME".txn_uniq_sales_reference_id_idx;

ALTER TABLE "$SCHEMA_NAME".match_audit DROP COLUMN IF EXISTS no_match_count;


-- ============================================================
-- GRANT STATEMENTS
-- ============================================================

GRANT SELECT ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

GRANT DELETE ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

GRANT INSERT ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

GRANT SELECT, INSERT, UPDATE, DELETE ON 
    "$SCHEMA_NAME".lead,
    "$SCHEMA_NAME".transaction,
    "$SCHEMA_NAME".leads_embeddings,
    "$SCHEMA_NAME".pos_embeddings,
    "$SCHEMA_NAME".account,
    "$SCHEMA_NAME".contact,
    "$SCHEMA_NAME".error_Audit,
    "$SCHEMA_NAME".batch_audit,
    "$SCHEMA_NAME".pos_transactions
TO "postgres";