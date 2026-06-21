import { searchMatches } from "@/lib/reports";

export const dynamic = "force-dynamic";

export default async function TalkWithDataPage({
  searchParams,
}: {
  searchParams: Promise<{ warehouse?: string; question?: string }>;
}) {
  const params = await searchParams;
  const warehouse = params.warehouse || "115";
  const question = params.question || "Show exact and high-confidence matches";
  const result = await searchMatches({ warehouse, minScore: 95, limit: 25 });
  const fuzzy = result.rows.filter((row) => row.match_type === "Fuzzy").length;
  const manual = result.rows.filter((row) => row.match_type === "Manual Review").length;

  return (
    <section className="card">
      <h1>Talk with Data</h1>
      <p className="subtitle">
        Optional AI layer. The current page uses report data directly; `/api/ask` can be wired to
        Gemini by environment later without changing the matching workflow.
      </p>
      <form className="form">
        <div className="field">
          <label>Warehouse</label>
          <input name="warehouse" defaultValue={warehouse} />
        </div>
        <div className="field">
          <label>Question</label>
          <input name="question" defaultValue={question} />
        </div>
        <button className="button primary" type="submit">
          Ask
        </button>
      </form>
      <div className="card answer">
        <strong>Answer</strong>
        <p>
          For warehouse {warehouse}, the latest report has {result.total || 0} rows matching this
          high-confidence query. The first {result.rows.length} rows include {fuzzy} fuzzy matches
          and {manual} manual-review matches. Use Search for exact row filtering and Download CSV
          for ServiceNow handoff.
        </p>
      </div>
    </section>
  );
}
