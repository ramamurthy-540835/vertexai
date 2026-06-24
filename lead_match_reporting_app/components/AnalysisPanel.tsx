"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";

type AnalysisPanelProps = {
  warehouse: string;
  runId: string;
};

export function AnalysisPanel({ warehouse, runId }: AnalysisPanelProps) {
  const [narrative, setNarrative] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchNarrative = async () => {
      try {
        const res = await fetch(
          `/api/analysis/narrative?warehouse=${encodeURIComponent(
            warehouse
          )}&run_id=${encodeURIComponent(runId)}`
        );
        if (!res.ok) {
          setError(`Failed to fetch narrative (${res.status})`);
          setLoading(false);
          return;
        }
        const text = await res.text();
        setNarrative(text);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    };
    fetchNarrative();
  }, [warehouse, runId]);

  if (loading) {
    return (
      <div className="card">
        <h2>Distribution Analysis</h2>
        <p>Loading...</p>
      </div>
    );
  }

  if (error || !narrative) {
    return (
      <div className="card">
        <h2>Distribution Analysis</h2>
        <p style={{ color: "#999" }}>
          {error || "No analysis available for this run."}
        </p>
      </div>
    );
  }

  return (
    <div className="card analysis-panel">
      <h2>Distribution Analysis</h2>
      <div className="markdown-content">
        <ReactMarkdown>{narrative}</ReactMarkdown>
      </div>
    </div>
  );
}
