import type { CsvRow } from "./csv";
import type { ReportSummary, SearchParams } from "./reports";

export type TalkIntent =
  | "manual-review"
  | "fuzzy-below-threshold"
  | "high-confidence"
  | "top-transactions"
  | "unmatched-leads"
  | "summary"
  | "general";

export type TalkPlan = {
  intent: TalkIntent;
  filters: SearchParams;
  limit: number;
};

const REVIEW_THRESHOLD = 95;

function includesAny(value: string, terms: string[]) {
  return terms.some((term) => value.includes(term));
}

export function planTalkQuestion(question: string, filterHint?: string): TalkPlan {
  const q = `${question} ${filterHint || ""}`.toLowerCase();

  if (includesAny(q, ["unmatched", "no pos", "without pos", "no transaction"])) {
    return { intent: "unmatched-leads", filters: {}, limit: 200 };
  }

  if (
    includesAny(q, ["top transactions", "highest-value", "highest value", "highest amount"]) ||
    (q.includes("transaction") && includesAny(q, ["top", "highest", "largest", "amount", "value"]))
  ) {
    return {
      intent: "top-transactions",
      filters: { sortBy: "amount", sortDirection: "desc" },
      limit: 200,
    };
  }

  if (
    q.includes("fuzzy") &&
    includesAny(q, ["below", "under", "less than", "low", "threshold", "maxscore"])
  ) {
    return {
      intent: "fuzzy-below-threshold",
      filters: {
        matchType: "Fuzzy",
        maxScore: REVIEW_THRESHOLD,
        sortBy: "score",
        sortDirection: "desc",
      },
      limit: 200,
    };
  }

  if (includesAny(q, ["manual", "review", "verify", "verification", "exception"])) {
    return {
      intent: "manual-review",
      filters: {
        manualReview: true,
        sortBy: "score",
        sortDirection: "desc",
      },
      limit: 200,
    };
  }

  if (includesAny(q, ["summary", "summarize", "quality", "coverage"])) {
    return { intent: "summary", filters: {}, limit: 200 };
  }

  if (includesAny(q, ["exact", "high-confidence", "high confidence", "score >= 95"])) {
    return {
      intent: "high-confidence",
      filters: {
        minScore: REVIEW_THRESHOLD,
        sortBy: "score",
        sortDirection: "desc",
      },
      limit: 200,
    };
  }

  return {
    intent: "general",
    filters: {
      minScore: REVIEW_THRESHOLD,
      sortBy: "score",
      sortDirection: "desc",
    },
    limit: 200,
  };
}

export function describeTalkIntent(plan: TalkPlan) {
  switch (plan.intent) {
    case "manual-review":
      return "manual-review matches";
    case "fuzzy-below-threshold":
      return `fuzzy matches below score ${REVIEW_THRESHOLD}`;
    case "high-confidence":
      return `high-confidence matches (score >= ${REVIEW_THRESHOLD})`;
    case "top-transactions":
      return "top matched transactions by amount";
    case "unmatched-leads":
      return "unmatched leads";
    case "summary":
      return "match quality summary";
    default:
      return `high-confidence matches (score >= ${REVIEW_THRESHOLD})`;
  }
}

function formatCount(value: number | undefined) {
  return Number(value || 0).toLocaleString();
}

function formatScore(value: string | undefined) {
  const score = Number(value || 0);
  return Number.isFinite(score) ? score.toFixed(2) : "0.00";
}

function formatAmount(row: CsvRow) {
  const amount = Number(row.order_amount || row.transaction_amount || 0);
  return Number.isFinite(amount)
    ? amount.toLocaleString("en-US", { style: "currency", currency: "USD" })
    : "$0.00";
}

function rowBusiness(row: CsvRow) {
  const lead = row.lead_business_name || row.lead_id || "unknown lead";
  const pos = row.pos_business_name || row.pos_id || "unknown POS";
  return lead === pos ? lead : `${lead} / ${pos}`;
}

function sampleRows(rows: CsvRow[], includeAmount = false) {
  const samples = rows.slice(0, 5).map((row) => {
    const amount = includeAmount ? `, ${formatAmount(row)}` : "";
    return `${row.lead_id || "lead"} -> ${row.pos_id || "POS"} (${row.match_type || "match"}, score ${formatScore(row.final_score)}${amount}, ${rowBusiness(row)})`;
  });
  return samples.length ? ` Examples: ${samples.join("; ")}.` : "";
}

export function buildTalkDigest(input: {
  warehouse: string;
  runId?: string;
  plan: TalkPlan;
  rows: CsvRow[];
  total: number;
  summary?: ReportSummary | null;
  allRows?: CsvRow[];
}) {
  const { warehouse, runId, plan, rows, total, summary, allRows } = input;
  const matchedLeadCount = allRows
    ? new Set(allRows.map((row) => row.lead_id).filter(Boolean)).size
    : undefined;

  return {
    warehouse,
    runId,
    intent: plan.intent,
    filters: plan.filters,
    reportTotals: summary
      ? {
          matchRows: summary.match_rows,
          leadRows: summary.lead_rows,
          posRows: summary.pos_rows,
          primaryTransactions: summary.primary_transaction_count,
          matchTypeCounts: summary.match_type_counts,
          lifecycleStateCounts: summary.lifecycle_state_counts,
          generatedAt: summary.generated_at,
        }
      : null,
    filteredTotal: total,
    rowsProvided: rows.length,
    matchedLeadCount,
    estimatedUnmatchedLeadCount:
      summary && matchedLeadCount !== undefined
        ? Math.max(summary.lead_rows - matchedLeadCount, 0)
        : undefined,
    sampleRows: rows.slice(0, 20).map((row) => ({
      lead_id: row.lead_id,
      pos_id: row.pos_id,
      match_type: row.match_type,
      lifecycle_state: row.lifecycle_state,
      primary_transaction: row.primary_transaction,
      final_score: row.final_score,
      lead_business_name: row.lead_business_name,
      pos_business_name: row.pos_business_name,
      order_amount: row.order_amount || row.transaction_amount,
    })),
  };
}

export function buildTalkAnswer(input: {
  warehouse: string;
  runId?: string;
  plan: TalkPlan;
  rows: CsvRow[];
  total: number;
  summary?: ReportSummary | null;
  allRows?: CsvRow[];
}) {
  const { warehouse, runId, plan, rows, total, summary, allRows } = input;
  const runText = runId ? ` run ${runId}` : "";

  if (plan.intent === "manual-review") {
    const reviewCount = summary?.match_type_counts?.["Manual Review"] ?? total;
    const potentialCount = summary?.lifecycle_state_counts?.Potential;
    const stateText =
      potentialCount !== undefined ? ` Lifecycle state Potential also has ${formatCount(potentialCount)} rows.` : "";
    return (
      `Warehouse ${warehouse}${runText}: ${formatCount(reviewCount)} matches need manual review. ` +
      `These are rows where match_type is Manual Review; ${formatCount(total)} are in the current filtered result set.${stateText}` +
      sampleRows(rows)
    );
  }

  if (plan.intent === "fuzzy-below-threshold") {
    return (
      `Warehouse ${warehouse}${runText}: ${formatCount(total)} fuzzy matches are below score ${REVIEW_THRESHOLD}. ` +
      `They are sorted closest to the threshold first so the highest-confidence exceptions are easiest to triage.` +
      sampleRows(rows)
    );
  }

  if (plan.intent === "top-transactions") {
    return (
      `Warehouse ${warehouse}${runText}: the top matched transaction rows are sorted by order amount. ` +
      `The filtered result set contains ${formatCount(total)} rows.` +
      sampleRows(rows, true)
    );
  }

  if (plan.intent === "unmatched-leads") {
    const matchedLeadCount = allRows
      ? new Set(allRows.map((row) => row.lead_id).filter(Boolean)).size
      : undefined;
    if (!summary || matchedLeadCount === undefined) {
      return (
        `Warehouse ${warehouse}${runText}: the provided matches file does not include a standalone unmatched-lead list. ` +
        "Use the lead source table or a dedicated unmatched export to list exact lead IDs."
      );
    }
    const unmatched = Math.max(summary.lead_rows - matchedLeadCount, 0);
    return (
      `Warehouse ${warehouse}${runText}: ${formatCount(unmatched)} of ${formatCount(summary.lead_rows)} leads have no row in matches.csv. ` +
      `The current report has match rows for ${formatCount(matchedLeadCount)} distinct leads. ` +
      "The matches report can estimate the count, but it does not contain the missing lead IDs; use the lead source table or an unmatched export for the exact list."
    );
  }

  if (plan.intent === "summary" && summary) {
    const fuzzy = summary.match_type_counts?.Fuzzy ?? 0;
    const review = summary.match_type_counts?.["Manual Review"] ?? 0;
    return (
      `Warehouse ${warehouse}${runText}: ${formatCount(summary.match_rows)} total match rows across ${formatCount(summary.lead_rows)} leads and ` +
      `${formatCount(summary.pos_rows)} POS rows. There are ${formatCount(summary.primary_transaction_count)} primary transactions, ` +
      `${formatCount(fuzzy)} fuzzy matches, and ${formatCount(review)} manual-review matches.`
    );
  }

  if (plan.intent === "high-confidence") {
    const exact = summary?.match_type_counts?.Exact ?? 0;
    const exactText =
      exact > 0
        ? `${formatCount(exact)} exact rows are present in the report.`
        : "No Exact match_type rows are present in this report.";
    return (
      `Warehouse ${warehouse}${runText}: ${formatCount(total)} rows have final_score >= ${REVIEW_THRESHOLD}. ${exactText}` +
      sampleRows(rows)
    );
  }

  return (
    `Warehouse ${warehouse}${runText}: found ${formatCount(total)} rows for ${describeTalkIntent(plan)}. ` +
    `Showing ${formatCount(rows.length)} rows in context.` +
    sampleRows(rows)
  );
}

export function shouldReturnReportAnswer(plan: TalkPlan) {
  return plan.intent !== "general";
}

export function geminiContradictsDigest(answer: string, plan: TalkPlan, total: number) {
  const normalized = answer.toLowerCase();
  if (total <= 0) return false;
  if (plan.intent === "manual-review") {
    return (
      normalized.includes("does not contain") ||
      normalized.includes("no manual") ||
      normalized.includes("not contain any matches")
    );
  }
  return false;
}
