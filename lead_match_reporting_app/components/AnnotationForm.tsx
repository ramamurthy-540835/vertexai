"use client";

import { useState, useTransition } from "react";

type Props = {
  runId: string;
  leadId?: string;
  posId?: string;
};

export function AnnotationForm({ runId, leadId, posId }: Props) {
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("");
  const [message, setMessage] = useState("");
  const [isPending, startTransition] = useTransition();

  function submit() {
    startTransition(async () => {
      setMessage("");
      const response = await fetch("/api/annotations", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          run_id: runId,
          lead_id: leadId,
          pos_id: posId,
          note,
          status,
          author: "reporting-ui",
        }),
      });
      if (!response.ok) {
        setMessage("Annotation failed.");
        return;
      }
      setNote("");
      setStatus("");
      setMessage("Annotation saved.");
    });
  }

  return (
    <div className="card" style={{ marginTop: 18 }}>
      <h2>Annotate Result</h2>
      <div className="form">
        <div className="field">
          <label>Status</label>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">No status</option>
            <option value="approved">Approved</option>
            <option value="review-needed">Review needed</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>
        <div className="field">
          <label>Note</label>
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Add context for ServiceNow or reviewer follow-up"
            rows={3}
          />
        </div>
      </div>
      <button className="button primary" disabled={isPending || !note} onClick={submit}>
        {isPending ? "Saving..." : "Save Annotation"}
      </button>
      {message ? <p>{message}</p> : null}
    </div>
  );
}
