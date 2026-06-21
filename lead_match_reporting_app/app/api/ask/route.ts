import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { latestSummary, searchMatches } from "@/lib/reports";
import {
  buildTalkAnswer,
  buildTalkDigest,
  geminiContradictsDigest,
  planTalkQuestion,
  shouldReturnReportAnswer,
} from "@/lib/talk";
import { VertexAI } from "@google-cloud/vertexai";

const SYSTEM_INSTRUCTION =
  "You are the Costco Lead-to-POS Match assistant. You answer questions about how sales leads " +
  "were matched to point-of-sale transactions for a given Costco warehouse. Use ONLY the match " +
  "rows provided in context. Costco terms: a 'Primary Transaction' is the POS row a lead was " +
  "matched to; 'high-confidence' = final_score >= 95; 'exact' = deterministic match; " +
  "'manual-review' means match_type is Manual Review; 'fuzzy below threshold' means match_type " +
  "is Fuzzy and final_score < 95. Trust the computed digest totals over individual row samples. " +
  "Never invent leads, transactions, or scores. If the provided digest and rows do not answer the question, say the data does not contain it " +
  "and suggest using Search or Download CSV for ServiceNow handoff. " +
  'Respond with JSON: {"answer": "<your answer>", "matchCountReferenced": <number>}';

let vertexAI: VertexAI | null = null;

function getVertexAI() {
  if (!vertexAI) {
    vertexAI = new VertexAI({
      project: process.env.GOOGLE_CLOUD_PROJECT || process.env.VERTEX_PROJECT_ID || "ctoteam",
      location: process.env.VERTEX_LOCATION || "us-central1",
    });
  }
  return vertexAI;
}

function geminiEnabled() {
  return process.env.ENABLE_GEMINI !== "false";
}

function reportFallback(
  question: string,
  run: { runId: string; warehouse: string },
  rows: Record<string, string>[],
  total: number,
  reason?: string,
) {
  const fuzzy = rows.filter((r) => r.match_type === "Fuzzy").length;
  const manual = rows.filter((r) => r.match_type === "Manual Review").length;
  const primary = rows.filter((r) => r.primary_transaction === "True").length;
  const suffix = reason ? ` (${reason})` : "";
  return NextResponse.json({
    question,
    run,
    answer:
      `Run ${run.runId} for warehouse ${run.warehouse}: ${total} total rows. ` +
      `Sample of ${rows.length}: ${fuzzy} fuzzy, ${manual} manual-review, ` +
      `${primary} primary transactions.${suffix}`,
    usedRows: rows.length,
    source: "report-data",
  });
}

function parseGeminiResponse(text: string): { answer: string; matchCountReferenced: number } {
  const cleaned = text
    .replace(/^```(?:json)?\s*\n?/m, "")
    .replace(/\n?```\s*$/m, "")
    .trim();
  try {
    const parsed = JSON.parse(cleaned);
    return {
      answer: String(parsed.answer || ""),
      matchCountReferenced: Number(parsed.matchCountReferenced || 0),
    };
  } catch {
    return { answer: cleaned, matchCountReferenced: 0 };
  }
}

export async function POST(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const body = await request.json();
  const warehouse = body.warehouse ? String(body.warehouse) : "115";
  const question = body.question ? String(body.question) : "Summarize latest matches";
  const filterHint = body.filter_hint ? String(body.filter_hint) : undefined;
  const plan = planTalkQuestion(question, filterHint);

  const result = await searchMatches({
    warehouse,
    ...plan.filters,
    minScore: body.min_score ? Number(body.min_score) : plan.filters.minScore,
    limit: plan.limit,
  });
  const summary = await latestSummary(warehouse);

  if (!result.run) {
    return NextResponse.json({ error: "No report found" }, { status: 404 });
  }

  const allRows =
    plan.intent === "unmatched-leads"
      ? (await searchMatches({ warehouse, limit: 10000 })).rows
      : undefined;
  const groundedAnswer = buildTalkAnswer({
    warehouse,
    runId: result.run.runId || summary?.match_run_id,
    plan,
    rows: result.rows,
    total: result.total || 0,
    summary,
    allRows,
  });

  if (shouldReturnReportAnswer(plan)) {
    return NextResponse.json({
      question,
      run: result.run,
      answer: groundedAnswer,
      usedRows: result.rows.length,
      source: "report-data",
    });
  }

  if (!geminiEnabled()) {
    return reportFallback(question, result.run, result.rows, result.total || 0, "Gemini disabled");
  }

  try {
    const ai = getVertexAI();
    const model = ai.getGenerativeModel({
      model: process.env.GEMINI_MODEL || "gemini-2.5-flash",
      systemInstruction: SYSTEM_INSTRUCTION,
    });

    const digest = buildTalkDigest({
      warehouse,
      runId: result.run.runId || summary?.match_run_id,
      plan,
      rows: result.rows,
      total: result.total || 0,
      summary,
      allRows,
    });
    const response = await model.generateContent({
      contents: [
        {
          role: "user",
          parts: [
            {
              text:
                `Computed answer from report data:\n${groundedAnswer}\n\n` +
                `Computed digest:\n${JSON.stringify(digest)}\n\nQuestion: ${question}`,
            },
          ],
        },
      ],
    });

    const text = response.response.candidates?.[0]?.content?.parts?.[0]?.text || "";
    const parsed = parseGeminiResponse(text);
    const answer = parsed.answer || groundedAnswer;

    return NextResponse.json({
      question,
      run: result.run,
      answer: geminiContradictsDigest(answer, plan, result.total || 0) ? groundedAnswer : answer,
      usedRows: parsed.matchCountReferenced || result.rows.length,
      source: "gemini",
    });
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    return reportFallback(
      question,
      result.run,
      result.rows,
      result.total || 0,
      `Gemini error: ${detail}`,
    );
  }
}
