import os
from flask import Flask
from google.cloud import workflows_v1
from google.cloud.workflows import executions_v1
#from google.cloud.workflows.executions_v1.types import executions

app = Flask(__name__)

@app.route('/')
def trigger_workflow():
    project_id = os.environ.get("PROJECT_ID")
    location = "us-central1"
    workflow_id = "snow_sync_workflow"  # 👈 update with your workflow name

    # Create Workflows Executions client
    execution_client = executions_v1.ExecutionsClient()

    # Create Workflows client
    workflows_client = workflows_v1.WorkflowsClient()


    # Build the workflow resource path
    parent = workflows_client.workflow_path(project_id, location, workflow_id)

    # Create execution request (no arguments)
    execution = execution_client.create_execution(request={"parent": parent})
    print(f"Workflow execution started: {execution.name}")

    # To get execution details:
    updated_execution = execution_client.get_execution(name=execution.name)
    print(f"Execution state: {updated_execution.state}")
    print(f"Execution result: {updated_execution.result}")

    return f"Workflow execution started: {execution.name}", 200


if __name__ == '__main__':
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
