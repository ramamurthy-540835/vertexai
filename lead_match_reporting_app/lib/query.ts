import { NextRequest } from "next/server";

export function resultQuery(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const minScore = params.get("min_score");
  const limit = params.get("limit");

  return {
    warehouse: params.get("warehouse") || undefined,
    runId: params.get("run_id") || undefined,
    leadId: params.get("lead_id") || undefined,
    posId: params.get("pos_id") || undefined,
    matchType: params.get("match_type") || undefined,
    lifecycleState: params.get("lifecycle_state") || undefined,
    minScore: minScore ? Number(minScore) : undefined,
    limit: limit ? Number(limit) : undefined,
  };
}
