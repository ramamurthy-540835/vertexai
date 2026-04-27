import os
from kfp import dsl
import configparser
from kfp.dsl import PipelineTaskFinalStatus
import os

BASE_IMAGE = os.environ.get("KFP_CUSTOM_IMAGE")
CPU_LIMIT = os.environ.get("CPU_LIMIT")
MEMORY_LIMIT = os.environ.get("MEMORY_LIMIT")
CPU_LIMIT_EG = os.environ.get("CPU_LIMIT_EG")
MEMORY_LIMIT_EG = os.environ.get("MEMORY_LIMIT_EG")


@dsl.component(base_image=BASE_IMAGE)
def load_and_preprocess_data_cloud_sql(base_name: str, config_file_path: str) -> str:
    from costco.leadmgmt.components.data_ingestion_cloud_sql import load_and_preprocess_data_cloud_sql

    return load_and_preprocess_data_cloud_sql(base_name=base_name, config_file_path=config_file_path)


@dsl.component(base_image=BASE_IMAGE)
def failure_handler(match_id: str, status: PipelineTaskFinalStatus, config_file_path: str) -> str:
    from costco.leadmgmt.components.failure_handler import failure_handler

    return failure_handler(match_id=match_id, status=status, config_file_path=config_file_path)


@dsl.component(base_image=BASE_IMAGE)
def fuzzy_matching(file_classified_path: str, config_file_path: str) -> str:
    from costco.leadmgmt.components.fuzzy_matching_sql import fuzzy_matching
    return fuzzy_matching(file_classified_path, config_file_path=config_file_path)


@dsl.component(base_image=BASE_IMAGE)
def delete_temp_files_from_gcs(match_id: str, file_path: str, config_file_path: str):
    from costco.leadmgmt.components.temporary_file_deletion import delete_temp_files_from_gcs
    return delete_temp_files_from_gcs(match_id=match_id, file_path=file_path, config_file_path=config_file_path)


@dsl.component(base_image=BASE_IMAGE)
def primary_classification(file_a_path: str, file_b_path: str, match_id: str, config_file_path: str) -> str:
    from costco.leadmgmt.components.primary_matching import primary_classification

    return primary_classification(file_a_path=file_a_path, file_b_path=file_b_path, match_id=match_id,
                                  config_file_path=config_file_path)


@dsl.component(base_image=BASE_IMAGE)
def update_cloud_sql(file_path: str, config_file_path: str):
    from costco.leadmgmt.components.update_source_data import update_cloud_sql
    return update_cloud_sql(config_file_path=config_file_path, file_path=file_path)


@dsl.component(base_image=BASE_IMAGE)
def embedding_generation_leads(file_leads: str, config_file_path: str):
    from costco.leadmgmt.components.vector_db_loading_leads import embedding_generation
    return embedding_generation(file_leads=file_leads, config_file_path=config_file_path)


@dsl.component(base_image=BASE_IMAGE)
def embedding_generation_pos(file_pos: str, config_file_path: str):
    from costco.leadmgmt.components.vector_db_loading_pos import embedding_generation
    return embedding_generation(file_pos=file_pos, config_file_path=config_file_path)


@dsl.component(base_image=BASE_IMAGE)
def update_servicenow(config_file_path: str, file_path: str):
    from costco.leadmgmt.components.update_servicenow import update_servicenow
    return update_servicenow(config_file_path=config_file_path, file_path=file_path)


@dsl.pipeline(name=os.environ.get("PIPELINE_NAME"))
def my_pipeline(
        match_id: str,
        config_file_path: str
):
    print("pipeline execution started")

    track_failure_status = failure_handler(
        match_id=match_id,
        config_file_path=config_file_path
    )

    track_failure_status.set_display_name('Update failure status')

    with dsl.ExitHandler(exit_task=track_failure_status):
        # Step 1: Load and preprocess the files from GCS
        # file_leads_preprocessed = load_and_preprocess_data_cloud_sql(
        #     base_name="leads",
        #     config_file_path=config_file_path
        # )

        # file_pos_preprocessed = load_and_preprocess_data_cloud_sql(
        #     base_name="pos",
        #     config_file_path=config_file_path
        # )

        # file_leads_preprocessed.set_caching_options(False).set_cpu_limit(CPU_LIMIT).set_memory_limit(MEMORY_LIMIT)
        # file_pos_preprocessed.set_caching_options(False).set_cpu_limit(CPU_LIMIT).set_memory_limit(MEMORY_LIMIT)

        # # file_leads_preprocessed.set_caching_options(False)
        # # file_pos_preprocessed.set_caching_options(False)

        # # step 2: Primary classification

        # task_1 = primary_classification(
        #     file_a_path=file_leads_preprocessed.output,  # Use the output of the preprocessed file task
        #     file_b_path=file_pos_preprocessed.output,  # Use the output of the preprocessed file task
        #     match_id=match_id,
        #     config_file_path=config_file_path
        # )
        # task_1.set_caching_options(False).set_cpu_limit(CPU_LIMIT).set_memory_limit(MEMORY_LIMIT)
        # # task_1.set_caching_options(False)

        # # step 3: Embedding Generation

        # # Embedding generation leads
        # task_2 = embedding_generation_leads(
        #     file_leads=file_leads_preprocessed.output,
        #     config_file_path=config_file_path)
        # task_2.set_caching_options(False).set_cpu_limit(CPU_LIMIT_EG).set_memory_limit(MEMORY_LIMIT_EG)
        # # task_2.set_caching_options(False)

        # # Embedding generation pos
        # task_3 = embedding_generation_pos(
        #     file_pos=file_pos_preprocessed.output,
        #     config_file_path=config_file_path)
        # task_3.set_caching_options(False).set_cpu_limit(CPU_LIMIT_EG).set_memory_limit(MEMORY_LIMIT_EG)
        # # task_3.set_caching_options(False)

        # # step 4: fuzzy matching
        # task_4 = fuzzy_matching(
        #     file_classified_path=task_1.output,
        #     config_file_path=config_file_path).after(task_2, task_3)

        # task_4.set_caching_options(False).set_cpu_limit(CPU_LIMIT).set_memory_limit(MEMORY_LIMIT)
        # # task_4.set_caching_options(False)

        # # step 5: Update source data
        # task_5 = update_cloud_sql(
        #     config_file_path=config_file_path,
        #     file_path=task_4.output).after(task_4)

        # task_5.set_caching_options(False).set_cpu_limit(CPU_LIMIT).set_memory_limit(MEMORY_LIMIT)
        # task_5.set_caching_options(False)

        task_6 = update_servicenow(
            config_file_path=config_file_path,
            file_path=task_4.output
        ).after(task_4)

        task_6.set_caching_options(False).set_cpu_limit(CPU_LIMIT).set_memory_limit(MEMORY_LIMIT)

        # Step 7: Cleanup the temporary files
        # task_7 = delete_temp_files_from_gcs(
        #     match_id=match_id,
        #     file_path=task_4.output,
        #     config_file_path=config_file_path).after(task_5, task_6)

        #task_7.set_caching_options(False).set_cpu_limit(CPU_LIMIT).set_memory_limit(MEMORY_LIMIT)
       

        # Set display names for each stage
        # file_leads_preprocessed.set_display_name("Load and Preprocess Leads data")
        # file_pos_preprocessed.set_display_name("Load and Preprocess POS data")
        # task_1.set_display_name("Primary Classification")
        # task_2.set_display_name("Embedding and Leads Vector database Loading")
        # task_3.set_display_name("Embedding and PoS Vector database Loading")
        # task_4.set_display_name("Similarity Search")
        # task_5.set_display_name("Update matching results to cloud sql")
        task_6.set_display_name("Update the matched results to ServiceNow")
        #task_7.set_display_name("Cleanup Temporary Files")


