import { Storage } from "@google-cloud/storage";
import {
  fetchWorkflowExecutions,
  fetchAllCloudRunJobs,
  fetchWarehouseStats,
  loadCachedGithubRuns,
} from "./gcp";
import type { Snapshot } from "./types";

const storage = new Storage();

function bucketName() {
  return process.env.REPORT_BUCKET || "lead-match-ctoteam";
}

async function writeSnapshotToGcs(snapshot: Snapshot): Promise<void> {
  const bucket = storage.bucket(bucketName());
  const json = JSON.stringify(snapshot, null, 2);
  const timestamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d+Z$/, "Z");
  await Promise.all([
    bucket
      .file("monitoring/latest.json")
      .save(json, { contentType: "application/json" }),
    bucket
      .file(`monitoring/snapshots/${timestamp}.json`)
      .save(json, { contentType: "application/json" }),
  ]);
}

export async function buildLiveSnapshot(): Promise<Snapshot> {
  const [workflowExecs, cloudRunResult, warehouseStats, githubRuns] =
    await Promise.all([
      fetchWorkflowExecutions(),
      fetchAllCloudRunJobs(),
      fetchWarehouseStats(),
      loadCachedGithubRuns(),
    ]);

  const activeRuns = workflowExecs.filter((e) => e.state === "ACTIVE");

  const warehouseRunStatus: Snapshot["warehouse_run_status"] = {};
  const sorted = [...workflowExecs].sort(
    (a, b) =>
      new Date(b.startTime).getTime() - new Date(a.startTime).getTime(),
  );
  for (const ex of sorted) {
    const wh = ex.warehouse;
    if (wh in warehouseRunStatus) continue;
    warehouseRunStatus[wh] = {
      latest_state: ex.state,
      latest_start: ex.startTime,
      latest_end: ex.endTime || undefined,
      latest_match_run_id: ex.matchRunId,
      is_active: ex.state === "ACTIVE",
      current_step: ex.currentStep || "",
      current_routine: ex.currentRoutine || "",
      elapsed_human: ex.elapsedHuman || "",
      report_uri_prefix: ex.reportUriPrefix || "",
    } as Snapshot["warehouse_run_status"][string];
  }

  const latestJobs = cloudRunResult.latestCloudRunJobs;
  const statusSummary = {
    active_gcp_workflow_count: activeRuns.length,
    running_cloud_run_job_count: Object.values(latestJobs).filter(
      (j) => j?.state === "RUNNING",
    ).length,
    failed_cloud_run_job_count: Object.values(latestJobs).filter(
      (j) => j?.state === "FAILED",
    ).length,
    github_in_progress_count: githubRuns.filter(
      (r) => r.status === "in_progress",
    ).length,
    github_failed_recent_count: githubRuns.filter(
      (r) =>
        r.status === "completed" &&
        ["failure", "cancelled", "timed_out"].includes(r.conclusion || ""),
    ).length,
  };

  const snapshot: Snapshot = {
    generated_at: new Date().toISOString(),
    project: process.env.GOOGLE_CLOUD_PROJECT || "ctoteam",
    region: process.env.REGION || "us-central1",
    workflow_name: process.env.WORKFLOW_NAME || "lead_match_workflow",
    lookback_hours: 24,
    warehouse_filter: "all",
    status_summary: statusSummary,
    active_runs: activeRuns,
    workflow_executions: workflowExecs,
    github_actions_runs: githubRuns,
    cloud_run_jobs: cloudRunResult.cloudRunJobs,
    latest_cloud_run_jobs: latestJobs,
    warehouse_stats: warehouseStats,
    warehouse_run_status: warehouseRunStatus,
  };

  writeSnapshotToGcs(snapshot).catch((err) =>
    console.error("GCS write-back failed:", err),
  );

  return snapshot;
}
