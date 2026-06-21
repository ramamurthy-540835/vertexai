import { NextResponse } from "next/server";

export function GET() {
  return NextResponse.json({
    ok: true,
    service: "lead-match-reporting-app",
    time: new Date().toISOString(),
  });
}
