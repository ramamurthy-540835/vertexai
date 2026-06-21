import { Storage } from "@google-cloud/storage";

export const dynamic = "force-dynamic";

const storage = new Storage();

function bucketName() {
  return process.env.REPORT_BUCKET || "lead-match-ctoteam";
}

type GcpExecution = {
  name: string;
  state: string;
  startTime: string;
  endTime: string;
  duration: string;
  warehouse: string;
  matchRunId: string;
  currentSteps: Array<{ routine?: string; step?: string }>;
  error?: unknown;
};

type GhRun = {
  databaseId: number;
  status: string;
  conclusion: string | null;
  startedAt: string;
  displayTitle: string;
};

type WarehouseStats = {
  has_reports?: boolean;
  summary_missing?: boolean;
  lead_count?: number;
  lead_embedding_count?: number;
  lead_embedding_pct?: number;
  pos_count?: number;
  pos_embedding_count?: number;
  pos_embedding_pct?: number;
  match_count?: number;
  latest_run_id?: string | null;
};

type Snapshot = {
  generated_at: string;
  project: string;
  region: string;
  lookback_hours: number;
  warehouse_filter: string;
  active_runs: GcpExecution[];
  workflow_executions: GcpExecution[];
  github_actions_runs: GhRun[];
  warehouse_stats: {
    available: boolean;
    warehouses: Record<string, WarehouseStats>;
  };
  warehouse_run_status: Record<
    string,
    { latest_state: string; latest_start: string; current_step: string }
  >;
};

async function loadSnapshot(): Promise<Snapshot | null> {
  try {
    const file = storage.bucket(bucketName()).file("monitoring/latest.json");
    const [exists] = await file.exists();
    if (!exists) return null;
    const [buffer] = await file.download();
    return JSON.parse(buffer.toString("utf8")) as Snapshot;
  } catch {
    return null;
  }
}

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

function stateBadge(state: string) {
  const s = state.toUpperCase();
  if (s === "SUCCEEDED" || s === "completed") return "badge badge-success";
  if (s === "ACTIVE" || s === "in_progress") return "badge badge-info";
  if (s === "FAILED" || s === "failure") return "badge badge-error";
  if (s === "CANCELLED" || s === "canceled") return "badge badge-warning";
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
  const snap = await loadSnapshot();

  if (!snap) {
    return (
      <section className="card" style={{ textAlign: "center", padding: 48 }}>
        <h1>No Monitoring Data</h1>
        <p className="subtitle" style={{ margin: "12px auto" }}>
          No monitoring snapshot found at{" "}
          <code>gs://{bucketName()}/monitoring/latest.json</code>. Run the{" "}
          <strong>Lead Match Monitor</strong> GitHub Actions workflow first.
        </p>
      </section>
    );
  }

  const active = snap.active_runs || [];
  const recent = (snap.workflow_executions || []).filter(
    (e) => e.state !== "ACTIVE",
  );
  const ghRuns = snap.github_actions_runs || [];
  const whStats = snap.warehouse_stats?.available
    ? snap.warehouse_stats.warehouses
    : {};
  const whRunStatus = snap.warehouse_run_status || {};

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

      {/* Active Runs */}
      <section className="card">
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
                  <th>Current Step</th>
                  <th>Started</th>
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
                        {r.currentSteps?.[0]?.step || "running"}
                      </span>
                    </td>
                    <td>{r.startTime?.slice(0, 19)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

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
                          <td colSpan={6} style={{ color: "var(--muted)" }}>
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
                        <td>{(s.lead_count ?? 0).toLocaleString()}</td>
                        <td>{(s.lead_embedding_count ?? 0).toLocaleString()} ({s.lead_embedding_pct ?? 0}%)</td>
                        <td><ProgressBar pct={s.lead_embedding_pct ?? 0} /></td>
                        <td>{(s.pos_count ?? 0).toLocaleString()}</td>
                        <td>{(s.pos_embedding_count ?? 0).toLocaleString()} ({s.pos_embedding_pct ?? 0}%)</td>
                        <td><ProgressBar pct={s.pos_embedding_pct ?? 0} /></td>
                        <td>{(s.match_count ?? 0).toLocaleString()}</td>
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
                  <th>Started</th>
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
                    <td>{formatDuration(r.duration)}</td>
                    <td>{r.startTime?.slice(0, 19)}</td>
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
                  <th>Status</th>
                  <th>Conclusion</th>
                  <th>Title</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {ghRuns.slice(0, 15).map((r) => (
                  <tr key={r.databaseId}>
                    <td style={{ fontFamily: "monospace", fontSize: 13 }}>
                      {r.databaseId}
                    </td>
                    <td>
                      <span className={stateBadge(r.status)}>{r.status}</span>
                    </td>
                    <td>
                      <span className={stateBadge(r.conclusion || "")}>
                        {r.conclusion || "-"}
                      </span>
                    </td>
                    <td>{r.displayTitle}</td>
                    <td>{r.startedAt?.slice(0, 19)}</td>
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
