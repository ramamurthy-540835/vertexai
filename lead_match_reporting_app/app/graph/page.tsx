import { graphData, latestSummary } from "@/lib/reports";
import { ForceGraph } from "@/components/ForceGraph";

export const dynamic = "force-dynamic";

type GraphNode = { id: string; label: string; type: "lead" | "pos"; name: string };
type GraphEdge = {
  from: string;
  to: string;
  score: number;
  match_type: string;
};

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
  const nodes: GraphNode[] = graph.nodes;
  const edges: GraphEdge[] = graph.edges.map((e) => ({
    from: e.from,
    to: e.to,
    score: e.score,
    match_type: e.match_type,
  }));

  return (
    <section>
      <div className="card">
        <h1>Graph Explorer</h1>
        <p className="subtitle">
          Lead-to-POS relationship graph for warehouse {warehouse}. Showing{" "}
          {nodes.length} nodes and {edges.length} edges.
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
            <label>Lead ID (filter)</label>
            <input name="lead_id" defaultValue={params.lead_id || ""} />
          </div>
          <button className="button primary" type="submit">
            Explore
          </button>
        </form>
      </div>

      <div style={{ marginTop: 16 }}>
        <ForceGraph nodes={nodes} edges={edges} />
      </div>

      <div className="graph-legend">
        <div className="graph-legend-item">
          <span
            className="graph-legend-swatch"
            style={{ borderColor: "#005DAA", background: "#005DAA" }}
          />
          Lead
        </div>
        <div className="graph-legend-item">
          <span
            className="graph-legend-swatch"
            style={{ borderColor: "#0d9488", background: "#0d9488" }}
          />
          POS
        </div>
        <div className="graph-legend-item">
          <span
            className="graph-legend-swatch"
            style={{
              borderColor: "#0F6E56",
              background: "transparent",
              borderWidth: 2,
            }}
          />
          High-confidence edge
        </div>
        <div className="graph-legend-item">
          <span
            className="graph-legend-swatch"
            style={{
              borderColor: "#B26A00",
              background: "transparent",
              borderWidth: 2,
            }}
          />
          Fuzzy / review edge
        </div>
      </div>

      <div className="grid">
        <div className="metric">
          <span>Nodes</span>
          <strong>{nodes.length}</strong>
        </div>
        <div className="metric">
          <span>Edges</span>
          <strong>{edges.length}</strong>
        </div>
      </div>
    </section>
  );
}
