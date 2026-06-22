import { NextResponse } from "next/server";
import { buildLiveSnapshot } from "@/lib/snapshot";

export async function GET() {
  try {
    const snapshot = await buildLiveSnapshot();
    return NextResponse.json({
      ...snapshot,
      _meta: {
        source: "live",
        served_at: new Date().toISOString(),
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { available: false, error: message },
      { status: 500 },
    );
  }
}
