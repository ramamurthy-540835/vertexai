-- Grant permissions on the database

GRANT ALL PRIVILEGES ON DATABASE "lead-mgmt-db" TO CURRENT_USER;

-- Create application schema

CREATE SCHEMA IF NOT EXISTS lead_mgmt_adt;

-- Set default privileges for the service account
ALTER DEFAULT PRIVILEGES IN SCHEMA lead_mgmt_adt GRANT ALL ON TABLES TO CURRENT_USER;

-- Create vector extension
CREATE EXTENSION IF NOT EXISTS vector SCHEMA lead_mgmt_adt;