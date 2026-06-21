import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { downloadReport } from "@/lib/reports";

const contentTypes = {
  csv: "text/csv; charset=utf-8",
  summary: "application/json; charset=utf-8",
  markdown: "text/markdown; charset=utf-8",
};

export async function GET(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const runId = request.nextUrl.searchParams.get("run_id");
  const warehouse = request.nextUrl.searchParams.get("warehouse") || undefined;
  const type = request.nextUrl.searchParams.get("type") || "csv";
  if (!runId || !["csv", "summary", "markdown"].includes(type)) {
    return NextResponse.json({ error: "run_id and valid type are required" }, { status: 400 });
  }

  const report = await downloadReport(runId, warehouse, type as "csv" | "summary" | "markdown");
  if (!report) {
    return NextResponse.json({ error: "Report not found" }, { status: 404 });
  }

  return new NextResponse(new Uint8Array(report.buffer), {
    headers: {
      "content-type": contentTypes[type as keyof typeof contentTypes],
      "content-disposition": `attachment; filename="${report.run.runId}-${report.fileName}"`,
    },
  });
}
