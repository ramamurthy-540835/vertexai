CREATE TABLE IF NOT EXISTS
  "$SCHEMA_NAME".account ( 
  account_id  varchar(20) PRIMARY KEY,   
  batch_id uuid,
  account_number bigint,
  type varchar(50),
    business_name VARCHAR(75),
    address_line_one VARCHAR(100),
	address_line_two VARCHAR(100),
    city VARCHAR(50),
    state VARCHAR(2), 
    zip_code VARCHAR(10),
    phone VARCHAR(40) NULL,
	email VARCHAR(100) NULL,
    industry_code VARCHAR(40) NULL, 
    bd_industry VARCHAR(50),
    updated_by VARCHAR(100) NULL,
    updated_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') ,
    CONSTRAINT uniq_account_name_addr_01
UNIQUE (business_name, address_line_one,address_line_two, city, state, zip_code)

    );

CREATE TABLE IF NOT EXISTS
  "$SCHEMA_NAME".lead ( 
  lead_id varchar(20)     PRIMARY KEY,
  lead_source varchar(100),
    account_id varchar(20),
	account_number bigint NULL,
    lead_status VARCHAR(100) NULL,
    confidence_level VARCHAR(100) NULL,
    membership_number bigint NULL,
    warehouse_number int NULL,
	fiscal_period int,
    fiscal_year int,
	closed_fiscal_period int NULL,
    closed_fiscal_year int NULL,
	batch_id uuid,
    load_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') ,
    updated_by VARCHAR(100),
    updated_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') ,
    

  FOREIGN KEY
    (account_id)
  REFERENCES
    "$SCHEMA_NAME".account (account_id)
 	) ;
  
 CREATE  INDEX IF NOT EXISTS lead_status_index  ON "$SCHEMA_NAME".lead (lead_status);
 
 
CREATE TABLE IF NOT EXISTS
  "$SCHEMA_NAME".contact( 
  contact_id varchar(200)    PRIMARY KEY,
  lead_id varchar(20) , 
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    email VARCHAR(100) NULL,
    phone VARCHAR(20) NULL,
	membership_number bigint NULL,
    job_title VARCHAR(100) NULL,
	batch_id uuid,
    updated_by VARCHAR(100),
    updated_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') ,
  FOREIGN KEY
    (lead_id)
  REFERENCES
    "$SCHEMA_NAME".lead (lead_id));


CREATE TABLE IF NOT EXISTS
  "$SCHEMA_NAME".transaction ( 
    pos_id varchar(120) PRIMARY KEY,     
    sales_reference_id  varchar(130),
	account_number bigint,
    lead_id varchar(20) NULL,
	match_score float,
	match_type varchar(20),
    batch_id uuid,
    membership_number bigint,
    order_amount float,
	transaction_count int, 
    fiscal_period int,
    fiscal_year int,
	week int,
    shop_type varchar(40),
	warehouse_number bigint,
	bd_industry varchar(200),
    business_name varchar(100),
    address_line_one VARCHAR(100),
	address_line_two VARCHAR(100),
    city varchar(30),
    state varchar(50),
    zip_code varchar(100),
    phone varchar(30) NULL,
    first_name varchar(100) NULL,
    last_name varchar(100),
    email varchar(50) NULL,
	sic_code bigint,
	sic_description VARCHAR(1000),
	load_date TIMESTAMP WITH TIME ZONE  DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    updated_by varchar(20) NULL,
    updated_date TIMESTAMP WITH TIME ZONE  DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
  FOREIGN KEY
    (lead_id)
  REFERENCES
    "$SCHEMA_NAME".lead (lead_id) ) ;
	
CREATE UNIQUE index IF NOT EXISTS txn_uniq_sales_reference_id_idx  ON "$SCHEMA_NAME".transaction ( sales_reference_id);
CREATE index IF NOT EXISTS txn_fiscal_year_period_idx  ON "$SCHEMA_NAME".transaction ( fiscal_year,fiscal_period);

  

CREATE UNIQUE INDEX IF NOT EXISTS account_unique_with_nulls_as_value
ON "$SCHEMA_NAME".account (
    COALESCE(business_name, '__NULL__'),
    COALESCE(address_line_one, '__NULL__'),
    COALESCE(address_line_two, '__NULL__'),
    COALESCE(city, '__NULL__'),
    COALESCE(state, '__NULL__'),
    COALESCE(zip_code, '__NULL__')
);


CREATE TABLE IF NOT EXISTS
  "$SCHEMA_NAME".batch_audit ( id uuid DEFAULT gen_random_uuid(), 
	batch_id uuid ,  
    data_type VARCHAR(100),	
	load_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    total_volume int null,
	success_count int  null,
	stage varchar(30),
	status varchar(30) ,
	start_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
	end_date TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    comments text
    );
	
CREATE TABLE IF NOT EXISTS
  "$SCHEMA_NAME".match_audit ( match_id uuid DEFAULT gen_random_uuid() PRIMARY KEY, 
    lead_count int,
	pos_count int ,
	match_count int,
	no_match_count int,
	stats varchar(100) NULL,
	status VARCHAR(10) ,
	start_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
	end_date TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    update_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
	comments text
    );
	
CREATE TABLE IF NOT EXISTS
  "$SCHEMA_NAME".api_audit ( id uuid DEFAULT gen_random_uuid(), 
	batch_id uuid ,  
    data_type VARCHAR(100),
	load_date TIMESTAMP DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
    total_volume int,
	success_count int ,
	stage varchar(30),
	status varchar(30),
	start_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
	end_date timestamp NULL,
    comments text
    );
	


CREATE TABLE IF NOT EXISTS "$SCHEMA_NAME".leads_embeddings( 
  lead_id VARCHAR,
  combined_field VARCHAR,
  business_name VARCHAR,
  business_address VARCHAR,
  combined_embedding VECTOR(768),
  address_embedding VECTOR(768),
  name_embedding VECTOR(768),
  updated_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
  warehouse_number INT,
  fiscal_year INT,
  fiscal_period INT
);
 
CREATE TABLE IF NOT EXISTS "$SCHEMA_NAME".pos_embeddings(
  pos_id VARCHAR,
  account_number BIGINT,
  combined_field VARCHAR,
  business_name VARCHAR,
  business_address VARCHAR,
  combined_embedding VECTOR(768),
  address_embedding VECTOR(768),
  name_embedding VECTOR(768),
  load_date TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'UTC'),
  warehouse_number INT,
  fiscal_year INT,
  fiscal_period INT
);
 
CREATE INDEX IF NOT EXISTS ON "$SCHEMA_NAME".leads_embeddings USING hnsw (combined_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS ON "$SCHEMA_NAME".pos_embeddings USING hnsw (combined_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS warehouse_index_leads  ON  "$SCHEMA_NAME".leads_embeddings  (warehouse_number);
 
CREATE INDEX IF NOT EXISTS  warehouse_index_pos  ON   "$SCHEMA_NAME".pos_embeddings  (warehouse_number);
CREATE INDEX IF NOT EXISTS  lead_id_indx ON  "$SCHEMA_NAME".leads_embeddings  (lead_id);
 
CREATE INDEX IF NOT EXISTS pos_id_indx ON  "$SCHEMA_NAME".pos_embeddings  (pos_id);
 

CREATE TABLE IF NOT EXISTS "$SCHEMA_NAME".match_configuration(
confidence_level varchar(20),
min_score float,
max_score float,
CONSTRAINT unique_confidence_level UNIQUE (confidence_level)
) ;

 commit;


 