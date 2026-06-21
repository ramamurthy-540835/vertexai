import { NextRequest, NextResponse } from "next/server";
import { requireApiAuth } from "@/lib/auth";
import { appendAnnotation, readAnnotations } from "@/lib/reports";

export async function GET(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const runId = request.nextUrl.searchParams.get("run_id");
  if (!runId) {
    return NextResponse.json({ error: "run_id is required" }, { status: 400 });
  }
  return NextResponse.json({ annotations: await readAnnotations(runId) });
}

export async function POST(request: NextRequest) {
  const denied = requireApiAuth(request);
  if (denied) return denied;

  const body = await request.json();
  if (!body.run_id || !body.note) {
    return NextResponse.json({ error: "run_id and note are required" }, { status: 400 });
  }

  const annotation = await appendAnnotation({
    run_id: String(body.run_id),
    lead_id: body.lead_id ? String(body.lead_id) : undefined,
    pos_id: body.pos_id ? String(body.pos_id) : undefined,
    note: String(body.note),
    status: body.status ? String(body.status) : undefined,
    author: body.author ? String(body.author) : undefined,
  });

  return NextResponse.json({ annotation }, { status: 201 });
}
