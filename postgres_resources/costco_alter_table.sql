-- Alter table "$SCHEMA_NAME".account 
-- ALTER COLUMN state TYPE VARCHAR(50);

-- GRANT SELECT ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

-- Alter table "$SCHEMA_NAME".error_audit
-- ADD COLUMN batch_id uuid;

-- GRANT DELETE ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

-- Alter table "$SCHEMA_NAME".account 
-- ALTER COLUMN business_name TYPE VARCHAR(150);

-- GRANT INSERT ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

-- Alter table "$SCHEMA_NAME".contact 
-- ALTER COLUMN phone TYPE VARCHAR(100);

-- Alter table "$SCHEMA_NAME".pos_embeddings
-- ADD COLUMN week int;

GRANT UPDATE ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";