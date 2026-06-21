import Link from "next/link";
import { latestSummary } from "@/lib/reports";

export const dynamic = "force-dynamic";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ warehouse?: string }>;
}) {
  const params = await searchParams;
  const warehouse = params.warehouse || "115";
  const summary = await latestSummary(warehouse);

  return (
    <section className="hero">
      <div className="card">
        <p className="pill" style={{ display: "inline-block" }}>
          Separate reporting layer over GCS
        </p>
        <h1 className="title">Latest Match Result Access</h1>
        <p className="subtitle">
          This app exposes already-generated GCS reports through secure HTTPS endpoints for
          ServiceNow and gives Costco users search, download, annotation, and graph-style review.
        </p>
        <div className="links" style={{ marginTop: 24 }}>
          <Link className="button primary" href={`/search?warehouse=${warehouse}`}>
            Search Results
          </Link>
          <Link className="button" href={`/graph?warehouse=${warehouse}`}>
            Explore Graph
          </Link>
          {summary ? (
            <a
              className="button"
              href={`/api/results/download?warehouse=${summary.warehouse}&run_id=${summary.match_run_id}&type=csv`}
            >
              Download CSV
            </a>
          ) : null}
        </div>
      </div>

      <aside className="card">
        <h2>Latest Warehouse {warehouse}</h2>
        {summary ? (
          <>
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
          </>
        ) : (
          <p>No report found for this warehouse.</p>
        )}
      </aside>
    </section>
  );
}
