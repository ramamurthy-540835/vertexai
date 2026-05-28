"""Helper for triggering Cloud Run Jobs programmatically.

Usage:
    from cloud_run_jobs import trigger_job

    operation = trigger_job(
        project_id = "my-project",
        region     = "us-central1",
        job_name   = "my-job",
        args       = ["main.py", "drive_to_gcs"],
    )
"""

import logging

from google.cloud import run_v2

log = logging.getLogger(__name__)


def trigger_job(
    project_id: str,
    region: str,
    job_name: str,
    args: list[str] | None = None,
) -> run_v2.Operation:
    """Trigger a Cloud Run Job and return the long-running operation.

    Args:
        project_id: GCP project ID.
        region:     Region the job lives in (e.g. 'us-central1').
        job_name:   Cloud Run Job name (e.g. 'drive-gcs-sync-job').
        args:       Optional list of args to pass to the container.

    Returns:
        The Operation returned by the Cloud Run Jobs API.

    Raises:
        google.api_core.exceptions.GoogleAPICallError: on API failure.
    """
    client = run_v2.JobsClient()

    job_resource = client.job_path(project_id, region, job_name)

    container_override = run_v2.RunJobRequest.Overrides.ContainerOverride(
        args=args or []
    )

    run_request = run_v2.RunJobRequest(
        name=job_resource,
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[container_override]
        ),
    )

    operation = client.run_job(request=run_request)
    log.info(
        "Cloud Run Job triggered. job=%s  args=%s  operation=%s",
        job_resource, args, operation.operation.name,
    )
    return operation