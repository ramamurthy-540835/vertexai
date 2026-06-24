-- Generated from: Studio Results 2026-06-20 14_03.xlsx
-- Purpose: Idempotent Cloud SQL/PostgreSQL schema for project ctoteam.
-- Safe to re-run: uses IF NOT EXISTS throughout, never drops existing objects.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE SCHEMA IF NOT EXISTS "leadmgmt";

CREATE TABLE IF NOT EXISTS "leadmgmt"."account" (
    "account_id" character varying NOT NULL,
    "batch_id" uuid,
    "account_number" bigint,
    "type" character varying,
    "business_name" character varying,
    "address_line_one" character varying,
    "address_line_two" character varying,
    "city" character varying,
    "state" character varying,
    "zip_code" character varying,
    "phone" character varying,
    "email" character varying,
    "industry_code" character varying,
    "bd_industry" character varying,
    "updated_by" character varying,
    "updated_date" timestamp with time zone,
    PRIMARY KEY ("account_id")
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."api_audit" (
    "id" uuid,
    "batch_id" uuid,
    "data_type" character varying,
    "load_date" timestamp without time zone,
    "total_volume" integer,
    "success_count" integer,
    "stage" character varying,
    "status" character varying,
    "start_date" timestamp with time zone,
    "end_date" timestamp without time zone,
    "comments" text
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."batch_audit" (
    "id" uuid,
    "batch_id" uuid,
    "data_type" character varying,
    "load_date" timestamp with time zone,
    "total_volume" integer,
    "success_count" integer,
    "stage" character varying,
    "status" character varying,
    "start_date" timestamp with time zone,
    "end_date" timestamp with time zone,
    "comments" text
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."contact" (
    "contact_id" character varying NOT NULL,
    "lead_id" character varying,
    "first_name" character varying,
    "last_name" character varying,
    "email" character varying,
    "phone" character varying,
    "membership_number" bigint,
    "job_title" character varying,
    "batch_id" uuid,
    "updated_by" character varying,
    "updated_date" timestamp with time zone,
    PRIMARY KEY ("contact_id")
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."error_audit" (
    "error_log_id" uuid NOT NULL,
    "entity_type" character varying NOT NULL,
    "entity_id" character varying NOT NULL,
    "error_message" text,
    "created_at" timestamp with time zone,
    "is_processed" boolean,
    "processed_at" timestamp with time zone,
    "batch_id" uuid,
    PRIMARY KEY ("error_log_id")
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."lead" (
    "lead_id" character varying(20) NOT NULL,
    "lead_source" character varying,
    "account_id" character varying,
    "account_number" bigint,
    "lead_status" character varying,
    "confidence_level" character varying,
    "membership_number" bigint,
    "warehouse_number" integer,
    "fiscal_period" integer,
    "fiscal_year" integer,
    "closed_fiscal_period" integer,
    "closed_fiscal_year" integer,
    "batch_id" uuid,
    "load_date" timestamp with time zone,
    "updated_by" character varying,
    "updated_date" timestamp with time zone,
    "match_result" character varying,
    "week" integer,
    PRIMARY KEY ("lead_id")
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."leads_embeddings" (
    "lead_id" character varying,
    "combined_field" character varying,
    "business_name" character varying,
    "business_address" character varying,
    "combined_embedding" vector(768),
    "address_embedding" vector(768),
    "name_embedding" vector(768),
    "updated_date" timestamp with time zone,
    "warehouse_number" integer,
    "fiscal_year" integer,
    "fiscal_period" integer
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."match_audit" (
    "match_id" uuid NOT NULL,
    "lead_count" integer,
    "pos_count" integer,
    "match_count" integer,
    "stats" character varying,
    "status" character varying,
    "start_date" timestamp with time zone,
    "end_date" timestamp with time zone,
    "update_date" timestamp with time zone,
    "comments" text,
    PRIMARY KEY ("match_id")
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."match_configuration" (
    "confidence_level" character varying,
    "min_score" double precision,
    "max_score" double precision,
    "match_result" character varying
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."pos_embeddings" (
    "pos_id" character varying,
    "account_number" bigint,
    "combined_field" character varying,
    "business_name" character varying,
    "business_address" character varying,
    "combined_embedding" vector(768),
    "address_embedding" vector(768),
    "name_embedding" vector(768),
    "load_date" timestamp with time zone,
    "warehouse_number" integer,
    "fiscal_year" integer,
    "fiscal_period" integer,
    "week" integer
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."pos_transactions" (
    "pos_id" character varying NOT NULL,
    "sales_reference_id" character varying,
    "account_number" bigint,
    "lead_id" character varying,
    "match_score" double precision,
    "match_type" character varying,
    "batch_id" uuid,
    "membership_number" bigint,
    "order_amount" double precision,
    "transaction_count" integer,
    "fiscal_period" integer,
    "fiscal_year" integer,
    "week" integer,
    "shop_type" character varying,
    "warehouse_number" bigint,
    "bd_industry" character varying,
    "business_name" character varying,
    "address_line_one" character varying,
    "address_line_two" character varying,
    "city" character varying,
    "state" character varying,
    "zip_code" character varying,
    "phone" character varying,
    "first_name" character varying,
    "last_name" character varying,
    "email" character varying,
    "sic_code" bigint,
    "sic_description" character varying,
    "primary_transaction" boolean,
    "load_date" timestamp with time zone,
    "updated_by" character varying,
    "updated_date" timestamp with time zone,
    PRIMARY KEY ("pos_id")
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."transaction" (
    "pos_id" character varying(120) NOT NULL,
    "sales_reference_id" character varying,
    "account_number" bigint,
    "lead_id" character varying,
    "match_score" double precision,
    "match_type" character varying,
    "batch_id" uuid,
    "membership_number" bigint,
    "order_amount" double precision,
    "transaction_count" integer,
    "fiscal_period" integer,
    "fiscal_year" integer,
    "week" integer,
    "shop_type" character varying,
    "warehouse_number" bigint,
    "bd_industry" character varying,
    "business_name" character varying,
    "address_line_one" character varying,
    "address_line_two" character varying,
    "city" character varying,
    "state" character varying,
    "zip_code" character varying,
    "phone" character varying,
    "first_name" character varying,
    "last_name" character varying,
    "email" character varying,
    "sic_code" bigint,
    "industry_description" character varying,
    "load_date" timestamp with time zone,
    "updated_by" character varying,
    "updated_date" timestamp with time zone,
    "primary_transaction" boolean,
    "oms_company" character varying,
    "oms_company_2" character varying,
    "oms_email_1" character varying,
    "oms_email_2" character varying,
    "oms_email_3" character varying,
    "oms_phone_1" character varying,
    "oms_phone_2" character varying,
    "oms_phone_3" character varying,
    "oms_cell_1" character varying,
    "oms_cell_2" character varying,
    "oms_first_name" character varying,
    "oms_middle_name" character varying,
    "oms_last_name" character varying,
    "oms_address_line_1" character varying,
    "oms_city" character varying,
    "oms_state" character varying,
    "oms_zip" character varying,
    "oms_address_line_1_v2" character varying,
    "oms_address_line_2" character varying,
    "oms_address_line_3" character varying,
    "oms_address_line_4" character varying,
    "oms_address_line_5" character varying,
    "oms_address_line_6" character varying,
    "oms_city_2" character varying,
    "oms_state_2" character varying,
    "oms_zip_2" character varying,
    "oms_zip_3" character varying,
    "oms_zip_4" character varying,
    "matching_comments" text,
    "is_processed" boolean NOT NULL,
    "process_datetime" timestamp without time zone,
    PRIMARY KEY ("pos_id")
);

CREATE TABLE IF NOT EXISTS "leadmgmt"."match_decision_detail" (
    -- Core Match Identifiers
    "match_run_id" character varying(100) NOT NULL,
    "lead_id" character varying(20) NOT NULL,
    "pos_id" character varying(120) NOT NULL,
    "warehouse_number" integer,
    
    -- Model Prediction & Explainability
    "match_type" character varying(100),
    "final_score" double precision,
    "combined_field_score" double precision,
    "full_address_score" double precision,
    "business_name_score" double precision,
    "weight_formula" character varying(100),
    "embedding_model" character varying(100),
    "created_date" timestamp with time zone DEFAULT CURRENT_TIMESTAMP,

    -- Human-in-the-Loop Feedback
    "analyst_decision" character varying(50), -- e.g., 'Approved', 'Rejected'
    "analyst_comments" text,
    "updated_by_analyst" character varying(100),
    "updated_at_analyst" timestamp with time zone,

    -- AI-Generated Per-Row Analysis (owned exclusively by analysis workflow)
    "match_reasoning" text,

    -- Constraints for Data Integrity
    PRIMARY KEY ("match_run_id", "lead_id", "pos_id"),
    CONSTRAINT "fk_mdd_lead" FOREIGN KEY ("lead_id") REFERENCES "leadmgmt"."lead" ("lead_id"),
    CONSTRAINT "fk_mdd_txn" FOREIGN KEY ("pos_id") REFERENCES "leadmgmt"."transaction" ("pos_id")
);

-- Indexes for fast lookup of match history
CREATE INDEX IF NOT EXISTS "idx_mdd_run" ON "leadmgmt"."match_decision_detail" ("match_run_id");
CREATE INDEX IF NOT EXISTS "idx_mdd_lead_id" ON "leadmgmt"."match_decision_detail" ("lead_id");


CREATE INDEX IF NOT EXISTS "idx_account_account_number" ON "leadmgmt"."account" ("account_number");
CREATE INDEX IF NOT EXISTS "idx_account_business_name" ON "leadmgmt"."account" ("business_name");
CREATE INDEX IF NOT EXISTS "idx_contact_lead_id" ON "leadmgmt"."contact" ("lead_id");
CREATE INDEX IF NOT EXISTS "idx_lead_account_id" ON "leadmgmt"."lead" ("account_id");
CREATE INDEX IF NOT EXISTS "idx_lead_warehouse_period" ON "leadmgmt"."lead" ("warehouse_number", "fiscal_year", "fiscal_period");
CREATE INDEX IF NOT EXISTS "idx_pos_transactions_account_number" ON "leadmgmt"."pos_transactions" ("account_number");
CREATE INDEX IF NOT EXISTS "idx_pos_transactions_warehouse_period" ON "leadmgmt"."pos_transactions" ("warehouse_number", "fiscal_year", "fiscal_period", "week");
CREATE INDEX IF NOT EXISTS "idx_transaction_account_number" ON "leadmgmt"."transaction" ("account_number");
CREATE INDEX IF NOT EXISTS "idx_transaction_warehouse_period" ON "leadmgmt"."transaction" ("warehouse_number", "fiscal_year", "fiscal_period", "week");
CREATE INDEX IF NOT EXISTS "idx_leads_embeddings_period" ON "leadmgmt"."leads_embeddings" ("warehouse_number", "fiscal_year", "fiscal_period");
CREATE INDEX IF NOT EXISTS "idx_pos_embeddings_period" ON "leadmgmt"."pos_embeddings" ("warehouse_number", "fiscal_year", "fiscal_period", "week");
CREATE UNIQUE INDEX IF NOT EXISTS "idx_leads_embeddings_lead_id_unique" ON "leadmgmt"."leads_embeddings" ("lead_id");
CREATE UNIQUE INDEX IF NOT EXISTS "idx_pos_embeddings_pos_id_unique" ON "leadmgmt"."pos_embeddings" ("pos_id");
CREATE INDEX IF NOT EXISTS "idx_leads_embeddings_combined_hnsw" ON "leadmgmt"."leads_embeddings" USING hnsw ("combined_embedding" vector_cosine_ops);
CREATE INDEX IF NOT EXISTS "idx_pos_embeddings_combined_hnsw" ON "leadmgmt"."pos_embeddings" USING hnsw ("combined_embedding" vector_cosine_ops);

COMMIT;
