import { useMemo, useState } from "react";
import type { Catalog, KnowledgePoint, KnowledgeSnapshotView } from "../../api/types";

interface Props {
  snapshot: KnowledgeSnapshotView;
  catalog: Catalog;
  selectedId: string | null;
  onSelect: (kpId: string) => void;
}

interface Positioned {
  point: KnowledgePoint;
  x: number;
  y: number;
}

const NODE_W = 148;
const NODE_H = 40;
const COL_GAP = 220;
const ROW_GAP = 88;

/** 图只画正式关系的方向；课程归属（annotation）不画成逻辑边。布局是确定性网格。 */
export function KnowledgeGraph({ snapshot, catalog, selectedId, onSelect }: Props) {
  const [zoom, setZoom] = useState(1);

  const topics = useMemo(() => {
    const ids = new Set<string>();
    snapshot.points.forEach((point) => point.topic_ids.forEach((topicId) => ids.add(topicId)));
    catalog.topics.forEach((topic) => ids.add(topic.topic_id));
    return catalog.topics.filter((topic) => ids.has(topic.topic_id));
  }, [snapshot.points, catalog.topics]);

  const positioned = useMemo<Positioned[]>(() => {
    const groups = new Map<string, KnowledgePoint[]>();
    snapshot.points.forEach((point) => {
      const topicId = point.topic_ids[0] ?? "未分组";
      groups.set(topicId, [...(groups.get(topicId) ?? []), point]);
    });
    const result: Positioned[] = [];
    topics.forEach((topic, topicIndex) => {
      const points = (groups.get(topic.topic_id) ?? []).sort((a, b) => a.kp_id.localeCompare(b.kp_id));
      points.forEach((point, pointIndex) => {
        result.push({ point, x: 40 + topicIndex * COL_GAP, y: 56 + pointIndex * ROW_GAP });
      });
    });
    groups.forEach((points, topicId) => {
      if (topics.some((topic) => topic.topic_id === topicId)) return;
      points.forEach((point, pointIndex) => {
        result.push({ point, x: 40 + topics.length * COL_GAP, y: 56 + pointIndex * ROW_GAP });
      });
    });
    return result;
  }, [snapshot.points, topics]);

  const byId = useMemo(() => new Map(positioned.map((item) => [item.point.kp_id, item])), [positioned]);
  const maxY = positioned.reduce((max, item) => Math.max(max, item.y), 0);
  const width = Math.max(860, 40 + (topics.length + 1) * COL_GAP);
  const height = Math.max(360, maxY + 100);

  const edges = snapshot.relations.flatMap((relation, relationIndex) => {
    const target = byId.get(relation.target_id);
    if (!target) return [];
    const type = (relation.relation_type ?? relation.type ?? "related").toLowerCase();
    return (relation.source_ids ?? [])
      .map((sourceId) => byId.get(sourceId))
      .filter((source): source is Positioned => Boolean(source))
      .map((source, sourceIndex) => ({ source, target, type, key: `${relationIndex}-${sourceIndex}` }));
  });

  return (
    <div className="stack">
      <div className="rowBetween">
        <span className="small muted">
          {snapshot.points.length} 个知识点 · {snapshot.relations.length} 条正式关系 · 布局按主题与编号固定，缩放 {Math.round(zoom * 100)}%
        </span>
        <div className="row">
          <button type="button" className="btn btnSm" onClick={() => setZoom((value) => Math.max(0.5, Math.round((value - 0.1) * 10) / 10))} disabled={zoom <= 0.5}>缩小</button>
          <button type="button" className="btn btnSm" onClick={() => setZoom(1)}>重置视图</button>
          <button type="button" className="btn btnSm" onClick={() => setZoom((value) => Math.min(2, Math.round((value + 0.1) * 10) / 10))} disabled={zoom >= 2}>放大</button>
        </div>
      </div>

      <div className="graphBox" style={{ overflow: "auto" }}>
        {positioned.length === 0 ? (
          <p className="small muted" style={{ padding: 16 }}>这个知识版本没有知识点。</p>
        ) : (
          <svg
            className="graphSvg"
            viewBox={`0 0 ${width} ${height}`}
            style={{ width: width * zoom, height: height * zoom, minWidth: "100%" }}
            role="img"
            aria-label="知识点关系图；同一份数据在左侧列表中可以键盘逐项访问"
          >
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-text-secondary)" />
              </marker>
            </defs>
            {topics.map((topic, index) => (
              <text key={topic.topic_id} x={40 + index * COL_GAP} y={28} fontSize={13} fontWeight={600} fill="var(--color-text-secondary)">
                {topic.title}
              </text>
            ))}
            {edges.map((edge) => {
              const prereq = edge.type === "prerequisite";
              const x1 = edge.source.x + NODE_W;
              const y1 = edge.source.y + NODE_H / 2;
              const x2 = edge.target.x;
              const y2 = edge.target.y + NODE_H / 2;
              return (
                <g key={edge.key}>
                  <line
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    stroke="var(--color-text-secondary)"
                    strokeWidth={prereq ? 1.75 : 1}
                    strokeDasharray={prereq ? undefined : "5 4"}
                    markerEnd="url(#arrow)"
                  />
                  <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6} fontSize={10} fill="var(--color-text-secondary)">
                    {prereq ? "前置" : "关联"}
                  </text>
                </g>
              );
            })}
            {positioned.map(({ point, x, y }) => {
              const selected = point.kp_id === selectedId;
              return (
                <g key={point.kp_id} role="button" tabIndex={0} aria-label={`知识点 ${point.name}`} style={{ cursor: "pointer" }} onClick={() => onSelect(point.kp_id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(point.kp_id); } }}>
                  <rect
                    x={x}
                    y={y}
                    width={NODE_W}
                    height={NODE_H}
                    rx={6}
                    fill={selected ? "var(--color-selected-bg)" : "var(--color-background)"}
                    stroke={selected ? "var(--color-selected-border)" : "var(--color-border)"}
                    strokeWidth={selected ? 1.75 : 1}
                  />
                  <text x={x + 10} y={y + 18} fontSize={12} fill="var(--color-text-primary)">
                    {point.name.length > 13 ? `${point.name.slice(0, 13)}…` : point.name}
                  </text>
                  <text x={x + 10} y={y + 32} fontSize={10} fill="var(--color-text-secondary)">
                    {point.scope_role === "core" ? "核心概念" : "拓展概念"}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
        <div className="graphLegend">
          <span className="legendLine"><svg width="28" height="8" aria-hidden="true"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--color-text-secondary)" strokeWidth="1.75" /></svg>前置（实线箭头）</span>
          <span className="legendLine"><svg width="28" height="8" aria-hidden="true"><line x1="0" y1="4" x2="28" y2="4" stroke="var(--color-text-secondary)" strokeWidth="1" strokeDasharray="5 4" /></svg>其他正式关系（虚线箭头）</span>
          <span>方向严格为来源 → 目标；课程材料归属在右侧“实际关联”列出，不画成逻辑边。</span>
        </div>
      </div>
      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>键盘可访问的知识点列表</summary>
        <ul className="stackSm" style={{ marginTop: 8 }}>
          {positioned.map(({ point }) => (
            <li key={point.kp_id}>
              <button type="button" className="btn btnSm btnGhost" onClick={() => onSelect(point.kp_id)}>
                {point.name}
              </button>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
