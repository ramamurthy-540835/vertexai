import { NextResponse } from "next/server";
import { Storage } from "@google-cloud/storage";

const storage = new Storage();

function bucketName() {
  return process.env.REPORT_BUCKET || "lead-match-ctoteam";
}

export async function GET() {
  try {
    const file = storage.bucket(bucketName()).file("monitoring/latest.json");
    const [exists] = await file.exists();
    if (!exists) {
      return NextResponse.json(
        { available: false, error: "No monitoring snapshot found" },
        { status: 404 },
      );
    }
    const [metadata] = await file.getMetadata();
    const [buffer] = await file.download();
    const snapshot = JSON.parse(buffer.toString("utf8"));
    return NextResponse.json({
      ...snapshot,
      _meta: {
        source: `gs://${bucketName()}/monitoring/latest.json`,
        gcs_updated: metadata.updated || metadata.timeCreated,
        served_at: new Date().toISOString(),
      },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ available: false, error: message }, { status: 500 });
  }
}
