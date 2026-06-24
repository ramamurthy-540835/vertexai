import { AnnotationForm } from "@/components/AnnotationForm";
import { AnalysisPanel } from "@/components/AnalysisPanel";
import { latestSummary, searchMatches } from "@/lib/reports";

export const dynamic = "force-dynamic";

type Params = {
  warehouse?: string;
  run_id?: string;
  lead_id?: string;
  pos_id?: string;
  match_type?: string;
  lifecycle_state?: string;
  min_score?: string;
};

function scoreBadge(score: string | undefined) {
  const n = Number(score || 0);
  if (n >= 95) return "badge badge-success";
  return "badge";
}

function typeBadge(matchType: string | undefined) {
  if (matchType === "Fuzzy" || matchType === "Manual Review") return "badge badge-warning";
  return "badge";
}

export default async function SearchPage({ searchParams }: { searchParams: Promise<Params> }) {
  const params = await searchParams;
  const warehouse = params.warehouse || "115";
  const latest = await latestSummary(warehouse);
  const result = await searchMatches({
    warehouse,
    runId: params.run_id || latest?.match_run_id,
    leadId: params.lead_id,
    posId: params.pos_id,
    matchType: params.match_type,
    lifecycleState: params.lifecycle_state,
    minScore: params.min_score ? Number(params.min_score) : undefined,
    limit: 100,
  });
  const runId = result.run?.runId || latest?.match_run_id || "";
  const firstRow = result.rows[0];

  return (
    <div className="search-layout">
      <aside className="card">
        <h2>Filters</h2>
        <form className="form form-stack">
          <div className="field">
            <label>Warehouse</label>
            <input name="warehouse" defaultValue={warehouse} />
          </div>
          <div className="field">
            <label>Run ID</label>
            <input name="run_id" defaultValue={runId} />
          </div>
          <div className="field">
            <label>Lead ID</label>
            <input name="lead_id" defaultValue={params.lead_id || ""} />
          </div>
          <div className="field">
            <label>POS ID</label>
            <input name="pos_id" defaultValue={params.pos_id || ""} />
          </div>
          <div className="field">
            <label>Match Type</label>
            <select name="match_type" defaultValue={params.match_type || ""}>
              <option value="">Any</option>
              <option value="Fuzzy">Fuzzy</option>
              <option value="Manual Review">Manual Review</option>
            </select>
          </div>
          <div className="field">
            <label>Min Score</label>
            <input name="min_score" defaultValue={params.min_score || ""} />
          </div>
          <button className="button primary" type="submit" style={{ width: "100%" }}>
            Filter
          </button>
        </form>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
          <a
            className="button outline-red"
            href={`/api/results/download?warehouse=${warehouse}&run_id=${runId}&type=csv`}
            style={{ textAlign: "center" }}
          >
            Download CSV
          </a>
          <a
            className="button"
            href={`/api/results/download?warehouse=${warehouse}&run_id=${runId}&type=summary`}
            style={{ textAlign: "center" }}
          >
            Download Summary
          </a>
          <a
            className="button"
            href={`/api/results/download?warehouse=${warehouse}&run_id=${runId}&type=markdown`}
            style={{ textAlign: "center" }}
          >
            Download Markdown
          </a>
        </div>
      </aside>

      <section>
        <h1>Search Match Results</h1>
        <p className="subtitle">
          Showing {result.rows.length.toLocaleString()} of{" "}
          {result.total?.toLocaleString() || 0} matching rows.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Lead</th>
                <th>POS</th>
                <th>Warehouse</th>
                <th>Type</th>
                <th>State</th>
                <th>Primary</th>
                <th>Score</th>
                <th>Lead Business</th>
                <th>POS Business</th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row) => (
                <tr key={`${row.lead_id}-${row.pos_id}`}>
                  <td>{row.lead_id}</td>
                  <td>{row.pos_id}</td>
                  <td>{row.warehouse_number}</td>
                  <td>
                    <span className={typeBadge(row.match_type)}>{row.match_type}</span>
                  </td>
                  <td>{row.lifecycle_state}</td>
                  <td>{row.primary_transaction}</td>
                  <td>
                    <span className={scoreBadge(row.final_score)}>{row.final_score}</span>
                  </td>
                  <td>{row.lead_business_name}</td>
                  <td>{row.pos_business_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {runId ? (
          <AnnotationForm runId={runId} leadId={firstRow?.lead_id} posId={firstRow?.pos_id} />
        ) : null}
      </section>

      {runId ? (
        <aside className="card">
          <AnalysisPanel warehouse={warehouse} runId={runId} />
        </aside>
      ) : null}
    </div>
  );
}
