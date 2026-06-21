import { readMatches } from "./reports";
import { getDriver, getDatabase } from "./neo4j";

const BATCH_SIZE = 1000;

const MERGE_QUERY = `
  MERGE (w:Warehouse {warehouseId: $warehouse})
  WITH w
  UNWIND $rows AS row
  MERGE (l:Lead {leadId: row.lead_id})
  SET l.name = row.lead_business_name, l.status = row.lifecycle_state,
      l.score = toFloat(row.final_score)
  MERGE (p:Pos {posId: row.pos_id})
  SET p.txnId = row.primary_transaction, p.amount = toFloat(row.transaction_amount),
      p.date = row.transaction_date
  MERGE (l)-[m:MATCHED_TO]->(p)
  SET m.finalScore = toFloat(row.final_score), m.matchType = row.match_type
  MERGE (p)-[:AT_WAREHOUSE]->(w)
  FOREACH (mid IN CASE WHEN row.member_id <> '' THEN [row.member_id] ELSE [] END |
    MERGE (mem:Member {memberId: mid})
    MERGE (l)-[:BELONGS_TO]->(mem)
    MERGE (p)-[:BELONGS_TO]->(mem)
  )
`;

function normalizeRow(row: Record<string, string>) {
  return {
    lead_id: row.lead_id || "",
    pos_id: row.pos_id || "",
    lead_business_name: row.lead_business_name || "",
    lifecycle_state: row.lifecycle_state || "",
    final_score: row.final_score || "0",
    primary_transaction: row.primary_transaction || "",
    transaction_amount: row.transaction_amount || "0",
    transaction_date: row.transaction_date || "",
    match_type: row.match_type || "",
    member_id: row.member_id || "",
  };
}

export async function ingestRun(project: string, warehouse: string, runId: string) {
  const driver = getDriver();
  if (!driver) throw new Error("Neo4j not configured (NEO4J_URI is unset)");

  const allRows = await readMatches(project, warehouse, runId);
  const db = getDatabase();
  let ingested = 0;

  for (let i = 0; i < allRows.length; i += BATCH_SIZE) {
    const rows = allRows.slice(i, i + BATCH_SIZE).map(normalizeRow);
    const session = driver.session({ database: db });
    try {
      await session.executeWrite((tx) => tx.run(MERGE_QUERY, { warehouse, rows }));
      ingested += rows.length;
    } finally {
      await session.close();
    }
  }

  return { ingested, runId, warehouse };
}
