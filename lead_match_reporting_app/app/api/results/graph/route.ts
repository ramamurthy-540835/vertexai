import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { resultQuery } from "@/lib/query";
import { graphData } from "@/lib/reports";

export async function GET(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const result = await graphData(resultQuery(request));
  if (!result.run) {
    return NextResponse.json({ error: "No report found", nodes: [], edges: [] }, { status: 404 });
  }
  return NextResponse.json(result);
}
