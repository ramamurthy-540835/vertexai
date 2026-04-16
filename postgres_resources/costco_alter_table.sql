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

-- GRANT UPDATE ON  TABLE  "$SCHEMA_NAME"."lead" TO "postgres";
-- GRANT UPDATE ON  TABLE  "$SCHEMA_NAME"."transaction" TO "postgres";

-- Alter table "$SCHEMA_NAME".match_configuration 
-- ADD COLUMN match_result VARCHAR(10);

-- UPDATE "$SCHEMA_NAME".match_configuration
-- SET match_result =
--     CASE WHEN confidence_level = 'High' THEN 'Complete'
--          ELSE 'Potential'
--     END;

-- ALTER TABLE "$SCHEMA_NAME".lead
-- ADD COLUMN match_result VARCHAR(10);
UPDATE "$SCHEMA_NAME".match_configuration 
set match_result = 'No Match' where confidence_level = 'No Match';

