import { graphData, latestSummary } from "@/lib/reports";
import { getDriver, getDatabase } from "@/lib/neo4j";

export const dynamic = "force-dynamic";

type GraphNode = { id: string; label: string; type: "lead" | "pos"; name: string };
type GraphEdge = {
  from: string;
  to: string;
  score: number;
  match_type: string;
  lifecycle_state?: string;
  primary_transaction?: string;
};

async function queryNeo4j(warehouse: string) {
  const driver = getDriver();
  if (!driver) return null;

  const session = driver.session({ database: getDatabase() });
  try {
    const res = await session.executeRead((tx) =>
      tx.run(
        `MATCH (l:Lead)-[m:MATCHED_TO]->(p:Pos)-[:AT_WAREHOUSE]->(w:Warehouse {warehouseId: $wh})
         RETURN l, m, p, w LIMIT 200`,
        { wh: warehouse },
      ),
    );

    const leadIds = new Set<string>();
    const posIds = new Set<string>();
    const nodes: GraphNode[] = [];
    const edges: GraphEdge[] = [];

    for (const record of res.records) {
      const l = record.get("l").properties;
      const p = record.get("p").properties;
      const m = record.get("m").properties;

      if (!leadIds.has(l.leadId)) {
        leadIds.add(l.leadId);
        nodes.push({ id: l.leadId, label: "Lead", type: "lead", name: l.name || l.leadId });
      }
      if (!posIds.has(p.posId)) {
        posIds.add(p.posId);
        nodes.push({ id: p.posId, label: "POS", type: "pos", name: p.posId });
      }

      edges.push({
        from: l.leadId,
        to: p.posId,
        score: typeof m.finalScore === "number" ? m.finalScore : Number(m.finalScore || 0),
        match_type: m.matchType || "",
      });
    }

    return { nodes, edges, source: "neo4j" as const };
  } catch {
    return null;
  } finally {
    await session.close();
  }
}

export default async function GraphPage({
  searchParams,
}: {
  searchParams: Promise<{ warehouse?: string; run_id?: string; lead_id?: string }>;
}) {
  const params = await searchParams;
  const warehouse = params.warehouse || "115";

  let nodes: GraphNode[] = [];
  let edges: GraphEdge[] = [];
  let source: "neo4j" | "csv" = "csv";

  const neo4jResult = await queryNeo4j(warehouse);
  if (neo4jResult) {
    nodes = neo4jResult.nodes;
    edges = neo4jResult.edges;
    source = "neo4j";
  } else {
    const latest = await latestSummary(warehouse);
    const runId = params.run_id || latest?.match_run_id;
    const graph = await graphData({ warehouse, runId, leadId: params.lead_id, limit: 80 });
    nodes = graph.nodes;
    edges = graph.edges;
  }

  return (
    <section>
      <div className="card">
        <h1>Graph Explorer</h1>
        <p className="subtitle">
          Lead-to-POS relationship graph for warehouse {warehouse}.
          {source === "neo4j"
            ? " Powered by Neo4j."
            : " Reading from CSV reports."}
        </p>

        {source === "csv" && (
          <div className="banner banner-warning">
            <strong>Neo4j not configured.</strong> Set NEO4J_URI, NEO4J_USER, and NEO4J_PASSWORD
            to enable the graph database backend. Showing CSV-derived graph.
          </div>
        )}

        <form className="form">
          <div className="field">
            <label>Warehouse</label>
            <input name="warehouse" defaultValue={warehouse} />
          </div>
          {source === "csv" && (
            <>
              <div className="field">
                <label>Run ID</label>
                <input name="run_id" defaultValue={params.run_id || ""} />
              </div>
              <div className="field">
                <label>Lead ID</label>
                <input name="lead_id" defaultValue={params.lead_id || ""} />
              </div>
            </>
          )}
          <button className="button primary" type="submit">
            Explore
          </button>
        </form>
      </div>

      <div className="card" style={{ marginTop: 16, padding: 0, overflow: "hidden" }}>
        <div className="graph">
          {nodes.map((node, index) => {
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
                <small style={{ color: "var(--muted)" }}>{node.id}</small>
              </div>
            );
          })}
        </div>
      </div>

      <div className="graph-legend">
        <div className="graph-legend-item">
          <span className="graph-legend-swatch" style={{ borderColor: "var(--costco-blue)", background: "rgba(0,93,170,0.06)" }} />
          Lead
        </div>
        <div className="graph-legend-item">
          <span className="graph-legend-swatch" style={{ borderColor: "#0d9488", background: "rgba(13,148,136,0.06)" }} />
          POS
        </div>
        <div className="graph-legend-item">
          <span className="graph-legend-swatch" style={{ borderColor: "#6b7280", background: "rgba(107,114,128,0.06)" }} />
          Warehouse
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
        <div className="metric">
          <span>Source</span>
          <strong style={{ fontSize: 20, textTransform: "uppercase" }}>{source}</strong>
        </div>
      </div>
    </section>
  );
}
