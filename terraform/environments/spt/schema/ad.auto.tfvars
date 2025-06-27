projectId	= "p-601-np-bcleadsmgmt-spt"
# SQL Scripts to execute
sql_scripts = {
  "create_schema" = {
    file_path  = "../../../../postgres_resources/lead_mgmt_schema_creation.sql"
    always_run = false
 }
}