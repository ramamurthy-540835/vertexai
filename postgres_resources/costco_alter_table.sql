Alter table "$SCHEMA_NAME".account 
ALTER COLUMN state TYPE VARCHAR(50);

GRANT SELECT ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

Alter table "$SCHEMA_NAME".error_audit
ADD COLUMN batch_id uuid;

GRANT DELETE ON ALL TABLES IN SCHEMA "$SCHEMA_NAME" TO "postgres";

Alter table "$SCHEMA_NAME".account 
ALTER COLUMN business_name TYPE VARCHAR(150);