import { NextResponse } from "next/server";
import { listRuns } from "@/lib/reports";

export async function GET() {
  const runs = await listRuns();

  const byWarehouse = new Map<string, { warehouse: string; latestRunId: string; updated: string }>();
  for (const run of runs) {
    if (!run.warehouse) continue;
    if (!byWarehouse.has(run.warehouse)) {
      byWarehouse.set(run.warehouse, {
        warehouse: run.warehouse,
        latestRunId: run.runId,
        updated: run.updated as string,
      });
    }
  }

  const warehouses = Array.from(byWarehouse.values()).sort((a, b) =>
    a.warehouse.localeCompare(b.warehouse, undefined, { numeric: true }),
  );

  return NextResponse.json(warehouses);
}
