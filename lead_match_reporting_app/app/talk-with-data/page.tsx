import { searchMatches, latestSummary } from "@/lib/reports";
import { queryTemplates } from "@/lib/query_templates";
import { buildTalkAnswer, planTalkQuestion } from "@/lib/talk";
import { TalkWithDataForm } from "./form";

export const dynamic = "force-dynamic";

export default async function TalkWithDataPage({
  searchParams,
}: {
  searchParams: Promise<{ warehouse?: string; question?: string; limit?: string }>;
}) {
  const params = await searchParams;
  const warehouse = params.warehouse || "115";
  const question = params.question;
  const rawLimit = params.limit ? Number(params.limit) : 25;
  const limit = Math.min(Math.max(Number.isFinite(rawLimit) ? rawLimit : 25, 1), 500);
  const activeQuestion = question || "Show exact and high-confidence matches";
  const plan = planTalkQuestion(activeQuestion);

  const result = await searchMatches({ warehouse, ...plan.filters, limit });
  const summary = await latestSummary(warehouse);
  const allRows =
    plan.intent === "unmatched-leads"
      ? (await searchMatches({ warehouse, limit: 10000 })).rows
      : undefined;

  const defaultAnswer = buildTalkAnswer({
    warehouse,
    runId: result.run?.runId || summary?.match_run_id,
    plan,
    rows: result.rows,
    total: result.total || 0,
    summary,
    allRows,
  });

  return (
    <section className="talk-page">
      <div className="talk-header-card">
        <div className="talk-header-strip">Powered by Gemini 2.5 Flash</div>
        <div className="talk-header-body">
          <div className="talk-header-left">
            <h1>Talk with Data</h1>
            <p className="subtitle" style={{ marginBottom: 0 }}>
              Ask natural-language questions about lead-to-POS match quality,
              coverage, and exceptions. Answers are grounded in your warehouse
              report data.
            </p>
          </div>
          {summary && (
            <div className="talk-context-stats">
              <div className="talk-stat">
                <span className="talk-stat-value">{summary.match_rows.toLocaleString()}</span>
                <span className="talk-stat-label">Total Matches</span>
              </div>
              <div className="talk-stat">
                <span className="talk-stat-value">
                  {summary.primary_transaction_count.toLocaleString()}
                </span>
                <span className="talk-stat-label">Primary Txns</span>
              </div>
              <div className="talk-stat">
                <span className="talk-stat-value">{summary.lead_rows.toLocaleString()}</span>
                <span className="talk-stat-label">Leads</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <TalkWithDataForm
        warehouse={warehouse}
        question={activeQuestion}
        defaultAnswer={defaultAnswer}
        templates={queryTemplates}
      />
    </section>
  );
}
