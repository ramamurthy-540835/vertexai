"""Cloud Run service entrypoint.

Endpoints:
  GET  /                              → trigger snow_sync_workflow
  GET  /health                        → liveness check 
  POST /servicenow/transaction-update → receive match/unmatch updates from ServiceNow
"""

import logging
import os

from flask import Flask, jsonify, request
from google.cloud import workflows_v1
from google.cloud.workflows import executions_v1

import config
import database
import update_pos

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def trigger_workflow():
    location = "us-central1"
    workflow_id = "snow_sync_workflow"

    execution_client = executions_v1.ExecutionsClient()
    workflows_client = workflows_v1.WorkflowsClient()
    parent = workflows_client.workflow_path(config.PROJECT_ID, location, workflow_id)

    execution = execution_client.create_execution(request={"parent": parent})
    log.info("Workflow execution started: %s", execution.name)

    return f"Workflow execution started: {execution.name}", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "success",
        "message": "Network reachable. API is healthy.",
        "service": "leadmgmt-workflow-api",
    }), 200


@app.route("/servicenow/transaction-update", methods=["POST"])
def transaction_update():
    payload = request.get_json(silent=True)
    if not payload or not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    records = payload.get("result")
    if not isinstance(records, list):
        return jsonify({"error": "'result' must be an array"}), 400

    if not records:
        return jsonify({"processed": 0, "succeeded": 0, "failed": 0, "errors": []}), 200

    result = update_pos.process_batch(database.get_engine(), records)
    return jsonify(result), (207 if result["errors"] else 200)


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))