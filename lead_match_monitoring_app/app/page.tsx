import { buildLiveSnapshot } from "@/lib/snapshot";
import type { CloudRunJobExecution } from "@/lib/types";

export const dynamic = "force-dynamic";

function formatDuration(dur: string): string {
  if (!dur || !dur.endsWith("s")) return dur || "-";
  try {
    const secs = parseFloat(dur.replace("s", ""));
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = Math.floor(secs % 60);
    return h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
  } catch {
    return dur;
  }
}

function formatDateTime(value?: string): string {
  return value ? value.slice(0, 19).replace("T", " ") : "-";
}

function formatElapsed(start?: string, end?: string): string {
  if (!start) return "-";
  const startMs = Date.parse(start);
  if (Number.isNaN(startMs)) return "-";
  const endMs = end ? Date.parse(end) : Date.now();
  const seconds = Math.max(0, Math.floor((endMs - startMs) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h > 0 ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
}

function shortName(value?: string): string {
  if (!value) return "-";
  return value.split("/").pop() || value;
}

function formatCount(value?: number): string {
  return (value ?? 0).toLocaleString();
}

function statusText(value?: string | null): string {
  return value && value.length > 0 ? value : "-";
}

function stateBadge(state: string) {
  const s = state.toUpperCase();
  if (s === "SUCCEEDED" || s === "completed") return "badge badge-success";
  if (s === "ACTIVE" || s === "RUNNING" || s === "in_progress") return "badge badge-info";
  if (s === "FAILED" || s === "failure" || s === "timed_out") return "badge badge-error";
  if (s === "CANCELLED" || s === "cancelled" || s === "canceled") return "badge badge-warning";
  return "badge";
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="progress-bar">
      <div
        className={`progress-fill${pct >= 100 ? " complete" : ""}`}
        style={{ width: `${Math.min(pct, 100)}%` }}
      />
    </div>
  );
}

export default async function MonitorPage() {
  const snap = await buildLiveSnapshot();

  const active = snap.active_runs || [];
  const recent = (snap.workflow_executions || []).filter(
    (e) => e.state !== "ACTIVE",
  );
  const ghRuns = snap.github_actions_runs || [];
  const whStats = snap.warehouse_stats?.available
    ? snap.warehouse_stats.warehouses
    : {};
  const whRunStatus = snap.warehouse_run_status || {};
  const latestCloudRunJobs = snap.latest_cloud_run_jobs || {};
  const cloudRunRows = Object.keys(latestCloudRunJobs)
    .sort()
    .map((job) => latestCloudRunJobs[job])
    .filter((job): job is CloudRunJobExecution => Boolean(job));
  const summary = snap.status_summary || {};
  const snapshotAge = formatElapsed(snap.generated_at);
  const ghInProgress =
    summary.github_in_progress_count ??
    ghRuns.filter((run) => run.status === "in_progress").length;
  const runningCloudRun =
    summary.running_cloud_run_job_count ??
    cloudRunRows.filter((job) => job.state === "RUNNING").length;
  const failedCloudRun =
    summary.failed_cloud_run_job_count ??
    cloudRunRows.filter((job) => job.state === "FAILED").length;

  return (
    <>
      <div className="refresh-bar">
        <span>
          Snapshot: <strong>{snap.generated_at}</strong> &middot; Project:{" "}
          <strong>{snap.project}</strong> &middot; Lookback:{" "}
          <strong>{snap.lookback_hours}h</strong>
        </span>
        <a className="button" href="/">
          Reload
        </a>
      </div>

      <section className="grid">
        <div className="metric">
          <span>Active GCP Workflows</span>
          <strong>{summary.active_gcp_workflow_count ?? active.length}</strong>
        </div>
        <div className="metric">
          <span>Running Cloud Run Jobs</span>
          <strong>{runningCloudRun}</strong>
        </div>
        <div className={`metric${failedCloudRun > 0 ? " warning" : " success"}`}>
          <span>Failed Cloud Run Jobs</span>
          <strong>{failedCloudRun}</strong>
        </div>
        <div className="metric">
          <span>GitHub In Progress</span>
          <strong>{ghInProgress}</strong>
        </div>
        <div className="metric">
          <span>Snapshot Age</span>
          <strong className="metric-text">{snapshotAge}</strong>
        </div>
      </section>

      {/* Active Runs */}
      <section className="card section-gap">
        <h2>
          Active Runs{" "}
          <span className="pill" style={{ fontSize: 11 }}>
            {active.length}
          </span>
        </h2>
        {active.length === 0 ? (
          <p className="subtitle">No GCP workflow executions currently running.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Warehouse</th>
                  <th>Match Run ID</th>
                  <th>Workflow Step</th>
                  <th>Elapsed</th>
                  <th>Started</th>
                  <th>Execution</th>
                  <th>Report Prefix</th>
                </tr>
              </thead>
              <tbody>
                {active.map((r) => (
                  <tr key={r.name}>
                    <td>
                      <strong>{r.warehouse}</strong>
                    </td>
                    <td style={{ fontSize: 12, fontFamily: "monospace" }}>
                      {r.matchRunId || "-"}
                    </td>
                    <td>
                      <span className="badge badge-info">
                        {r.currentRoutine ? `${r.currentRoutine}.` : ""}
                        {r.currentStep || r.currentSteps?.[0]?.step || "running"}
                      </span>
                    </td>
                    <td>{r.elapsedHuman || formatElapsed(r.startTime)}</td>
                    <td>{formatDateTime(r.startTime)}</td>
                    <td className="mono">{r.executionId || shortName(r.name)}</td>
                    <td className="mono small-cell">{r.reportUriPrefix || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Cloud Run Jobs */}
      {cloudRunRows.length > 0 && (
        <section className="card section-gap">
          <h2>Latest Cloud Run Job Executions</h2>
          <p className="subtitle">
            Latest execution per semantic job. This helps separate lead embeddings,
            POS embeddings, fuzzy matching, and report generation bottlenecks.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>State</th>
                  <th>Running</th>
                  <th>Succeeded</th>
                  <th>Failed</th>
                  <th>Started</th>
                  <th>Completed</th>
                  <th>Execution</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {cloudRunRows.map((job) => (
                  <tr key={`${job.job}-${job.name}`}>
                    <td><strong>{job.job}</strong></td>
                    <td>
                      <span className={stateBadge(job.state)}>{job.state}</span>
                    </td>
                    <td>{job.runningCount ?? 0}</td>
                    <td>{job.succeededCount ?? 0}</td>
                    <td>{job.failedCount ?? 0}</td>
                    <td>{formatDateTime(job.startTime || job.createTime)}</td>
                    <td>{formatDateTime(job.completionTime)}</td>
                    <td className="mono">{shortName(job.name)}</td>
                    <td className="small-cell">
                      {job.conditionReason || job.conditionMessage || "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Warehouse Stats */}
      {Object.keys(whStats).length > 0 && (
        <section className="card section-gap">
          <h2>Warehouse Pipeline Stats</h2>
          <p className="subtitle">
            Sourced from GCS report summaries. Shows latest completed run data per
            warehouse.
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>WH</th>
                  <th>Leads</th>
                  <th>Lead Embeds</th>
                  <th style={{ minWidth: 100 }}></th>
                  <th>POS</th>
                  <th>POS Embeds</th>
                  <th style={{ minWidth: 100 }}></th>
                  <th>Matches</th>
                  <th>Latest Report</th>
                  <th>Run Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(whStats)
                  .sort((a, b) => {
                    const na = parseInt(a, 10);
                    const nb = parseInt(b, 10);
                    if (!isNaN(na) && !isNaN(nb)) return na - nb;
                    return a.localeCompare(b);
                  })
                  .map((wh) => {
                    const s = whStats[wh];
                    const runSt = whRunStatus[wh];
                    const state = runSt?.latest_state || "Never run";

                    if (s.summary_missing || !s.has_reports) {
                      return (
                        <tr key={wh}>
                          <td><strong>{wh}</strong></td>
                          <td colSpan={7} style={{ color: "var(--muted)" }}>
                            {s.summary_missing ? "Summary file missing" : "No reports"}
                          </td>
                          <td>-</td>
                          <td><span className={stateBadge(state)}>{state}</span></td>
                        </tr>
                      );
                    }

                    return (
                      <tr key={wh}>
                        <td><strong>{wh}</strong></td>
                        <td>{formatCount(s.lead_count)}</td>
                        <td>{formatCount(s.lead_embedding_count)} ({s.lead_embedding_pct ?? 0}%)</td>
                        <td><ProgressBar pct={s.lead_embedding_pct ?? 0} /></td>
                        <td>{formatCount(s.pos_count)}</td>
                        <td>{formatCount(s.pos_embedding_count)} ({s.pos_embedding_pct ?? 0}%)</td>
                        <td><ProgressBar pct={s.pos_embedding_pct ?? 0} /></td>
                        <td>
                          <strong>{formatCount(s.match_count)}</strong>
                          {s.primary_transactions !== undefined && (
                            <div className="muted tiny">
                              Primary tx: {formatCount(s.primary_transactions)}
                            </div>
                          )}
                        </td>
                        <td className="small-cell">
                          <div className="mono">{s.latest_run_id || "-"}</div>
                          <div className="muted tiny">
                            {formatDateTime(s.generated_at)}
                          </div>
                          {s.embedding_model && (
                            <div className="muted tiny">{s.embedding_model}</div>
                          )}
                        </td>
                        <td><span className={stateBadge(state)}>{state}</span></td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Recent GCP Executions */}
      {recent.length > 0 && (
        <section className="card section-gap">
          <h2>
            Recent GCP Executions{" "}
            <span className="pill" style={{ fontSize: 11 }}>
              {recent.length}
            </span>
          </h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Warehouse</th>
                  <th>State</th>
                  <th>Match Run ID</th>
                  <th>Duration</th>
                  <th>Step</th>
                  <th>Started</th>
                  <th>Report Prefix</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {recent.slice(0, 15).map((r) => (
                  <tr key={r.name}>
                    <td>{r.warehouse}</td>
                    <td>
                      <span className={stateBadge(r.state)}>{r.state}</span>
                    </td>
                    <td style={{ fontSize: 12, fontFamily: "monospace" }}>
                      {r.matchRunId || "-"}
                    </td>
                    <td>{formatDuration(r.duration) || r.elapsedHuman || "-"}</td>
                    <td>{r.currentStep || "-"}</td>
                    <td>{formatDateTime(r.startTime)}</td>
                    <td className="mono small-cell">{r.reportUriPrefix || "-"}</td>
                    <td className="small-cell">
                      {r.error ? JSON.stringify(r.error).slice(0, 180) : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* GitHub Actions Runs */}
      {ghRuns.length > 0 && (
        <section className="card section-gap">
          <h2>
            GitHub Actions Runs{" "}
            <span className="pill" style={{ fontSize: 11 }}>
              {ghRuns.length}
            </span>
          </h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Run ID</th>
                  <th>Warehouse</th>
                  <th>Status</th>
                  <th>Conclusion</th>
                  <th>Step Progress</th>
                  <th>Current Step</th>
                  <th>Last/Failed Step</th>
                  <th>Started</th>
                  <th>Run</th>
                </tr>
              </thead>
              <tbody>
                {ghRuns.slice(0, 15).map((r) => (
                  <tr key={r.databaseId}>
                    <td style={{ fontFamily: "monospace", fontSize: 13 }}>
                      {r.databaseId}
                    </td>
                    <td><strong>{r.warehouse || "-"}</strong></td>
                    <td>
                      <span className={stateBadge(r.status)}>{r.status}</span>
                    </td>
                    <td>
                      <span className={stateBadge(r.conclusion || "")}>
                        {statusText(r.conclusion)}
                      </span>
                    </td>
                    <td>
                      {(r.completedStepCount ?? 0)}/{r.totalStepCount ?? 0}
                    </td>
                    <td>{r.currentStep || "-"}</td>
                    <td className="small-cell">
                      {r.failedStep ? (
                        <span className="badge badge-error">{r.failedStep}</span>
                      ) : (
                        r.lastCompletedStep || "-"
                      )}
                    </td>
                    <td>{formatDateTime(r.startedAt)}</td>
                    <td>
                      {r.url ? (
                        <a href={r.url} target="_blank" rel="noreferrer">
                          Open
                        </a>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
