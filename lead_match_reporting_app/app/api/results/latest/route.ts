import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { latestSummary } from "@/lib/reports";

export async function GET(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const warehouse = request.nextUrl.searchParams.get("warehouse") || undefined;
  const summary = await latestSummary(warehouse);
  if (!summary) {
    return NextResponse.json({ error: "No report found" }, { status: 404 });
  }
  return NextResponse.json(summary);
}
