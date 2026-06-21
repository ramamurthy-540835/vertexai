import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { searchMatches } from "@/lib/reports";

export async function POST(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const body = await request.json();
  const warehouse = body.warehouse ? String(body.warehouse) : "115";
  const question = body.question ? String(body.question) : "Summarize latest matches";
  const result = await searchMatches({
    warehouse,
    leadId: body.lead_id ? String(body.lead_id) : undefined,
    minScore: body.min_score ? Number(body.min_score) : undefined,
    limit: 50,
  });

  if (!result.run) {
    return NextResponse.json({ error: "No report found" }, { status: 404 });
  }

  const fuzzy = result.rows.filter((row) => row.match_type === "Fuzzy").length;
  const manual = result.rows.filter((row) => row.match_type === "Manual Review").length;
  const primary = result.rows.filter((row) => row.primary_transaction === "True").length;

  return NextResponse.json({
    question,
    run: result.run,
    answer:
      `Run ${result.run.runId} has ${result.total || 0} rows matching the request. ` +
      `The returned sample contains ${fuzzy} fuzzy matches, ${manual} manual-review rows, ` +
      `and ${primary} primary transactions. Use the search endpoint for exact filters and ` +
      `the download endpoint for the complete CSV.`,
    rows: result.rows.slice(0, 10),
  });
}
