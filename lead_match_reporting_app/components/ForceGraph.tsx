"use client";

import { useEffect, useRef, useState, useCallback } from "react";

type Node = { id: string; label: string; type: "lead" | "pos"; name: string };
type Edge = {
  from: string;
  to: string;
  score: number;
  match_type: string;
};

type SimNode = Node & { x: number; y: number; vx: number; vy: number };

const COLORS: Record<string, string> = {
  lead: "#005DAA",
  pos: "#0d9488",
};

const EDGE_COLORS: Record<string, string> = {
  Fuzzy: "#B26A00",
  "Manual Review": "#B26A00",
  Exact: "#0F6E56",
};

export function ForceGraph({
  nodes,
  edges,
}: {
  nodes: Node[];
  edges: Edge[];
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [simNodes, setSimNodes] = useState<SimNode[]>([]);
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    node: SimNode;
  } | null>(null);
  const animRef = useRef(0);
  const nodesRef = useRef<SimNode[]>([]);

  const W = 960;
  const H = 640;

  useEffect(() => {
    const leads = nodes.filter((n) => n.type === "lead");
    const poss = nodes.filter((n) => n.type === "pos");

    const sn: SimNode[] = [];
    leads.forEach((n, i) => {
      const angle = (i / Math.max(leads.length, 1)) * Math.PI * 2;
      sn.push({
        ...n,
        x: W * 0.3 + Math.cos(angle) * 120 + (Math.random() - 0.5) * 40,
        y: H * 0.5 + Math.sin(angle) * 120 + (Math.random() - 0.5) * 40,
        vx: 0,
        vy: 0,
      });
    });
    poss.forEach((n, i) => {
      const angle = (i / Math.max(poss.length, 1)) * Math.PI * 2;
      sn.push({
        ...n,
        x: W * 0.7 + Math.cos(angle) * 160 + (Math.random() - 0.5) * 40,
        y: H * 0.5 + Math.sin(angle) * 160 + (Math.random() - 0.5) * 40,
        vx: 0,
        vy: 0,
      });
    });

    nodesRef.current = sn;
    let iter = 0;
    const MAX = 250;

    function tick() {
      const sn = nodesRef.current;
      const REPULSION = 2500;
      const ATTRACTION = 0.008;
      const GRAVITY = 0.012;
      const DAMPING = 0.82;

      for (const n of sn) {
        n.vx = 0;
        n.vy = 0;
      }

      for (let i = 0; i < sn.length; i++) {
        for (let j = i + 1; j < sn.length; j++) {
          const dx = sn[i].x - sn[j].x;
          const dy = sn[i].y - sn[j].y;
          const d2 = dx * dx + dy * dy || 1;
          const dist = Math.sqrt(d2);
          const f = REPULSION / d2;
          const fx = (dx / dist) * f;
          const fy = (dy / dist) * f;
          sn[i].vx += fx;
          sn[i].vy += fy;
          sn[j].vx -= fx;
          sn[j].vy -= fy;
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
        const f = ATTRACTION * dist;
        a.vx += (dx / dist) * f * dist * 0.01;
        a.vy += (dy / dist) * f * dist * 0.01;
        b.vx -= (dx / dist) * f * dist * 0.01;
        b.vy -= (dy / dist) * f * dist * 0.01;
      }

      for (const n of sn) {
        n.vx += (W / 2 - n.x) * GRAVITY;
        n.vy += (H / 2 - n.y) * GRAVITY;
        n.vx *= DAMPING;
        n.vy *= DAMPING;
        n.x += n.vx;
        n.y += n.vy;
        n.x = Math.max(40, Math.min(W - 40, n.x));
        n.y = Math.max(40, Math.min(H - 40, n.y));
      }

      iter++;
      setSimNodes([...sn]);
      if (iter < MAX) animRef.current = requestAnimationFrame(tick);
    }

    animRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animRef.current);
  }, [nodes, edges]);

  const handleNodeHover = useCallback(
    (node: SimNode | null, evt?: React.MouseEvent) => {
      if (!node || !evt) {
        setTooltip(null);
        return;
      }
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return;
      setTooltip({
        x: evt.clientX - rect.left,
        y: evt.clientY - rect.top - 10,
        node,
      });
    },
    [],
  );

  if (simNodes.length === 0) {
    return (
      <div style={{ textAlign: "center", padding: 40, color: "var(--muted)" }}>
        Loading graph…
      </div>
    );
  }

  const map = new Map(simNodes.map((n) => [n.id, n]));

  return (
    <div style={{ position: "relative" }}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{
          width: "100%",
          height: "auto",
          minHeight: 500,
          background: "#f8f9fb",
          borderRadius: 8,
          border: "1px solid var(--border)",
        }}
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 6"
            refX="10"
            refY="3"
            markerWidth="8"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0,0 L10,3 L0,6 Z" fill="#b0b8c0" />
          </marker>
        </defs>

        {edges.map((e, i) => {
          const a = map.get(e.from);
          const b = map.get(e.to);
          if (!a || !b) return null;
          const rB = b.type === "lead" ? 24 : 18;
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const x2 = b.x - (dx / dist) * rB;
          const y2 = b.y - (dy / dist) * rB;
          return (
            <line
              key={`e${i}`}
              x1={a.x}
              y1={a.y}
              x2={x2}
              y2={y2}
              stroke={EDGE_COLORS[e.match_type] || "#b0b8c0"}
              strokeWidth={e.score >= 95 ? 2 : 1.2}
              opacity={0.45}
              markerEnd="url(#arrow)"
            />
          );
        })}

        {simNodes.map((n) => {
          const r = n.type === "lead" ? 24 : 18;
          return (
            <g
              key={n.id}
              onMouseEnter={(evt) => handleNodeHover(n, evt)}
              onMouseLeave={() => handleNodeHover(null)}
              style={{ cursor: "pointer" }}
            >
              <circle
                cx={n.x}
                cy={n.y}
                r={r}
                fill={COLORS[n.type]}
                stroke="#fff"
                strokeWidth={2}
              />
              <text
                x={n.x}
                y={n.y + 3}
                textAnchor="middle"
                fontSize={9}
                fontWeight={700}
                fill="#fff"
                pointerEvents="none"
              >
                {n.label}
              </text>
              <text
                x={n.x}
                y={n.y + r + 13}
                textAnchor="middle"
                fontSize={9}
                fill="var(--muted)"
                pointerEvents="none"
              >
                {n.name.length > 22 ? n.name.slice(0, 20) + "…" : n.name}
              </text>
            </g>
          );
        })}
      </svg>

      {tooltip && (
        <div
          style={{
            position: "absolute",
            left: tooltip.x,
            top: tooltip.y,
            transform: "translate(-50%, -100%)",
            background: "#1a1a1a",
            color: "#fff",
            padding: "8px 12px",
            borderRadius: 6,
            fontSize: 12,
            lineHeight: 1.4,
            pointerEvents: "none",
            whiteSpace: "nowrap",
            zIndex: 10,
          }}
        >
          <strong>{tooltip.node.label}:</strong> {tooltip.node.name}
          <br />
          <span style={{ opacity: 0.7 }}>{tooltip.node.id}</span>
        </div>
      )}
    </div>
  );
}
