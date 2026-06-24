import { AnalysisPanel } from "@/components/AnalysisPanel";
import { latestSummary } from "@/lib/reports";

export const dynamic = "force-dynamic";

type Params = {
  warehouse?: string;
  run_id?: string;
};

export default async function AnalysisPage({
  searchParams,
}: {
  searchParams: Promise<Params>;
}) {
  const params = await searchParams;
  const warehouse = params.warehouse || "115";
  const latest = await latestSummary(warehouse);
  const runId = params.run_id || latest?.match_run_id || "";

  return (
    <div className="analysis-layout">
      <section className="card full-width">
        <h1>Distribution Analysis</h1>
        <p className="subtitle">
          Warehouse: <code>{warehouse}</code> | Run: <code>{runId}</code>
        </p>

        {runId ? (
          <AnalysisPanel warehouse={warehouse} runId={runId} />
        ) : (
          <p style={{ color: "#999" }}>
            No run ID provided. Check back after a match run completes.
          </p>
        )}

        <div style={{ marginTop: 24 }}>
          <a href={`/search?warehouse=${warehouse}&run_id=${runId}`} className="button outline">
            Back to Search
          </a>
        </div>
      </section>
    </div>
  );
}
