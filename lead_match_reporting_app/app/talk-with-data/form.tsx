"use client";

import { useState, type FormEvent } from "react";
import type { QueryTemplate } from "@/lib/query_templates";

export function TalkWithDataForm({
  warehouse: initialWarehouse,
  question: initialQuestion,
  defaultAnswer,
  templates,
}: {
  warehouse: string;
  question: string;
  defaultAnswer: string;
  templates: QueryTemplate[];
}) {
  const [warehouse, setWarehouse] = useState(initialWarehouse);
  const [question, setQuestion] = useState(initialQuestion);
  const [answer, setAnswer] = useState(defaultAnswer);
  const [source, setSource] = useState<string>("report-data");
  const [loading, setLoading] = useState(false);

  async function askGemini(wh: string, q: string) {
    setLoading(true);
    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ warehouse: wh, question: q }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAnswer(data.answer);
      setSource(data.source || "gemini");
    } catch {
      window.location.href =
        `/talk-with-data?warehouse=${encodeURIComponent(wh)}&question=${encodeURIComponent(q)}`;
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    askGemini(warehouse, question);
  }

  function handleChip(template: QueryTemplate) {
    setQuestion(template.question);
    askGemini(warehouse, template.question);
  }

  return (
    <>
      <form className="form" onSubmit={handleSubmit}>
        <div className="field">
          <label>Warehouse</label>
          <input
            name="warehouse"
            value={warehouse}
            onChange={(e) => setWarehouse(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Question</label>
          <input
            name="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
        </div>
        <button className="button primary" type="submit" disabled={loading}>
          {loading ? "Thinking…" : "Ask"}
        </button>
      </form>

      <div className="chips">
        {templates.map((t) => (
          <button
            key={t.label}
            type="button"
            className="chip"
            disabled={loading}
            onClick={() => handleChip(t)}
            title={t.filterHint}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="card answer">
        <strong>Answer</strong>
        {source !== "report-data" && (
          <small style={{ marginLeft: 8, color: "var(--muted)" }}>via {source}</small>
        )}
        <p>{loading ? "Querying match data…" : answer}</p>
      </div>
    </>
  );
}
