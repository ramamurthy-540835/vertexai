import neo4j, { type Driver } from "neo4j-driver";

let driver: Driver | null = null;

export function getDriver(): Driver | null {
  const uri = process.env.NEO4J_URI;
  if (!uri) return null;

  if (!driver) {
    driver = neo4j.driver(
      uri,
      neo4j.auth.basic(
        process.env.NEO4J_USER || "neo4j",
        process.env.NEO4J_PASSWORD || "",
      ),
    );
  }
  return driver;
}

export function getDatabase(): string {
  return process.env.NEO4J_DATABASE || "neo4j";
}
