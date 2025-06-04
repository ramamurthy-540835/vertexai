# compile_pipeline.py
import kfp
from kfp import dsl
from kfp.registry import RegistryClient
import os
from datetime import datetime
from costco.leadmgmt.pipeline_manager.pipeline_definition import my_pipeline  # import the pipeline & CONFIG from a shared module

def compile_and_upload_pipeline(pipeline_name,registry_url):


    pipeline_path = f'''{pipeline_name}.yaml'''
    print(pipeline_path)
    print("compiling pipline")
    # Compile the pipeline to a YAML file
    kfp.compiler.Compiler().compile(my_pipeline, pipeline_path)
    print("compiling pipline completed")
    # Upload to Vertex AI pipeline registry

    client = RegistryClient(host=registry_url)
    version = f"v{datetime.now().strftime('%Y%m%d%H%M')}"

    templateName, versionName = client.upload_pipeline(
        file_name=pipeline_path,
        tags=[version, "latest"],
        extra_headers={"description": "lead matching pipeline template."}
    )
    full_template_path = f"{registry_url}/{templateName}/latest"
    print(f"Pipeline uploaded to: {full_template_path}")
