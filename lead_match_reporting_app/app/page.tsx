import Link from "next/link";
import { listRuns, latestSummary, readSummary, type ReportSummary } from "@/lib/reports";

export const dynamic = "force-dynamic";

type WarehouseCard = {
  warehouse: string;
  runId: string;
  updated: string;
  summary: ReportSummary | null;
};

async function allWarehouses(): Promise<WarehouseCard[]> {
  const runs = await listRuns();

  const latest = new Map<string, { warehouse: string; runId: string; project: string; updated: string }>();
  for (const run of runs) {
    if (!run.warehouse || latest.has(run.warehouse)) continue;
    latest.set(run.warehouse, {
      warehouse: run.warehouse,
      runId: run.runId,
      project: run.project,
      updated: run.updated as string,
    });
  }

  const entries = Array.from(latest.values()).sort((a, b) =>
    a.warehouse.localeCompare(b.warehouse, undefined, { numeric: true }),
  );

  const cards = await Promise.all(
    entries.map(async (entry) => {
      let summary: ReportSummary | null = null;
      try {
        summary = await readSummary(entry.project, entry.warehouse, entry.runId);
      } catch {}
      return { warehouse: entry.warehouse, runId: entry.runId, updated: entry.updated, summary };
    }),
  );

  return cards;
}

function WarehouseLanding({ cards }: { cards: WarehouseCard[] }) {
  return (
    <section>
      <div className="card" style={{ marginBottom: 24 }}>
        <p className="pill">All Warehouses</p>
        <h1 className="title">Lead Match Fleet</h1>
        <p className="subtitle">
          {cards.length} warehouses with reports. Select a warehouse to view its latest match
          results, search rows, explore the graph, or ask questions. New warehouses appear
          automatically when their reports land in GCS.
        </p>
      </div>

      <div className="warehouse-grid">
        {cards.map((card) => (
          <div key={card.warehouse} className="card warehouse-card">
            <div className="warehouse-card-header">
              <strong className="warehouse-id">WH {card.warehouse}</strong>
              <span className="pill" style={{ fontSize: 11 }}>
                {card.summary ? `${card.summary.match_rows.toLocaleString()} matches` : "—"}
              </span>
            </div>
            {card.summary ? (
              <>
                <p style={{ margin: "8px 0 4px", fontSize: 13, color: "var(--muted)" }}>
                  Run {card.runId}
                </p>
                <p style={{ margin: 0, fontSize: 12, color: "var(--muted)" }}>
                  {card.summary.generated_at}
                </p>
                <div className="warehouse-card-metrics">
                  <span>
                    <strong style={{ color: "var(--costco-blue)" }}>
                      {card.summary.primary_transaction_count.toLocaleString()}
                    </strong>{" "}
                    primary txns
                  </span>
                  <span>
                    <strong style={{ color: "var(--costco-blue)" }}>
                      {card.summary.lead_rows.toLocaleString()}
                    </strong>{" "}
                    leads
                  </span>
                </div>
              </>
            ) : (
              <p style={{ margin: "8px 0", fontSize: 13, color: "var(--muted)" }}>
                Summary unavailable
              </p>
            )}
            <div className="warehouse-card-links">
              <Link className="button primary" href={`/?warehouse=${card.warehouse}`}>
                Latest
              </Link>
              <Link className="button outline-blue" href={`/search?warehouse=${card.warehouse}`}>
                Search
              </Link>
              <Link className="button outline-blue" href={`/graph?warehouse=${card.warehouse}`}>
                Graph
              </Link>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function SingleWarehouse({
  warehouse,
  summary,
}: {
  warehouse: string;
  summary: ReportSummary | null;
}) {
  if (!summary) {
    return (
      <section className="card" style={{ textAlign: "center", padding: 48 }}>
        <h1>No Reports Found</h1>
        <p className="subtitle" style={{ margin: "12px auto" }}>
          Warehouse <strong>{warehouse}</strong> has no match reports in GCS yet. Reports appear
          automatically once the matching pipeline writes to this warehouse folder.
        </p>
        <Link className="button primary" href="/">
          &larr; All Warehouses
        </Link>
      </section>
    );
  }

  return (
    <section className="hero">
      <div className="card">
        <p className="pill">Reporting Layer over GCS</p>
        <h1 className="title">Latest Match Result Access</h1>
        <p className="subtitle">
          This app exposes already-generated GCS reports through secure HTTPS endpoints for
          ServiceNow and gives Costco users search, download, annotation, and graph-style review.
        </p>
        <div className="links" style={{ marginTop: 24 }}>
          <Link className="button primary" href={`/search?warehouse=${warehouse}`}>
            Search Results
          </Link>
          <Link className="button outline-blue" href={`/graph?warehouse=${warehouse}`}>
            Explore Graph
          </Link>
          <a
            className="button outline-red"
            href={`/api/results/download?warehouse=${summary.warehouse}&run_id=${summary.match_run_id}&type=csv`}
          >
            Download CSV
          </a>
        </div>
        <div style={{ marginTop: 16 }}>
          <Link href="/" style={{ fontSize: 13, color: "var(--muted)" }}>
            &larr; All Warehouses
          </Link>
        </div>
      </div>

      <aside className="card">
        <h2>Warehouse {warehouse}</h2>
        <p>
          <strong>{summary.match_run_id}</strong>
        </p>
        <p className="subtitle">Generated {summary.generated_at}</p>
        <div className="grid">
          <div className="metric">
            <span>Matches</span>
            <strong>{summary.match_rows.toLocaleString()}</strong>
          </div>
          <div className="metric">
            <span>Primary Transactions</span>
            <strong>{summary.primary_transaction_count.toLocaleString()}</strong>
          </div>
          <div className="metric">
            <span>Leads</span>
            <strong>{summary.lead_rows.toLocaleString()}</strong>
          </div>
          <div className="metric">
            <span>POS Rows</span>
            <strong>{summary.pos_rows.toLocaleString()}</strong>
          </div>
        </div>
      </aside>
    </section>
  );
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ warehouse?: string }>;
}) {
  const params = await searchParams;
  const warehouse = params.warehouse;

  if (!warehouse) {
    const cards = await allWarehouses();
    return <WarehouseLanding cards={cards} />;
  }

  const summary = await latestSummary(warehouse);
  return <SingleWarehouse warehouse={warehouse} summary={summary} />;
}
