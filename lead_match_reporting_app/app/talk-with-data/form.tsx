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
      <div className="card talk-input-card">
        <form className="talk-form" onSubmit={handleSubmit}>
          <div className="field" style={{ flex: "0 0 140px" }}>
            <label>Warehouse</label>
            <input
              name="warehouse"
              value={warehouse}
              onChange={(e) => setWarehouse(e.target.value)}
            />
          </div>
          <div className="field" style={{ flex: 1 }}>
            <label>Ask a question about this warehouse&apos;s match data</label>
            <input
              name="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. Which leads have the highest match scores?"
            />
          </div>
          <button
            className="button primary talk-ask-btn"
            type="submit"
            disabled={loading}
          >
            {loading ? "Analyzing…" : "Ask"}
          </button>
        </form>
      </div>

      <div className="talk-chips-section">
        <span className="talk-chips-label">Quick questions:</span>
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
      </div>

      <div className="talk-answer-card">
        <div className="talk-answer-header">
          <span className="talk-answer-title">Answer</span>
          {source !== "report-data" && (
            <span className="talk-source-badge">
              {source === "gemini" ? "Gemini 2.5 Flash" : source}
            </span>
          )}
        </div>
        <div className="talk-answer-body">
          {loading ? (
            <p className="talk-loading">
              Analyzing match data for warehouse {warehouse}…
            </p>
          ) : (
            <p>{answer}</p>
          )}
        </div>
      </div>
    </>
  );
}
