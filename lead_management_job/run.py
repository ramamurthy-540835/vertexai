from google.cloud import aiplatform,aiplatform_v1
from costco.leadmgmt.pipeline_manager.compile_pipeline import compile_and_upload_pipeline
from costco.leadmgmt.util.match_id_creation import match_id_creation
from costco.leadmgmt.components.update_source_data import update_cloud_sql
from costco.leadmgmt.components.update_servicenow import update_servicenow
from costco.leadmgmt.components.data_ingestion_cloud_sql import load_and_preprocess_data_cloud_sql
from costco.leadmgmt.components.lead_matching import primary_classification
#from costco.leadmgmt.components.temporary_file_deletion import delete_temp_files_from_gcs
from costco.leadmgmt.components.validation_del_temp_files import delete_temp_files_from_gcs,mark_match_failed
import sys
import os

match_id = os.environ.get("MATCH_ID") or str(match_id_creation())
print(f"Using match_id: {match_id}")

def run_pipeline(config_file_path,match_id):

    project_id = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION")
    pipeline_name = os.environ.get("PIPELINE_NAME")
    vertex_ai_network = os.environ.get("VERTEX_AI_NETWORK")
    service_account = os.environ.get("SERVICE_ACCOUNT")
    registry_url = os.environ.get("ARTIFACT_REGISTRY_URL")
    bucket = os.environ.get("GCS_BUCKET")
    max_workers = os.environ.get('MAX_WORKERS')
    network_attachment = os.environ.get("NETWORK_ATTACHMENT")

    aiplatform.init(project=project_id, location=region)
    template_path = f"{registry_url}/{pipeline_name}/latest"

    # job = aiplatform.PipelineJob(
    #     display_name=f"{pipeline_name}-latest",
    #     template_path=template_path,
    #     pipeline_root=f"gs://{bucket}/pipelines",
    #     parameter_values={
    #         'match_id': match_id,
    #         'config_file_path': config_file_path,
    #         'project_id' : project_id,
    #         'max_workers': max_workers
    #     }
    # )

    # job.run(network=vertex_ai_network, service_account=service_account, sync=True)
    # print("Pipeline executed successfully.")

    client = aiplatform_v1.PipelineServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    request = aiplatform_v1.CreatePipelineJobRequest(
        parent=f"projects/{project_id}/locations/{region}",
        pipeline_job=aiplatform_v1.PipelineJob(
            display_name=f"{pipeline_name}-latest",
            template_uri=template_path,
            runtime_config=aiplatform_v1.PipelineJob.RuntimeConfig(
                gcs_output_directory=f"gs://{bucket}/pipelines",
                parameter_values={
                    'match_id': match_id,
                    'config_file_path': config_file_path,
                    'project_id': project_id,
                    'max_workers': max_workers
                }
            ),
            service_account=service_account,
            psc_interface_config=aiplatform_v1.PscInterfaceConfig(
                network_attachment=f"projects/{project_id}/regions/{region}/networkAttachments/{network_attachment}"
            ),
        )
    )

    response = client.create_pipeline_job(request=request)
    print(f"Pipeline job created: {response.name}")
    print("Pipeline executed successfully.")

if __name__ == "__main__":
    stage = sys.argv[1]

    config_file_path = os.environ.get("CONFIG_FILE_PATH")
    pipeline_name = os.environ.get("PIPELINE_NAME")
    registry_url = os.environ.get("ARTIFACT_REGISTRY_URL")

    if stage.lower() == "compile_run_pipeline":
        compile_and_upload_pipeline(pipeline_name,registry_url)
        run_pipeline(config_file_path,match_id)
    elif stage.lower() == "run_pipeline":
        run_pipeline(config_file_path,match_id)
    elif stage.lower() == "compile_pipeline":
        compile_and_upload_pipeline(pipeline_name, registry_url)
    elif stage.lower() == "update_database":
        update_cloud_sql(config_file_path)
    elif stage.lower() == "update_service_now":
        update_servicenow(config_file_path)
    elif stage.lower() == "ingest_leads_from_cloud_sql":
        load_and_preprocess_data_cloud_sql('leads',config_file_path)
    elif stage.lower() == "ingest_pos_from_cloud_sql":
        load_and_preprocess_data_cloud_sql('pos',config_file_path)   
    elif stage.lower() == "primary_matching":
        primary_classification(match_id,config_file_path)
    elif stage.lower() == "temporary_file_deletion":
        delete_temp_files_from_gcs(match_id,config_file_path)
    elif stage.lower() == "mark_match_failed":
        mark_match_failed(match_id, config_file_path)