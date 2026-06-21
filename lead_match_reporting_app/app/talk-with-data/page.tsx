import { searchMatches, type SearchParams } from "@/lib/reports";
import { queryTemplates } from "@/lib/query_templates";
import { TalkWithDataForm } from "./form";

export const dynamic = "force-dynamic";

function parseQuestion(
  question: string,
): Pick<SearchParams, "minScore" | "matchType" | "manualReview"> {
  const q = question.toLowerCase();
  const filters: Pick<SearchParams, "minScore" | "matchType" | "manualReview"> = {};

  if (q.includes("exact")) filters.matchType = "Exact";
  else if (q.includes("fuzzy")) filters.matchType = "Fuzzy";

  if (q.includes("manual") || q.includes("review")) filters.manualReview = true;

  if (q.includes("high-confidence") || q.includes("high confidence")) filters.minScore = 95;

  if (!filters.matchType && !filters.manualReview && filters.minScore === undefined) {
    filters.minScore = 95;
  }

  return filters;
}

function describeIntent(
  filters: Pick<SearchParams, "minScore" | "matchType" | "manualReview">,
): string {
  const parts: string[] = [];
  if (filters.matchType) parts.push(`${filters.matchType.toLowerCase()} matches`);
  if (filters.manualReview) parts.push("manual-review matches");
  if (filters.minScore) parts.push(`high-confidence matches (score >= ${filters.minScore})`);
  if (parts.length === 0) parts.push("high-confidence matches (score >= 95)");
  return parts.join(" and ");
}

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

  const filters = question ? parseQuestion(question) : { minScore: 95 as const };
  const intent = describeIntent(filters);

  const result = await searchMatches({ warehouse, ...filters, limit });
  const fuzzy = result.rows.filter((row) => row.match_type === "Fuzzy").length;
  const manual = result.rows.filter((row) => row.match_type === "Manual Review").length;

  const defaultAnswer =
    `For warehouse ${warehouse}, the latest report has ${result.total || 0} rows matching ` +
    `${intent}. Showing ${result.rows.length} rows: ${fuzzy} fuzzy and ${manual} manual-review. ` +
    `Use Search for exact row filtering and Download CSV for ServiceNow handoff.`;

  return (
    <section className="card">
      <div className="gemini-header">Powered by Gemini 3.5 Flash</div>
      <h1>Talk with Data</h1>
      <p className="subtitle">
        Ask questions about lead-to-POS match data. Answers are grounded in your
        warehouse report data.
      </p>
      <TalkWithDataForm
        warehouse={warehouse}
        question={question || "Show exact and high-confidence matches"}
        defaultAnswer={defaultAnswer}
        templates={queryTemplates}
      />
    </section>
  );
}
