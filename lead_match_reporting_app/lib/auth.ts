import { NextRequest, NextResponse } from "next/server";

export function requireApiAuth(request: NextRequest): NextResponse | null {
  const expectedToken = process.env.REPORTING_API_TOKEN?.trim();
  if (!expectedToken) {
    return null;
  }

  const header = request.headers.get("authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice("Bearer ".length).trim() : "";
  if (token === expectedToken) {
    return null;
  }

  return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
}
