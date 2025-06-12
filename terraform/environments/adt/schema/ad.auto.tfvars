location	= "us" 
environment	= "adt"
projectId	= "p-601-np-membership-adt"
# SQL Scripts to execute
sql_scripts = {
  "create_schema" = {
    file_path  = "../../../../postgres_resources/lead_mgmt_schema_creation.sql"
    always_run = false
 }
}