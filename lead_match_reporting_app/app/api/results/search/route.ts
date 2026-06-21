import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { resultQuery } from "@/lib/query";
import { searchMatches } from "@/lib/reports";

export async function GET(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const result = await searchMatches(resultQuery(request));
  if (!result.run) {
    return NextResponse.json({ error: "No report found", rows: [] }, { status: 404 });
  }
  return NextResponse.json(result);
}
