import { GoogleAuth } from "google-auth-library";
import { Storage } from "@google-cloud/storage";
import type {
  GcpExecution,
  GhRun,
  WarehouseStats,
  CloudRunJobExecution,
} from "./types";

const auth = new GoogleAuth({
  scopes: ["https://www.googleapis.com/auth/cloud-platform"],
});
const storage = new Storage();

function config() {
  return {
    project: process.env.GOOGLE_CLOUD_PROJECT || "ctoteam",
    region: process.env.REGION || "us-central1",
    workflowName: process.env.WORKFLOW_NAME || "lead_match_workflow",
    bucket: process.env.REPORT_BUCKET || "lead-match-ctoteam",
    reportPrefix: "reports/lead_match",
    cloudRunJobs: [
      "lead-match-lead-embeddings",
      "lead-match-pos-embeddings",
      "lead-match-fuzzy-match",
      "lead-match-report",
    ] as const,
    lookbackHours: 24,
  };
}

function humanSeconds(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  if (h > 0) return `${h}h ${m}m ${rem}s`;
  if (m > 0) return `${m}m ${rem}s`;
  return `${rem}s`;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function safeJsonParse(value: unknown): any {
  if (typeof value !== "string") return {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

export async function fetchWorkflowExecutions(): Promise<GcpExecution[]> {
  try {
    const { project, region, workflowName, lookbackHours, bucket } = config();
    const cutoff = new Date(
      Date.now() - lookbackHours * 3600 * 1000,
    ).toISOString();
    const parent = `projects/${project}/locations/${region}/workflows/${workflowName}`;
    const url = `https://workflowexecutions.googleapis.com/v1/${parent}/executions`;
    const client = await auth.getClient();
    const response = await client.request<{
      executions?: Array<Record<string, unknown>>;
    }>({
      url,
      params: {
        filter: `createTime>="${cutoff}"`,
        pageSize: 50,
        view: "FULL",
      },
    });

    const rawExecs = response.data.executions || [];
    const now = Date.now();

    return rawExecs.map((raw) => {
      const argument = safeJsonParse(raw.argument);
      const startTime = String(raw.startTime || "");
      const endTime = String(raw.endTime || "");
      const startMs = startTime ? Date.parse(startTime) : NaN;
      const endMs = endTime ? Date.parse(endTime) : now;
      const elapsedSeconds =
        !isNaN(startMs) ? Math.floor((endMs - startMs) / 1000) : null;

      const currentSteps = (
        (raw.status as Record<string, unknown>)?.currentSteps || []
      ) as Array<{ routine?: string; step?: string }>;
      const current = currentSteps[0] || {};

      const reportPrefix = String(argument.reportPrefix || "");
      const reportBucket = String(
        argument.reportBucket || bucket,
      );
      const reportUriPrefix = reportPrefix
        ? `gs://${reportBucket}/${reportPrefix}`
        : "";

      return {
        name: String(raw.name || ""),
        executionId: String(raw.name || "")
          .split("/")
          .pop() || "",
        state: String(raw.state || "UNKNOWN"),
        startTime,
        endTime,
        duration: String(raw.duration || ""),
        elapsedSeconds,
        elapsedHuman: humanSeconds(elapsedSeconds),
        warehouse: String(argument.warehouse || "unknown"),
        matchRunId: String(argument.matchRunId || ""),
        reportBucket,
        reportPrefix,
        reportUriPrefix,
        reportUris: reportUriPrefix
          ? {
              summary: `${reportUriPrefix}/summary.json`,
              matches: `${reportUriPrefix}/matches.csv`,
              report: `${reportUriPrefix}/report.md`,
            }
          : undefined,
        currentStep: current.step || "",
        currentRoutine: current.routine || "",
        currentSteps,
        error: raw.error || undefined,
      };
    });
  } catch (err) {
    console.error("fetchWorkflowExecutions failed:", err);
    return [];
  }
}

export async function fetchCloudRunJobExecutions(
  jobName: string,
): Promise<CloudRunJobExecution[]> {
  try {
    const { project, region } = config();
    const parent = `projects/${project}/locations/${region}/jobs/${jobName}`;
    const url = `https://run.googleapis.com/v2/${parent}/executions`;
    const client = await auth.getClient();
    const response = await client.request<{
      executions?: Array<Record<string, unknown>>;
    }>({
      url,
      params: { pageSize: 10 },
    });

    const rawExecs = response.data.executions || [];
    return rawExecs.map((raw) => {
      const succeededCount = Number(raw.succeededCount || 0);
      const failedCount = Number(raw.failedCount || 0);
      const runningCount = Number(raw.runningCount || 0);

      const conditions = (raw.conditions || []) as Array<{
        type?: string;
        status?: string;
        reason?: string;
        message?: string;
      }>;
      const terminal =
        conditions.find(
          (c) =>
            c.type === "Completed" ||
            c.type === "Ready" ||
            c.type === "Succeeded",
        ) || {};

      let state = "UNKNOWN";
      if (runningCount > 0) state = "RUNNING";
      else if (failedCount > 0) state = "FAILED";
      else if (succeededCount > 0 || terminal.status === "True")
        state = "SUCCEEDED";
      else if (terminal.status === "False") state = "FAILED";

      const template = (raw.template || {}) as Record<string, unknown>;

      return {
        name: String(raw.name || ""),
        job: jobName,
        state,
        createTime: String(raw.createTime || ""),
        startTime: String(raw.startTime || ""),
        completionTime: String(raw.completionTime || ""),
        succeededCount,
        failedCount,
        runningCount,
        taskCount: (raw.taskCount ?? template.taskCount ?? "") as
          | number
          | string,
        parallelism: (raw.parallelism ?? template.parallelism ?? "") as
          | number
          | string,
        conditionType: terminal.type || "",
        conditionReason: terminal.reason || "",
        conditionMessage: terminal.message || "",
      };
    });
  } catch (err) {
    console.error(`fetchCloudRunJobExecutions(${jobName}) failed:`, err);
    return [];
  }
}

export async function fetchAllCloudRunJobs(): Promise<{
  cloudRunJobs: Record<string, CloudRunJobExecution[]>;
  latestCloudRunJobs: Record<string, CloudRunJobExecution | null>;
}> {
  const { cloudRunJobs: jobNames } = config();
  const results = await Promise.all(
    jobNames.map((name) => fetchCloudRunJobExecutions(name)),
  );

  const cloudRunJobs: Record<string, CloudRunJobExecution[]> = {};
  const latestCloudRunJobs: Record<string, CloudRunJobExecution | null> = {};

  jobNames.forEach((name, i) => {
    cloudRunJobs[name] = results[i];
    latestCloudRunJobs[name] = results[i][0] || null;
  });

  return { cloudRunJobs, latestCloudRunJobs };
}

export async function fetchWarehouseStats(): Promise<{
  available: boolean;
  warehouses: Record<string, WarehouseStats>;
}> {
  try {
    const { bucket, reportPrefix, project } = config();
    const prefix = `${reportPrefix}/${project}/`;
    const b = storage.bucket(bucket);

    const [, , apiResponse] = await b.getFiles({
      prefix,
      delimiter: "/",
      autoPaginate: false,
    });
    const prefixes: string[] =
      (apiResponse as { prefixes?: string[] })?.prefixes || [];

    const warehouseDirs = prefixes.map((p: string) =>
      p.replace(/\/$/, "").split("/").pop()!,
    );

    const warehouses: Record<string, WarehouseStats> = {};

    await Promise.all(
      warehouseDirs.map(async (wh) => {
        try {
          const whPrefix = `${prefix}${wh}/`;
          const [, , whApiResp] = await b.getFiles({
            prefix: whPrefix,
            delimiter: "/",
            autoPaginate: false,
          });
          const runDirs: string[] = (
            (whApiResp as { prefixes?: string[] })?.prefixes || []
          )
            .map((p: string) => p.replace(/\/$/, ""))
            .sort();

          if (runDirs.length === 0) {
            warehouses[wh] = { has_reports: false, latest_run_id: null };
            return;
          }

          const latestRunDir = runDirs[runDirs.length - 1];
          const latestRunId = latestRunDir.split("/").pop() || "";
          const summaryPath = `${latestRunDir}/summary.json`;

          try {
            const [buffer] = await b.file(summaryPath).download();
            const summary = JSON.parse(buffer.toString("utf8"));
            const leadRows = Number(summary.lead_rows || 0);
            const leadEmbRows = Number(summary.lead_embedding_rows || 0);
            const posRows = Number(summary.pos_rows || 0);
            const posEmbRows = Number(summary.pos_embedding_rows || 0);

            warehouses[wh] = {
              has_reports: true,
              latest_run_id: latestRunId,
              lead_count: leadRows,
              lead_embedding_count: leadEmbRows,
              lead_embedding_pct: Math.round(
                (leadEmbRows / Math.max(leadRows, 1)) * 1000,
              ) / 10,
              pos_count: posRows,
              pos_embedding_count: posEmbRows,
              pos_embedding_pct: Math.round(
                (posEmbRows / Math.max(posRows, 1)) * 1000,
              ) / 10,
              match_count: Number(summary.match_rows || 0),
              match_types: summary.match_type_counts || {},
              lifecycle_states: summary.lifecycle_state_counts || {},
              primary_transactions: Number(
                summary.primary_transaction_count || 0,
              ),
              embedding_model: String(summary.embedding_model || ""),
              generated_at: String(summary.generated_at || ""),
            };
          } catch {
            warehouses[wh] = {
              has_reports: true,
              latest_run_id: latestRunId,
              summary_missing: true,
            };
          }
        } catch {
          warehouses[wh] = { has_reports: false, latest_run_id: null };
        }
      }),
    );

    return { available: true, warehouses };
  } catch (err) {
    console.error("fetchWarehouseStats failed:", err);
    return { available: false, warehouses: {} };
  }
}

export async function loadCachedGithubRuns(): Promise<GhRun[]> {
  try {
    const { bucket } = config();
    const file = storage.bucket(bucket).file("monitoring/latest.json");
    const [exists] = await file.exists();
    if (!exists) return [];
    const [buffer] = await file.download();
    const snapshot = JSON.parse(buffer.toString("utf8"));
    return snapshot.github_actions_runs || [];
  } catch {
    return [];
  }
}
