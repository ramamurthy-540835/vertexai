from google.cloud import aiplatform
from costco.leadmgmt.pipeline_manager.compile_pipeline import compile_and_upload_pipeline
from costco.leadmgmt.util.match_id_creation import match_id_creation
from costco.leadmgmt.components.* import update_cloud_sql,update_servicenow,data_ingestion_cloud_sql,
primary_matching,temporary_file_deletion
import sys
import os


def run_pipeline(config_file_path):

    project_id = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION")
    pipeline_name = os.environ.get("PIPELINE_NAME")
    vertex_ai_network = os.environ.get("VERTEX_AI_NETWORK")
    service_account = os.environ.get("SERVICE_ACCOUNT")
    registry_url = os.environ.get("ARTIFACT_REGISTRY_URL")
    bucket = os.environ.get("GCS_BUCKET")

    aiplatform.init(project=project_id, location=region)
    template_path = f"{registry_url}/{pipeline_name}/latest"

    match_id = str(match_id_creation())



    job = aiplatform.PipelineJob(
        display_name=f"{pipeline_name}-latest",
        template_path=template_path,
        pipeline_root=f"gs://{bucket}/pipelines",
        parameter_values={
            'match_id': match_id,
            'config_file_path': config_file_path
        }
    )

    job.run(network=vertex_ai_network, service_account=service_account, sync=True)
    print("Pipeline executed successfully.")

if __name__ == "__main__":
    stage = sys.argv[1]

    config_file_path = os.environ.get("CONFIG_FILE_PATH")
    pipeline_name = os.environ.get("PIPELINE_NAME")
    registry_url = os.environ.get("ARTIFACT_REGISTRY_URL")

    if stage.lower() == "compile_run_pipeline":
        compile_and_upload_pipeline(pipeline_name,registry_url)
        run_pipeline(config_file_path)
    elif stage.lower() == "run_pipeline":
        run_pipeline(config_file_path)
    elif stage.lower() == "compile_pipeline":
        compile_and_upload_pipeline(pipeline_name, registry_url)
    elif stage.lower() == "update_database":
        update_cloud_sql(config_file_path)
    elif stage.lower() == "update_service_now":
        update_servicenow(config_file_path)
    elif stage.lower() == "ingest_leads_from_cloud_sql":
        data_ingestion_cloud_sql('leads',config_file_path)
    elif stage.lower() == "ingest_leads_from_cloud_sql":
        data_ingestion_cloud_sql('pos',config_file_path)   
    elif stage.lower() == "primary_matching":
        lead_matching(match_id,config_file_path)
    elif stage.lower() == "temporary_file_deletion":
        temporary_file_deletion(match_id,config_file_path)