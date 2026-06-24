import { Storage } from "@google-cloud/storage";

export const dynamic = "force-dynamic";

const storage = new Storage();

function bucketName() {
  return process.env.REPORT_BUCKET || "lead-match-ctoteam";
}

function projectId() {
  return process.env.GOOGLE_CLOUD_PROJECT || "ctoteam";
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const warehouse = searchParams.get("warehouse") || "115";
  const runId = searchParams.get("run_id");

  if (!runId) {
    return new Response("Missing run_id parameter", { status: 400 });
  }

  try {
    const bucket = storage.bucket(bucketName());
    const path = `reports/lead_match/${projectId()}/${warehouse}/${runId}/comparative_analysis.md`;
    const file = bucket.file(path);

    const [exists] = await file.exists();
    if (!exists) {
      return new Response(
        `Analysis not available. Run: gs://${bucketName()}/${path}`,
        { status: 404 }
      );
    }

    const [content] = await file.download();
    return new Response(content.toString(), {
      headers: { "content-type": "text/markdown; charset=utf-8" },
    });
  } catch (error) {
    console.error("Error fetching narrative:", error);
    return new Response(`Error: ${(error as Error).message}`, { status: 500 });
  }
}
