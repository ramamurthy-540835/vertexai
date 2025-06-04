import sys

from costco.leadmgmt.pipeline_manager.compile_pipeline import compile_and_upload_pipeline

if __name__ == "__main__":

    if len(sys.argv) != 3:
        print("error")
        raise Exception("Message")

    pipeline_name = sys.argv[1]
    registry_url = sys.argv[2]
    compile_and_upload_pipeline(pipeline_name, registry_url)
    print("Pipeline upload  successfully.")