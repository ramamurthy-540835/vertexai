-- Grant permissions on the database

GRANT ALL PRIVILEGES ON DATABASE "$DATABASE_NAME" TO "$IAM_USER";

-- Create application schema

CREATE SCHEMA IF NOT EXISTS "$SCHEMA_NAME";

-- Set default privileges for the service account
GRANT USAGE, CREATE ON SCHEMA "$SCHEMA_NAME" TO "$IAM_USER";
ALTER DEFAULT PRIVILEGES IN SCHEMA "$SCHEMA_NAME" GRANT ALL ON TABLES TO "$IAM_USER";

-- Create vector extension
CREATE EXTENSION IF NOT EXISTS vector;