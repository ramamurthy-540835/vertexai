import { graphData, latestSummary } from "@/lib/reports";

export const dynamic = "force-dynamic";

export default async function GraphPage({
  searchParams,
}: {
  searchParams: Promise<{ warehouse?: string; run_id?: string; lead_id?: string }>;
}) {
  const params = await searchParams;
  const warehouse = params.warehouse || "115";
  const latest = await latestSummary(warehouse);
  const runId = params.run_id || latest?.match_run_id;
  const graph = await graphData({
    warehouse,
    runId,
    leadId: params.lead_id,
    limit: 80,
  });

  return (
    <section className="card">
      <h1>Graph Explorer</h1>
      <p className="subtitle">
        Neo4j-style lead-to-POS exploration from the CSV report. This is a lightweight graph view
        over GCS data, not a separate graph database dependency.
      </p>
      <form className="form">
        <div className="field">
          <label>Warehouse</label>
          <input name="warehouse" defaultValue={warehouse} />
        </div>
        <div className="field">
          <label>Run ID</label>
          <input name="run_id" defaultValue={runId || ""} />
        </div>
        <div className="field">
          <label>Lead ID</label>
          <input name="lead_id" defaultValue={params.lead_id || ""} />
        </div>
        <button className="button primary" type="submit">
          Explore
        </button>
      </form>

      <div className="card graph">
        {graph.nodes.map((node, index) => {
          const column = index % 4;
          const row = Math.floor(index / 4);
          return (
            <div
              className={`node ${node.type}`}
              key={node.id}
              style={{
                left: `${8 + column * 23}%`,
                top: `${8 + row * 90}px`,
              }}
              title={node.id}
            >
              <strong>{node.label}</strong>
              <br />
              {node.name}
              <br />
              <small>{node.id}</small>
            </div>
          );
        })}
      </div>

      <div className="grid">
        <div className="metric">
          <span>Nodes</span>
          <strong>{graph.nodes.length}</strong>
        </div>
        <div className="metric">
          <span>Edges</span>
          <strong>{graph.edges.length}</strong>
        </div>
      </div>
    </section>
  );
}
