-- Grant permissions on the database

GRANT ALL PRIVILEGES ON DATABASE "lead-mgmt-db" TO "gco-iam-svc-cicd-mbr-bc-np@gcp-prj-cicd-core.iam";

-- Create application schema

CREATE SCHEMA IF NOT EXISTS $SCHEMA_NAME;

-- Set default privileges for the service account
ALTER DEFAULT PRIVILEGES IN SCHEMA $SCHEMA_NAME GRANT ALL ON TABLES TO "gco-iam-svc-cicd-mbr-bc-np@gcp-prj-cicd-core.iam";

-- Create vector extension
CREATE EXTENSION IF NOT EXISTS vector SCHEMA $SCHEMA_NAME;