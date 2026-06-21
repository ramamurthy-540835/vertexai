import { Storage } from "@google-cloud/storage";
import { parseCsv, type CsvRow } from "./csv";

export type ReportSummary = {
  project: string;
  schema: string;
  warehouse: string;
  match_run_id: string;
  generated_at: string;
  match_rows: number;
  lead_rows: number;
  pos_rows: number;
  primary_transaction_count: number;
  match_type_counts: Record<string, number>;
  lifecycle_state_counts: Record<string, number>;
  report_uris?: Record<string, string>;
};

export type SearchParams = {
  warehouse?: string;
  runId?: string;
  leadId?: string;
  posId?: string;
  matchType?: string;
  lifecycleState?: string;
  minScore?: number;
  limit?: number;
};

export type Annotation = {
  run_id: string;
  lead_id?: string;
  pos_id?: string;
  note: string;
  status?: string;
  author?: string;
  created_at: string;
};

const storage = new Storage();

function bucketName() {
  return process.env.REPORT_BUCKET || "lead-match-ctoteam";
}

function reportRoot() {
  return (process.env.REPORT_PREFIX || "reports/lead_match").replace(/^\/+|\/+$/g, "");
}

function safeSegment(value: string, name: string) {
  if (!/^[A-Za-z0-9_.=-]+$/.test(value)) {
    throw new Error(`Invalid ${name}`);
  }
  return value;
}

function bucket() {
  return storage.bucket(bucketName());
}

export function reportPrefix(project: string, warehouse: string, runId: string) {
  return [
    reportRoot(),
    safeSegment(project, "project"),
    safeSegment(warehouse, "warehouse"),
    safeSegment(runId, "run_id"),
  ].join("/");
}

function defaultProject() {
  return process.env.GOOGLE_CLOUD_PROJECT || "ctoteam";
}

export async function listRuns(warehouse?: string) {
  const root = reportRoot();
  const prefix = warehouse
    ? `${root}/${defaultProject()}/${safeSegment(warehouse, "warehouse")}/`
    : `${root}/`;
  const [files] = await bucket().getFiles({ prefix, autoPaginate: true });
  const summaries = files.filter((file) => file.name.endsWith("/summary.json"));

  return summaries
    .map((file) => {
      const parts = file.name.split("/");
      const runId = parts.at(-2) || "";
      const warehouseValue = parts.at(-3) || "";
      const project = parts.at(-4) || defaultProject();
      return {
        project,
        warehouse: warehouseValue,
        runId,
        object: file.name,
        updated: file.metadata.updated || file.metadata.timeCreated || "",
      };
    })
    .sort((left, right) => right.updated.localeCompare(left.updated));
}

export async function latestSummary(warehouse?: string): Promise<ReportSummary | null> {
  const [latest] = await listRuns(warehouse);
  if (!latest) {
    return null;
  }
  return readSummary(latest.project, latest.warehouse, latest.runId);
}

export async function readSummary(
  project: string,
  warehouse: string,
  runId: string,
): Promise<ReportSummary> {
  const [buffer] = await bucket()
    .file(`${reportPrefix(project, warehouse, runId)}/summary.json`)
    .download();
  return JSON.parse(buffer.toString("utf8")) as ReportSummary;
}

export async function findRun(runId: string, warehouse?: string) {
  const runs = await listRuns(warehouse);
  return runs.find((run) => run.runId === runId) || null;
}

export async function readMatches(project: string, warehouse: string, runId: string): Promise<CsvRow[]> {
  const [buffer] = await bucket()
    .file(`${reportPrefix(project, warehouse, runId)}/matches.csv`)
    .download();
  return parseCsv(buffer.toString("utf8"));
}

export async function searchMatches(params: SearchParams) {
  const run = params.runId
    ? await findRun(params.runId, params.warehouse)
    : (await listRuns(params.warehouse))[0] || null;

  if (!run) {
    return { run: null, rows: [] };
  }

  const rows = await readMatches(run.project, run.warehouse, run.runId);
  const limit = Math.min(Math.max(params.limit || 100, 1), 1000);
  const filtered = rows.filter((row) => {
    if (params.leadId && !row.lead_id?.includes(params.leadId)) return false;
    if (params.posId && !row.pos_id?.includes(params.posId)) return false;
    if (params.matchType && row.match_type !== params.matchType) return false;
    if (params.lifecycleState && row.lifecycle_state !== params.lifecycleState) return false;
    if (params.minScore && Number(row.final_score || 0) < params.minScore) return false;
    return true;
  });

  return { run, rows: filtered.slice(0, limit), total: filtered.length };
}

export async function downloadReport(
  runId: string,
  warehouse: string | undefined,
  type: "csv" | "summary" | "markdown",
) {
  const run = await findRun(runId, warehouse);
  if (!run) {
    return null;
  }
  const fileNameByType = {
    csv: "matches.csv",
    summary: "summary.json",
    markdown: "report.md",
  };
  const objectName = `${reportPrefix(run.project, run.warehouse, run.runId)}/${fileNameByType[type]}`;
  const [buffer] = await bucket().file(objectName).download();
  return { run, buffer, fileName: fileNameByType[type] };
}

export async function readAnnotations(runId: string): Promise<Annotation[]> {
  const objectName = `${reportRoot()}/annotations/${safeSegment(runId, "run_id")}.json`;
  const file = bucket().file(objectName);
  const [exists] = await file.exists();
  if (!exists) {
    return [];
  }
  const [buffer] = await file.download();
  return JSON.parse(buffer.toString("utf8")) as Annotation[];
}

export async function appendAnnotation(annotation: Omit<Annotation, "created_at">) {
  const existing = await readAnnotations(annotation.run_id);
  const next: Annotation = {
    ...annotation,
    created_at: new Date().toISOString(),
  };
  const objectName = `${reportRoot()}/annotations/${safeSegment(annotation.run_id, "run_id")}.json`;
  await bucket()
    .file(objectName)
    .save(JSON.stringify([...existing, next], null, 2) + "\n", {
      contentType: "application/json",
    });
  return next;
}

export async function graphData(params: SearchParams) {
  const result = await searchMatches({ ...params, limit: params.limit || 120 });
  const leadIds = new Set<string>();
  const posIds = new Set<string>();
  const nodes: Array<{ id: string; label: string; type: "lead" | "pos"; name: string }> = [];
  const edges = result.rows.map((row) => {
    if (row.lead_id && !leadIds.has(row.lead_id)) {
      leadIds.add(row.lead_id);
      nodes.push({
        id: row.lead_id,
        label: "Lead",
        type: "lead",
        name: row.lead_business_name || row.lead_id,
      });
    }
    if (row.pos_id && !posIds.has(row.pos_id)) {
      posIds.add(row.pos_id);
      nodes.push({
        id: row.pos_id,
        label: "POS",
        type: "pos",
        name: row.pos_business_name || row.pos_id,
      });
    }
    return {
      from: row.lead_id,
      to: row.pos_id,
      score: Number(row.final_score || 0),
      match_type: row.match_type,
      lifecycle_state: row.lifecycle_state,
      primary_transaction: row.primary_transaction,
    };
  });

  return { run: result.run, nodes, edges };
}
