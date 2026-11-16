import { useMemo } from "react";
import { cx, statusTone } from "./ui";

export interface GraphNode { id: string; label: string; status: string; statusLabel: string; deps: string[] }

const W = 158, H = 88, GAP_X = 52, GAP_Y = 26, PAD = 28;
const TONE_TEXT = { teal: "text-teal", violet: "text-violet-soft", amber: "text-amber", red: "text-red", muted: "text-faint" };

/** Layered left-to-right layout: a node's column is its longest dependency chain. */
function layout(nodes: GraphNode[]) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const depth = new Map<string, number>();
  const depthOf = (id: string, seen = new Set<string>()): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0; // plans are validated acyclic; this only guards rendering
    seen.add(id);
    const node = byId.get(id);
    const value = node && node.deps.length ? Math.max(...node.deps.filter((d) => byId.has(d)).map((d) => depthOf(d, seen) + 1), 0) : 0;
    depth.set(id, value);
    return value;
  };
  const columns: GraphNode[][] = [];
  for (const node of nodes) (columns[depthOf(node.id)] ??= []).push(node);
  const rows = Math.max(...columns.map((c) => c.length));
  const height = rows * H + (rows - 1) * GAP_Y + PAD * 2;
  const position = new Map<string, { x: number; y: number }>();
  columns.forEach((column, c) => {
    const block = column.length * H + (column.length - 1) * GAP_Y;
    column.forEach((node, r) => position.set(node.id, { x: PAD + c * (W + GAP_X), y: (height - block) / 2 + r * (H + GAP_Y) }));
  });
  return { position, width: columns.length * W + (columns.length - 1) * GAP_X + PAD * 2, height };
}

export function PlanGraph({ nodes, selected, onSelect }: { nodes: GraphNode[]; selected: string | null; onSelect: (id: string) => void }) {
  const { position, width, height } = useMemo(() => layout(nodes), [nodes]);
  return (
    <div className="dot-grid overflow-x-auto" tabIndex={0} role="group" aria-label="Execution plan graph">
      <div className="relative mx-auto" style={{ width, height: Math.max(height, 250) }}>
        <svg aria-hidden className="absolute inset-0 text-[#5d6072]" width={width} height={Math.max(height, 250)}>
          <defs>
            <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0 L8 4 L0 8 z" fill="currentColor" />
            </marker>
          </defs>
          <g transform={`translate(0 ${(Math.max(height, 250) - height) / 2})`}>
            {nodes.flatMap((node) => node.deps.map((dep) => {
              const from = position.get(dep), to = position.get(node.id);
              if (!from || !to) return null;
              const x1 = from.x + W, y1 = from.y + H / 2, x2 = to.x, y2 = to.y + H / 2, mid = x2 - GAP_X / 2;
              return <path key={`${dep}-${node.id}`} d={`M${x1} ${y1} H${mid} V${y2} H${x2 - 2}`} fill="none"
                stroke="currentColor" strokeWidth="1.5" markerEnd="url(#arrow)" />;
            }))}
          </g>
        </svg>
        <div className="absolute inset-0" style={{ top: (Math.max(height, 250) - height) / 2 }}>
          {nodes.map((node, index) => {
            const at = position.get(node.id)!;
            const isSelected = node.id === selected;
            return (
              <button key={node.id} type="button" onClick={() => onSelect(node.id)} aria-pressed={isSelected}
                style={{ left: at.x, top: at.y, width: W, height: H }}
                className={cx("absolute rounded-[5px] border bg-panel px-4 py-3 text-left transition-colors",
                  isSelected ? "border-violet-soft bg-[#17162a] ring-1 ring-violet-soft" :
                    node.status === "waiting_approval" ? "border-dashed border-amber" : "border-line-strong hover:border-faint")}>
                <span className={cx("block font-mono text-[11px]", isSelected ? "text-violet-soft" : "text-faint")}>
                  {String(index + 1).padStart(2, "0")}{isSelected && " · selected"}
                </span>
                <span className="mt-1 block truncate text-[15px] font-medium">{node.label}</span>
                <span className={cx("mt-0.5 block truncate text-[13px]", TONE_TEXT[statusTone(node.status)])}>{node.statusLabel}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
