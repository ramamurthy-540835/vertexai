"use client";

import { useEffect, useRef, useState, useCallback } from "react";

type Node = { id: string; label: string; type: "lead" | "pos"; name: string };
type Edge = { from: string; to: string; score: number; match_type: string };
type SimNode = Node & { x: number; y: number; vx: number; vy: number; pinned?: boolean };

const NODE_COLORS: Record<string, string> = { lead: "#005DAA", pos: "#0d9488" };
const EDGE_COLORS: Record<string, string> = {
  Fuzzy: "#B26A00",
  "Manual Review": "#B26A00",
  Exact: "#0F6E56",
};

function toCSV(nodes: Node[], edges: Edge[]): string {
  const header = "from_id,from_name,from_type,to_id,to_name,to_type,score,match_type";
  const nodeMap = new Map(nodes.map((n) => [n.id, n]));
  const rows = edges.map((e) => {
    const a = nodeMap.get(e.from);
    const b = nodeMap.get(e.to);
    const esc = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
    return [
      esc(e.from), esc(a?.name || ""), a?.type || "",
      esc(e.to), esc(b?.name || ""), b?.type || "",
      e.score, esc(e.match_type),
    ].join(",");
  });
  return [header, ...rows].join("\n");
}

function download(content: string, filename: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function ForceGraph({ nodes, edges }: { nodes: Node[]; edges: Edge[] }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [simNodes, setSimNodes] = useState<SimNode[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ id: string; startX: number; startY: number } | null>(null);
  const panRef = useRef<{ startX: number; startY: number; panX: number; panY: number } | null>(null);
  const nodesRef = useRef<SimNode[]>([]);
  const animRef = useRef(0);
  const runningRef = useRef(true);

  const W = 960;
  const H = 640;

  useEffect(() => {
    const leads = nodes.filter((n) => n.type === "lead");
    const poss = nodes.filter((n) => n.type === "pos");
    const sn: SimNode[] = [];

    leads.forEach((n, i) => {
      const a = (i / Math.max(leads.length, 1)) * Math.PI * 2;
      sn.push({ ...n, x: W * 0.32 + Math.cos(a) * 140, y: H * 0.5 + Math.sin(a) * 140, vx: 0, vy: 0 });
    });
    poss.forEach((n, i) => {
      const a = (i / Math.max(poss.length, 1)) * Math.PI * 2;
      sn.push({ ...n, x: W * 0.68 + Math.cos(a) * 180, y: H * 0.5 + Math.sin(a) * 180, vx: 0, vy: 0 });
    });

    nodesRef.current = sn;
    let iter = 0;
    runningRef.current = true;

    function tick() {
      if (!runningRef.current) return;
      const sn = nodesRef.current;
      for (const n of sn) { if (!n.pinned) { n.vx = 0; n.vy = 0; } }

      for (let i = 0; i < sn.length; i++) {
        for (let j = i + 1; j < sn.length; j++) {
          if (sn[i].pinned && sn[j].pinned) continue;
          const dx = sn[i].x - sn[j].x;
          const dy = sn[i].y - sn[j].y;
          const d2 = dx * dx + dy * dy || 1;
          const dist = Math.sqrt(d2);
          const f = 2800 / d2;
          if (!sn[i].pinned) { sn[i].vx += (dx / dist) * f; sn[i].vy += (dy / dist) * f; }
          if (!sn[j].pinned) { sn[j].vx -= (dx / dist) * f; sn[j].vy -= (dy / dist) * f; }
        }
      }

      const map = new Map(sn.map((n) => [n.id, n]));
      for (const e of edges) {
        const a = map.get(e.from);
        const b = map.get(e.to);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = 0.006 * dist;
        if (!a.pinned) { a.vx += (dx / dist) * f * dist * 0.01; a.vy += (dy / dist) * f * dist * 0.01; }
        if (!b.pinned) { b.vx -= (dx / dist) * f * dist * 0.01; b.vy -= (dy / dist) * f * dist * 0.01; }
      }

      const damping = iter < 60 ? 0.78 : 0.88;
      for (const n of sn) {
        if (n.pinned) continue;
        n.vx += (W / 2 - n.x) * 0.01;
        n.vy += (H / 2 - n.y) * 0.01;
        n.vx *= damping;
        n.vy *= damping;
        n.x += n.vx;
        n.y += n.vy;
        n.x = Math.max(30, Math.min(W - 30, n.x));
        n.y = Math.max(30, Math.min(H - 30, n.y));
      }

      iter++;
      setSimNodes([...sn]);
      if (iter < 300) animRef.current = requestAnimationFrame(tick);
    }

    animRef.current = requestAnimationFrame(tick);
    return () => { runningRef.current = false; cancelAnimationFrame(animRef.current); };
  }, [nodes, edges]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.max(0.3, Math.min(3, z - e.deltaY * 0.001)));
  }, []);

  const handleBgDown = useCallback((e: React.MouseEvent) => {
    if ((e.target as SVGElement).tagName === "svg" || (e.target as SVGElement).classList.contains("graph-bg")) {
      panRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y };
    }
  }, [pan]);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (dragRef.current) {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return;
      const n = nodesRef.current.find((n) => n.id === dragRef.current!.id);
      if (n) {
        n.x = (e.clientX - rect.left - pan.x) / zoom;
        n.y = (e.clientY - rect.top - pan.y) / zoom;
        n.pinned = true;
        setSimNodes([...nodesRef.current]);
      }
    }
    if (panRef.current) {
      setPan({
        x: panRef.current.panX + (e.clientX - panRef.current.startX),
        y: panRef.current.panY + (e.clientY - panRef.current.startY),
      });
    }
  }, [zoom, pan]);

  const handleMouseUp = useCallback(() => {
    if (dragRef.current) {
      const n = nodesRef.current.find((n) => n.id === dragRef.current!.id);
      if (n) n.pinned = false;
      dragRef.current = null;
    }
    panRef.current = null;
  }, []);

  const handleNodeDown = useCallback((id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    dragRef.current = { id, startX: e.clientX, startY: e.clientY };
    const n = nodesRef.current.find((n) => n.id === id);
    if (n) n.pinned = true;
  }, []);

  const handleNodeClick = useCallback((id: string) => {
    setSelected((prev) => (prev === id ? null : id));
  }, []);

  const connectedTo = new Set<string>();
  if (selected) {
    for (const e of edges) {
      if (e.from === selected) connectedTo.add(e.to);
      if (e.to === selected) connectedTo.add(e.from);
    }
  }

  if (simNodes.length === 0) {
    return <div style={{ textAlign: "center", padding: 60, color: "var(--muted)" }}>Loading graph…</div>;
  }

  const map = new Map(simNodes.map((n) => [n.id, n]));

  return (
    <div>
      <div className="graph-toolbar">
        <div className="graph-toolbar-left">
          <button className="button" onClick={() => setZoom((z) => Math.min(3, z + 0.2))}>Zoom +</button>
          <button className="button" onClick={() => setZoom((z) => Math.max(0.3, z - 0.2))}>Zoom −</button>
          <button className="button" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>Reset</button>
          {selected && (
            <span style={{ fontSize: 13, color: "var(--costco-blue)", fontWeight: 500 }}>
              Selected: {simNodes.find((n) => n.id === selected)?.name || selected}
            </span>
          )}
        </div>
        <div className="graph-toolbar-right">
          <button
            className="button outline-blue"
            onClick={() => download(JSON.stringify({ nodes, edges }, null, 2), "graph.json", "application/json")}
          >
            Export JSON
          </button>
          <button
            className="button outline-blue"
            onClick={() => download(toCSV(nodes, edges), "graph.csv", "text/csv")}
          >
            Export CSV
          </button>
        </div>
      </div>

      <div style={{ position: "relative", overflow: "hidden", borderRadius: 8, border: "1px solid var(--border)" }}>
        <svg
          ref={svgRef}
          width="100%"
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          style={{ background: "#1e2430", display: "block", cursor: panRef.current ? "grabbing" : "grab" }}
          onWheel={handleWheel}
          onMouseDown={handleBgDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <rect className="graph-bg" width={W} height={H} fill="transparent" />

          <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
            {edges.map((e, i) => {
              const a = map.get(e.from);
              const b = map.get(e.to);
              if (!a || !b) return null;
              const rB = b.type === "lead" ? 26 : 20;
              const dx = b.x - a.x;
              const dy = b.y - a.y;
              const dist = Math.sqrt(dx * dx + dy * dy) || 1;
              const x2 = b.x - (dx / dist) * rB;
              const y2 = b.y - (dy / dist) * rB;

              const isHighlighted = selected && (e.from === selected || e.to === selected);
              const dimmed = selected && !isHighlighted;

              return (
                <g key={`e${i}`}>
                  <line
                    x1={a.x} y1={a.y} x2={x2} y2={y2}
                    stroke={isHighlighted ? "#fff" : (EDGE_COLORS[e.match_type] || "#556")}
                    strokeWidth={isHighlighted ? 2.5 : (e.score >= 95 ? 1.8 : 1)}
                    opacity={dimmed ? 0.08 : (isHighlighted ? 0.9 : 0.35)}
                  />
                  {isHighlighted && (
                    <text
                      x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6}
                      textAnchor="middle" fontSize={9} fill="#8af" fontWeight={600}
                    >
                      {e.score} · {e.match_type}
                    </text>
                  )}
                </g>
              );
            })}

            {simNodes.map((n) => {
              const r = n.type === "lead" ? 26 : 20;
              const isSelected = n.id === selected;
              const isConnected = connectedTo.has(n.id);
              const dimmed = selected && !isSelected && !isConnected;

              return (
                <g
                  key={n.id}
                  style={{ cursor: "pointer" }}
                  onMouseDown={(e) => handleNodeDown(n.id, e)}
                  onClick={() => handleNodeClick(n.id)}
                  onMouseEnter={(e) => {
                    const rect = svgRef.current?.getBoundingClientRect();
                    if (rect) setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top - 12, text: `${n.label}: ${n.name}\n${n.id}` });
                  }}
                  onMouseLeave={() => setTooltip(null)}
                >
                  {isSelected && (
                    <circle cx={n.x} cy={n.y} r={r + 6} fill="none" stroke="#4da6ff" strokeWidth={2} opacity={0.7} />
                  )}
                  <circle
                    cx={n.x} cy={n.y} r={r}
                    fill={NODE_COLORS[n.type]}
                    stroke={isSelected ? "#fff" : "rgba(255,255,255,0.3)"}
                    strokeWidth={isSelected ? 2.5 : 1.5}
                    opacity={dimmed ? 0.15 : 1}
                  />
                  <text
                    x={n.x} y={n.y - 3} textAnchor="middle"
                    fontSize={8} fontWeight={700} fill="#fff"
                    opacity={dimmed ? 0.15 : 1} pointerEvents="none"
                  >
                    {n.label}
                  </text>
                  <text
                    x={n.x} y={n.y + 8} textAnchor="middle"
                    fontSize={7} fill="rgba(255,255,255,0.7)"
                    opacity={dimmed ? 0.15 : 1} pointerEvents="none"
                  >
                    {n.name.length > 18 ? n.name.slice(0, 16) + "…" : n.name}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>

        {tooltip && (
          <div
            style={{
              position: "absolute", left: tooltip.x, top: tooltip.y,
              transform: "translate(-50%, -100%)", background: "#fff",
              color: "#1a1a1a", padding: "8px 14px", borderRadius: 6,
              fontSize: 12, lineHeight: 1.5, pointerEvents: "none",
              whiteSpace: "pre-line", boxShadow: "0 4px 16px rgba(0,0,0,0.25)", zIndex: 10,
            }}
          >
            {tooltip.text}
          </div>
        )}
      </div>
    </div>
  );
}
