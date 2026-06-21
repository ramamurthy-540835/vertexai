import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { searchMatches } from "@/lib/reports";
import { VertexAI } from "@google-cloud/vertexai";

const SYSTEM_INSTRUCTION =
  "You are the Costco Lead-to-POS Match assistant. You answer questions about how sales leads " +
  "were matched to point-of-sale transactions for a given Costco warehouse. Use ONLY the match " +
  "rows provided in context. Costco terms: a 'Primary Transaction' is the POS row a lead was " +
  "matched to; 'high-confidence' = final_score >= 95; 'exact' = deterministic match; " +
  "'fuzzy'/'manual-review' = needs human verification. Never invent leads, transactions, or " +
  "scores. If the provided rows do not answer the question, say the data does not contain it " +
  "and suggest using Search or Download CSV for ServiceNow handoff. " +
  'Respond with JSON: {"answer": "<your answer>", "matchCountReferenced": <number>}';

let vertexAI: VertexAI | null = null;

function getVertexAI() {
  if (!vertexAI) {
    vertexAI = new VertexAI({
      project: process.env.GOOGLE_CLOUD_PROJECT || process.env.VERTEX_PROJECT_ID || "ctoteam",
      location: process.env.VERTEX_LOCATION || "global",
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

  const result = await searchMatches({
    warehouse,
    minScore: body.min_score ? Number(body.min_score) : undefined,
    limit: 200,
  });

  if (!result.run) {
    return NextResponse.json({ error: "No report found" }, { status: 404 });
  }

  if (!geminiEnabled()) {
    return reportFallback(question, result.run, result.rows, result.total || 0, "Gemini disabled");
  }

  try {
    const ai = getVertexAI();
    const model = ai.getGenerativeModel({
      model: process.env.GEMINI_MODEL || "gemini-3.5-flash",
      systemInstruction: SYSTEM_INSTRUCTION,
    });

    const rowContext = JSON.stringify(result.rows.slice(0, 200));
    const response = await model.generateContent({
      contents: [
        {
          role: "user",
          parts: [
            {
              text: `Match rows for warehouse ${warehouse} (${result.total || 0} total, showing ${result.rows.length}):\n${rowContext}\n\nQuestion: ${question}`,
            },
          ],
        },
      ],
    });

    const text = response.response.candidates?.[0]?.content?.parts?.[0]?.text || "";
    const parsed = parseGeminiResponse(text);

    return NextResponse.json({
      question,
      run: result.run,
      answer: parsed.answer || "No answer generated.",
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
