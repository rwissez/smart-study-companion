import { useEffect, useMemo, useRef, useState } from "react";
import type { Catalog, KnowledgePoint, KnowledgeRelation, KnowledgeSnapshotView } from "../../api/types";
import { UNCLASSIFIED_TOPIC_LABEL, knowledgeScopeLabel, relationTypeLabel } from "../../lib/labels";

interface Props {
  snapshot: KnowledgeSnapshotView;
  catalog: Catalog;
  selectedId: string | null;
  onSelect: (kpId: string) => void;
}

/** 主题标签页：全部之外，每个目录主题一个页签；没有主题的知识点进入“未归类”。 */
const ALL_KEY = "__all__";
const UNCLASSIFIED_KEY = "__unclassified__";
const NODE_W = 148;
const NODE_H = 40;
const PADDING = 26;
const COLLISION_GAP = 12;
const LAYOUT_CACHE_LIMIT = 24;
const TOPIC_ACCENTS = [
  "var(--color-primary)",
  "var(--color-secondary)",
  "var(--color-warning)",
  "var(--color-unknown)",
];

export interface LayoutPoint {
  kp_id: string;
  topic_ids: string[];
}

export interface LayoutRelation {
  source_ids: string[];
  target_id: string;
}

export interface LayoutNode {
  kpId: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface KnowledgeLayout {
  width: number;
  height: number;
  nodes: LayoutNode[];
}

interface LayoutOptions {
  version: string;
  seedKey: string;
  groupOf?: Record<string, string>;
}

interface Edge {
  key: string;
  source: LayoutNode;
  target: LayoutNode;
  type: string;
  path: string;
  labelX: number;
  labelY: number;
  sourceName: string;
  targetName: string;
}

/** 确定性哈希只用于固定初始位置与曲线方向；同一知识版本得到同一布局。 */
function hashString(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function mulberry32(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

function layoutSize(count: number): { width: number; height: number } {
  const root = Math.sqrt(Math.max(1, count));
  return {
    width: Math.round(clamp(root * 195, 560, 1180)),
    height: Math.round(clamp(root * 132, 420, 760)),
  };
}

interface ComponentBox {
  key: string;
  width: number;
  height: number;
  nodes: LayoutNode[];
}

function findAllComponents(
  points: LayoutPoint[],
  relations: LayoutRelation[],
): LayoutPoint[][] {
  const byId = new Map(points.map((point) => [point.kp_id, point]));
  const adjacency = new Map<string, Set<string>>();
  points.forEach((point) => adjacency.set(point.kp_id, new Set()));
  relations.forEach((relation) => {
    if (!byId.has(relation.target_id)) return;
    (relation.source_ids ?? []).forEach((sourceId) => {
      if (!byId.has(sourceId) || sourceId === relation.target_id) return;
      adjacency.get(sourceId)?.add(relation.target_id);
      adjacency.get(relation.target_id)?.add(sourceId);
    });
  });
  const seen = new Set<string>();
  const components: LayoutPoint[][] = [];
  [...points]
    .sort((left, right) => left.kp_id.localeCompare(right.kp_id))
    .forEach((point) => {
      if (seen.has(point.kp_id)) return;
      const stack = [point.kp_id];
      seen.add(point.kp_id);
      const ids: string[] = [];
      while (stack.length > 0) {
        const current = stack.pop() as string;
        ids.push(current);
        adjacency.get(current)?.forEach((next) => {
          if (!seen.has(next)) {
            seen.add(next);
            stack.push(next);
          }
        });
      }
      components.push(
        ids
          .sort((left, right) => left.localeCompare(right))
          .map((id) => byId.get(id) as LayoutPoint),
      );
    });
  return components;
}

/** 单个连通分量内部的力导向布局；分量之间不会再互相排斥，避免把节点推到画布边缘。 */
function localForceLayout(
  points: LayoutPoint[],
  relations: LayoutRelation[],
  width: number,
  height: number,
  seed: number,
): LayoutNode[] {
  const ordered = [...points].sort((left, right) => left.kp_id.localeCompare(right.kp_id));
  if (ordered.length === 1) {
    return [{ kpId: ordered[0].kp_id, x: 0, y: 0, width: NODE_W, height: NODE_H }];
  }
  const random = mulberry32(seed);
  const centerX = width / 2;
  const centerY = height / 2;
  const nodes = ordered.map((point, index) => {
    const angle = (2 * Math.PI * index) / ordered.length + (random() - 0.5) * 0.4;
    const radius = Math.min(width, height) * 0.28 * (0.5 + random() * 0.6);
    return {
      kpId: point.kp_id,
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius * 0.8,
      width: NODE_W,
      height: NODE_H,
    };
  });

  const indexById = new Map(nodes.map((node, index) => [node.kpId, index]));
  const pairs: Array<[number, number]> = [];
  relations.forEach((relation) => {
    const targetIndex = indexById.get(relation.target_id);
    if (targetIndex === undefined) return;
    (relation.source_ids ?? []).forEach((sourceId) => {
      const sourceIndex = indexById.get(sourceId);
      if (sourceIndex !== undefined && sourceIndex !== targetIndex) {
        pairs.push([sourceIndex, targetIndex]);
      }
    });
  });

  const padding = 10;
  const area = Math.max(1, (width - padding * 2) * (height - padding * 2));
  const idealDistance = Math.sqrt(area / nodes.length) * 0.7;
  const iterations = 170;

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const temperature = Math.max(
      0.6,
      Math.min(width, height) * 0.1 * (1 - iteration / iterations),
    );
    const displacement = nodes.map(() => ({ x: 0, y: 0 }));

    for (let left = 0; left < nodes.length; left += 1) {
      for (let right = left + 1; right < nodes.length; right += 1) {
        const dx = nodes[left].x - nodes[right].x;
        const dy = nodes[left].y - nodes[right].y;
        const distance = Math.max(1, Math.hypot(dx, dy));
        const force = ((idealDistance * idealDistance) / distance) * 0.5;
        displacement[left].x += (dx / distance) * force;
        displacement[left].y += (dy / distance) * force;
        displacement[right].x -= (dx / distance) * force;
        displacement[right].y -= (dy / distance) * force;
      }
    }

    pairs.forEach(([source, target]) => {
      const dx = nodes[target].x - nodes[source].x;
      const dy = nodes[target].y - nodes[source].y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const force = ((distance * distance) / idealDistance) * 1.2;
      displacement[source].x += (dx / distance) * force;
      displacement[source].y += (dy / distance) * force;
      displacement[target].x -= (dx / distance) * force;
      displacement[target].y -= (dy / distance) * force;
    });

    nodes.forEach((node, index) => {
      displacement[index].x += (centerX - node.x) * 0.03;
      displacement[index].y += (centerY - node.y) * 0.03;
    });

    nodes.forEach((node, index) => {
      const dx = displacement[index].x;
      const dy = displacement[index].y;
      const length = Math.hypot(dx, dy);
      if (length > 0) {
        const step = Math.min(length, temperature);
        node.x += (dx / length) * step;
        node.y += (dy / length) * step;
      }
      node.x = clamp(node.x, padding, width - padding - NODE_W);
      node.y = clamp(node.y, padding, height - padding - NODE_H);
    });
  }

  for (let pass = 0; pass < 140; pass += 1) {
    let moved = false;
    for (let left = 0; left < nodes.length; left += 1) {
      for (let right = left + 1; right < nodes.length; right += 1) {
        const overlapX = NODE_W + COLLISION_GAP - Math.abs(nodes[left].x - nodes[right].x);
        const overlapY = NODE_H + COLLISION_GAP - Math.abs(nodes[left].y - nodes[right].y);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        if (overlapX < overlapY) {
          const direction = nodes[left].x <= nodes[right].x ? -1 : 1;
          const push = (overlapX / 2 + 0.5) * direction;
          nodes[left].x += push;
          nodes[right].x -= push;
        } else {
          const direction = nodes[left].y <= nodes[right].y ? -1 : 1;
          const push = (overlapY / 2 + 0.5) * direction;
          nodes[left].y += push;
          nodes[right].y -= push;
        }
      }
    }
    if (!moved) break;
  }

  const minX = Math.min(...nodes.map((node) => node.x));
  const minY = Math.min(...nodes.map((node) => node.y));
  const offsetX = padding - minX;
  const offsetY = padding - minY;
  return nodes.map((node) => ({
    kpId: node.kpId,
    x: round1(node.x + offsetX),
    y: round1(node.y + offsetY),
    width: NODE_W,
    height: NODE_H,
  }));
}

/** 货架式打包：大的连通分量在前，单点在后，按行排布，避免整张图出现大片空洞。 */
function packComponentBoxes(
  boxes: ComponentBox[],
  maxWidth: number,
  gap: number,
): { width: number; height: number; nodes: LayoutNode[] } {
  const sorted = [...boxes].sort(
    (left, right) =>
      right.width * right.height - left.width * left.height ||
      left.key.localeCompare(right.key),
  );
  const nodes: LayoutNode[] = [];
  const rows: Array<{ start: number; end: number; width: number }> = [];
  let x = 0;
  let y = 0;
  let rowHeight = 0;
  let rowStart = 0;
  let contentWidth = 0;
  sorted.forEach((box) => {
    if (x > 0 && x + box.width > maxWidth) {
      rows.push({ start: rowStart, end: nodes.length, width: x - gap });
      x = 0;
      y += rowHeight + gap;
      rowHeight = 0;
      rowStart = nodes.length;
    }
    box.nodes.forEach((node) => {
      nodes.push({ ...node, x: round1(node.x + x), y: round1(node.y + y) });
    });
    x += box.width + gap;
    contentWidth = Math.max(contentWidth, x - gap);
    rowHeight = Math.max(rowHeight, box.height);
  });
  if (nodes.length > rowStart) {
    rows.push({ start: rowStart, end: nodes.length, width: x - gap });
  }
  rows.forEach((row) => {
    const shift = (contentWidth - row.width) / 2;
    if (shift === 0) return;
    for (let index = row.start; index < row.end; index += 1) {
      nodes[index] = { ...nodes[index], x: round1(nodes[index].x + shift) };
    }
  });
  return { width: contentWidth, height: y + rowHeight, nodes };
}

/**
 * 先按连通分量做局部力导向，再把分量打包进主题块，最后把主题块打包进画布。
 * 同一版本、同一主题范围与同一批节点得到完全相同的坐标。
 */
export function computeKnowledgeLayout(
  points: LayoutPoint[],
  relations: LayoutRelation[],
  options: LayoutOptions,
): KnowledgeLayout {
  const size = layoutSize(points.length);
  if (points.length === 0) {
    return { width: size.width, height: size.height, nodes: [] };
  }

  const ordered = [...points].sort((left, right) => left.kp_id.localeCompare(right.kp_id));
  const groupFor = (point: LayoutPoint): string =>
    options.groupOf?.[point.kp_id] ?? point.topic_ids[0] ?? UNCLASSIFIED_KEY;
  const groupNames = [...new Set(ordered.map(groupFor))].sort((left, right) => {
    if (left === UNCLASSIFIED_KEY) return 1;
    if (right === UNCLASSIFIED_KEY) return -1;
    return left.localeCompare(right);
  });

  const groupBlocks: ComponentBox[] = groupNames.map((group) => {
    const groupPoints = ordered.filter((point) => groupFor(point) === group);
    const components = findAllComponents(groupPoints, relations);
    const componentBoxes = components.map((component) => {
      const key = component.map((point) => point.kp_id).join("|");
      if (component.length === 1) {
        return {
          key,
          width: NODE_W,
          height: NODE_H,
          nodes: [{ kpId: component[0].kp_id, x: 0, y: 0, width: NODE_W, height: NODE_H }],
        };
      }
      const boxWidth = clamp(230 + component.length * 36, 300, 520);
      const boxHeight = clamp(150 + component.length * 24, 170, 340);
      const nodes = localForceLayout(
        component,
        relations,
        boxWidth,
        boxHeight,
        hashString(`${options.version}|${options.seedKey}|${group}|${key}`),
      );
      return {
        key,
        width: Math.max(...nodes.map((node) => node.x + node.width)),
        height: Math.max(...nodes.map((node) => node.y + node.height)),
        nodes,
      };
    });
    const groupMaxWidth =
      groupNames.length === 1 ? size.width - PADDING * 2 : Math.round(size.width * 0.64);
    const packed = packComponentBoxes(componentBoxes, groupMaxWidth, 24);
    return { key: group, width: packed.width, height: packed.height, nodes: packed.nodes };
  });

  const packedGroups = packComponentBoxes(groupBlocks, size.width - PADDING * 2, 40);
  const finalWidth = Math.max(size.width, Math.ceil(packedGroups.width + PADDING * 2));
  const finalHeight = Math.max(size.height, Math.ceil(packedGroups.height + PADDING * 2));
  const offsetX = (finalWidth - packedGroups.width) / 2;
  const offsetY = Math.max(PADDING, (finalHeight - packedGroups.height) / 2);

  return {
    width: finalWidth,
    height: finalHeight,
    nodes: packedGroups.nodes.map((node) => ({
      ...node,
      x: round1(node.x + offsetX),
      y: round1(node.y + offsetY),
    })),
  };
}

const layoutCache = new Map<string, KnowledgeLayout>();

function cachedLayout(
  points: LayoutPoint[],
  relations: LayoutRelation[],
  options: LayoutOptions,
): KnowledgeLayout {
  const groupSignature = Object.entries(options.groupOf ?? {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}:${value}`)
    .join(",");
  const key = [
    options.version,
    options.seedKey,
    points.map((point) => point.kp_id).sort().join(","),
    relations.map((relation) => `${(relation.source_ids ?? []).join("+")}>${relation.target_id}`).join(";"),
    groupSignature,
  ].join("|");
  const cached = layoutCache.get(key);
  if (cached) return cached;
  const layout = computeKnowledgeLayout(points, relations, options);
  layoutCache.set(key, layout);
  if (layoutCache.size > LAYOUT_CACHE_LIMIT) {
    const oldest = layoutCache.keys().next().value;
    if (oldest) layoutCache.delete(oldest);
  }
  return layout;
}

function relationType(relation: KnowledgeRelation): string {
  return (relation.relation_type ?? relation.type ?? "related").toLowerCase();
}

function relationEndpoints(relation: KnowledgeRelation): string[] {
  return [...(relation.source_ids ?? []), relation.target_id].filter(Boolean);
}

function truncateName(name: string): string {
  return name.length > 10 ? `${name.slice(0, 10)}…` : name;
}

function rectBoundary(node: LayoutNode, toward: LayoutNode): { x: number; y: number } {
  const centerX = node.x + node.width / 2;
  const centerY = node.y + node.height / 2;
  const dx = toward.x + toward.width / 2 - centerX;
  const dy = toward.y + toward.height / 2 - centerY;
  if (dx === 0 && dy === 0) return { x: centerX, y: centerY };
  const scale = 1 / Math.max(Math.abs(dx) / (node.width / 2), Math.abs(dy) / (node.height / 2));
  return { x: centerX + dx * scale, y: centerY + dy * scale };
}

function buildEdges(
  relations: KnowledgeRelation[],
  byId: Map<string, LayoutNode>,
  pointNames: Map<string, string>,
): Edge[] {
  return relations.flatMap((relation, relationIndex) => {
    const target = byId.get(relation.target_id);
    if (!target) return [];
    const type = relationType(relation);
    return (relation.source_ids ?? []).flatMap((sourceId, sourceIndex) => {
      if (sourceId === relation.target_id) return [];
      const source = byId.get(sourceId);
      if (!source) return [];
      const start = rectBoundary(source, target);
      const end = rectBoundary(target, source);
      const dx = end.x - start.x;
      const dy = end.y - start.y;
      const length = Math.max(1, Math.hypot(dx, dy));
      const direction = hashString(`${relationIndex}:${sourceId}:${relation.target_id}`) % 2 === 0 ? 1 : -1;
      const curve = Math.min(34, Math.max(10, length * 0.16)) * direction;
      const controlX = (start.x + end.x) / 2 + (-dy / length) * curve;
      const controlY = (start.y + end.y) / 2 + (dx / length) * curve;
      return [
        {
          key: `${relationIndex}-${sourceIndex}-${sourceId}-${relation.target_id}`,
          source,
          target,
          type,
          path: `M ${round1(start.x)} ${round1(start.y)} Q ${round1(controlX)} ${round1(controlY)} ${round1(end.x)} ${round1(end.y)}`,
          labelX: (start.x + 2 * controlX + end.x) / 4,
          labelY: (start.y + 2 * controlY + end.y) / 4 - 5,
          sourceName: pointNames.get(sourceId) ?? "知识点",
          targetName: pointNames.get(relation.target_id) ?? "知识点",
        },
      ];
    });
  });
}

/** 图只画正式关系的方向；课程归属（annotation）不画成逻辑边。布局由版本号固定种子。 */
export function KnowledgeGraph({ snapshot, catalog, selectedId, onSelect }: Props) {
  const [zoom, setZoom] = useState(1);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const viewportRef = useRef<HTMLDivElement | null>(null);

  const topicData = useMemo(() => {
    const titleById = new Map<string, string>();
    (catalog.topics ?? []).forEach((topic) => {
      const title = topic.title?.trim();
      if (title) titleById.set(topic.topic_id, title);
    });
    const accentByTopic = new Map<string, string>();
    (catalog.topics ?? []).forEach((topic, index) => {
      accentByTopic.set(topic.topic_id, TOPIC_ACCENTS[index % TOPIC_ACCENTS.length]);
    });
    accentByTopic.set(UNCLASSIFIED_KEY, "var(--color-text-muted)");

    const groupsByPoint = new Map<string, string[]>();
    const counts = new Map<string, number>();
    snapshot.points.forEach((point) => {
      const known = [...new Set(point.topic_ids ?? [])].filter((topicId) => titleById.has(topicId));
      const groups = known.length > 0 ? known : [UNCLASSIFIED_KEY];
      groupsByPoint.set(point.kp_id, groups);
      groups.forEach((group) => counts.set(group, (counts.get(group) ?? 0) + 1));
    });

    const tabs: Array<{ key: string; label: string; count: number; accent: string | null }> = [
      { key: ALL_KEY, label: "全部", count: snapshot.points.length, accent: null },
    ];
    (catalog.topics ?? []).forEach((topic) => {
      if (!titleById.has(topic.topic_id)) return;
      const count = counts.get(topic.topic_id) ?? 0;
      if (count === 0) return;
      tabs.push({
        key: topic.topic_id,
        label: titleById.get(topic.topic_id) as string,
        count,
        accent: accentByTopic.get(topic.topic_id) ?? TOPIC_ACCENTS[0],
      });
    });
    if ((counts.get(UNCLASSIFIED_KEY) ?? 0) > 0) {
      tabs.push({
        key: UNCLASSIFIED_KEY,
        label: UNCLASSIFIED_TOPIC_LABEL,
        count: counts.get(UNCLASSIFIED_KEY) ?? 0,
        accent: accentByTopic.get(UNCLASSIFIED_KEY) as string,
      });
    }
    const groupOf: Record<string, string> = {};
    snapshot.points.forEach((point) => {
      groupOf[point.kp_id] = groupsByPoint.get(point.kp_id)?.[0] ?? UNCLASSIFIED_KEY;
    });
    return { titleById, accentByTopic, groupsByPoint, tabs, groupOf };
  }, [snapshot.points, catalog.topics]);

  const defaultKey = topicData.tabs.find((tab) => tab.key !== ALL_KEY)?.key ?? ALL_KEY;
  const currentKey =
    activeKey && topicData.tabs.some((tab) => tab.key === activeKey) ? activeKey : defaultKey;
  const currentTab = topicData.tabs.find((tab) => tab.key === currentKey);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const update = () => setViewportWidth(element.clientWidth);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [snapshot.points.length]);

  const visiblePoints = useMemo(
    () =>
      currentKey === ALL_KEY
        ? snapshot.points
        : snapshot.points.filter((point) =>
            (topicData.groupsByPoint.get(point.kp_id) ?? []).includes(currentKey),
          ),
    [currentKey, snapshot.points, topicData.groupsByPoint],
  );

  const pointNames = useMemo(() => {
    const map = new Map<string, string>();
    snapshot.points.forEach((point) => map.set(point.kp_id, point.name));
    return map;
  }, [snapshot.points]);

  const visibleIds = useMemo(() => new Set(visiblePoints.map((point) => point.kp_id)), [visiblePoints]);

  const { internalRelations, crossTopicRelations, missingEndpointRelations } = useMemo(() => {
    const internal: KnowledgeRelation[] = [];
    const cross: KnowledgeRelation[] = [];
    const missing: KnowledgeRelation[] = [];
    snapshot.relations.forEach((relation) => {
      const endpoints = relationEndpoints(relation);
      const inside = endpoints.filter((id) => visibleIds.has(id));
      const allInside = endpoints.length > 0 && inside.length === endpoints.length;
      if (endpoints.some((id) => !pointNames.has(id))) missing.push(relation);
      if (allInside) {
        internal.push(relation);
      } else if (inside.length > 0) {
        cross.push(relation);
      }
    });
    return {
      internalRelations: internal,
      crossTopicRelations: cross,
      missingEndpointRelations: missing,
    };
  }, [snapshot.relations, visibleIds, pointNames]);

  const layout = useMemo(
    () =>
      cachedLayout(visiblePoints, internalRelations, {
        version: snapshot.knowledge_version,
        seedKey: currentKey,
        groupOf: topicData.groupOf,
      }),
    [visiblePoints, internalRelations, snapshot.knowledge_version, currentKey, topicData.groupOf],
  );

  const byId = useMemo(() => new Map(layout.nodes.map((node) => [node.kpId, node])), [layout.nodes]);
  const edges = useMemo(
    () => buildEdges(internalRelations, byId, pointNames),
    [internalRelations, byId, pointNames],
  );

  const fitZoom = useMemo(() => {
    const availableWidth = viewportWidth > 0 ? Math.max(320, viewportWidth - 20) : 760;
    const minimum = currentKey === ALL_KEY ? 0.5 : 0.75;
    const fit = Math.min(
      1,
      Math.max(minimum, Math.min(availableWidth / layout.width, 520 / layout.height)),
    );
    return Math.round(fit * 20) / 20;
  }, [layout.width, layout.height, viewportWidth, currentKey]);

  useEffect(() => {
    setZoom(fitZoom);
    setHoveredEdge(null);
  }, [currentKey, fitZoom]);

  const accentFor = (point: KnowledgePoint): string => {
    if (currentKey !== ALL_KEY && currentKey !== UNCLASSIFIED_KEY) {
      return topicData.accentByTopic.get(currentKey) ?? TOPIC_ACCENTS[0];
    }
    const group = topicData.groupsByPoint.get(point.kp_id)?.[0] ?? UNCLASSIFIED_KEY;
    return topicData.accentByTopic.get(group) ?? TOPIC_ACCENTS[0];
  };

  const topicLabelFor = (point: KnowledgePoint): string => {
    const group = topicData.groupsByPoint.get(point.kp_id)?.[0] ?? UNCLASSIFIED_KEY;
    const title = topicData.titleById.get(group);
    return title ? truncateName(title) : UNCLASSIFIED_TOPIC_LABEL;
  };

  const scopeLabel = currentTab?.label ?? "全部";
  const zoomPercent = Math.round(zoom * 100);

  if (snapshot.points.length === 0) {
    return (
      <div className="graphBox">
        <p className="graphEmpty">这个知识版本没有知识点。</p>
      </div>
    );
  }

  return (
    <div className="stack graphRoot">
      <div className="graphTabs" role="tablist" aria-label="按主题查看知识图谱">
        {topicData.tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={currentKey === tab.key}
            className={currentKey === tab.key ? "graphTab graphTabActive" : "graphTab"}
            onClick={() => setActiveKey(tab.key)}
          >
            {tab.accent ? (
              <span className="graphTabDot" style={{ background: tab.accent }} aria-hidden="true" />
            ) : null}
            <span>{tab.label}</span>
            <span className="graphTabCount">{tab.count}</span>
          </button>
        ))}
      </div>

      <div className="rowBetween graphToolbar">
        <span className="small muted">
          {scopeLabel} · {visiblePoints.length} 个知识点 ·{" "}
          {currentKey === ALL_KEY ? "可绘制" : "内部"} {internalRelations.length} 条关系 · 全量{" "}
          {snapshot.relations.length} 条正式关系 · 缩放 {zoomPercent}%
        </span>
        <div className="row">
          <button
            type="button"
            className="btn btnSm"
            onClick={() => setZoom((value) => Math.max(0.5, Math.round((value - 0.1) * 10) / 10))}
            disabled={zoom <= 0.5}
          >
            缩小
          </button>
          <button type="button" className="btn btnSm" onClick={() => setZoom(fitZoom)}>
            重置视图
          </button>
          <button
            type="button"
            className="btn btnSm"
            onClick={() => setZoom((value) => Math.min(2, Math.round((value + 0.1) * 10) / 10))}
            disabled={zoom >= 2}
          >
            放大
          </button>
        </div>
      </div>

      {currentKey !== ALL_KEY && crossTopicRelations.length > 0 ? (
        <p className="graphNotice" role="status">
          <span>
            另有 {crossTopicRelations.length} 条跨主题联系，本主题图内不绘制，避免把跨主题的长线混进局部网络。
          </span>
          <button
            type="button"
            className="btn btnSm btnGhost"
            onClick={() => setActiveKey(ALL_KEY)}
          >
            切到「全部」查看
          </button>
        </p>
      ) : null}

      {currentKey === ALL_KEY && snapshot.relations.length > 0 ? (
        <p className="graphNotice graphNoticeMuted" role="status">
          <span>
            全部视图保留 {snapshot.relations.length} 条正式关系
            {missingEndpointRelations.length > 0
              ? `；其中 ${missingEndpointRelations.length} 条关系的端点未出现在当前知识版本，图内不绘制，右侧详情仍保留原始记录。`
              : "，跨主题边也在其中。"}
          </span>
        </p>
      ) : null}

      <div className="graphBox">
        {snapshot.relations.length === 0 ? (
          <p className="graphEmpty">
            暂无正式关系：当前知识版本没有登记 prerequisite / related 关系边，不绘制占位线。
          </p>
        ) : null}
        {snapshot.relations.length > 0 && internalRelations.length === 0 ? (
          <p className="graphEmpty">
            {currentKey === ALL_KEY
              ? "当前知识版本的关系端点都不在图内，未绘制占位线。"
              : "当前主题内部没有关系边；跨主题联系见上方提示。"}
          </p>
        ) : null}

        <div
          className="graphViewport"
          ref={viewportRef}
          tabIndex={0}
          role="group"
          aria-label={`${scopeLabel}知识图谱画布，可滚动查看，使用上方按钮缩放`}
        >
          <svg
            className="graphSvg"
            viewBox={`0 0 ${layout.width} ${layout.height}`}
            style={{ width: layout.width * zoom, height: layout.height * zoom }}
            role="img"
            aria-label={`${scopeLabel}知识图谱：${visiblePoints.length} 个知识点，${internalRelations.length} 条内部关系`}
          >
            <defs>
              <marker
                id="knowledge-graph-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--color-text-secondary)" />
              </marker>
            </defs>

            {edges.map((edge) => {
              const hovered = hoveredEdge === edge.key;
              const related =
                Boolean(selectedId) &&
                (edge.source.kpId === selectedId || edge.target.kpId === selectedId);
              const emphasized = hovered || related;
              return (
                <g
                  key={edge.key}
                  data-edge={edge.key}
                  onMouseEnter={() => setHoveredEdge(edge.key)}
                  onMouseLeave={() =>
                    setHoveredEdge((value) => (value === edge.key ? null : value))
                  }
                >
                  <title>
                    {edge.sourceName} → {edge.targetName} · {relationTypeLabel(edge.type)}
                  </title>
                  <path
                    d={edge.path}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={14}
                    pointerEvents="stroke"
                    aria-hidden="true"
                  />
                  <path
                    d={edge.path}
                    fill="none"
                    stroke={emphasized ? "var(--color-primary)" : "var(--color-text-secondary)"}
                    strokeOpacity={emphasized ? 1 : 0.6}
                    strokeWidth={edge.type === "prerequisite" ? 1.75 : 1.2}
                    strokeDasharray={edge.type === "prerequisite" ? undefined : "5 4"}
                    markerEnd="url(#knowledge-graph-arrow)"
                  />
                  {emphasized ? (
                    <text
                      x={edge.labelX}
                      y={edge.labelY}
                      textAnchor="middle"
                      fontSize={10}
                      fill="var(--color-text-secondary)"
                      stroke="var(--color-background)"
                      strokeWidth={3}
                      paintOrder="stroke"
                    >
                      {relationTypeLabel(edge.type)}
                    </text>
                  ) : null}
                </g>
              );
            })}

            {visiblePoints.map((point) => {
              const node = byId.get(point.kp_id);
              if (!node) return null;
              const selected = point.kp_id === selectedId;
              const accent = accentFor(point);
              return (
                <g
                  key={point.kp_id}
                  data-kp-id={point.kp_id}
                  role="button"
                  tabIndex={0}
                  aria-label={`知识点 ${point.name}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => onSelect(point.kp_id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onSelect(point.kp_id);
                    }
                  }}
                >
                  <title>{point.name}</title>
                  <rect
                    x={node.x}
                    y={node.y}
                    width={node.width}
                    height={node.height}
                    rx={6}
                    fill={selected ? "var(--color-selected-bg)" : "var(--color-background)"}
                    stroke={selected ? "var(--color-selected-border)" : "var(--color-border)"}
                    strokeWidth={selected ? 1.75 : 1}
                  />
                  <rect x={node.x} y={node.y} width={3} height={node.height} rx={1.5} fill={accent} />
                  <text
                    x={node.x + 10}
                    y={node.y + 17}
                    fontSize={12}
                    fill="var(--color-text-primary)"
                  >
                    {truncateName(point.name)}
                  </text>
                  <text
                    x={node.x + 10}
                    y={node.y + 33}
                    fontSize={10}
                    fill="var(--color-text-secondary)"
                  >
                    {currentKey === ALL_KEY ? topicLabelFor(point) : knowledgeScopeLabel(point.scope_role)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>

        <div className="graphLegend">
          <span className="legendLine">
            <svg width="28" height="8" aria-hidden="true">
              <line
                x1="0"
                y1="4"
                x2="28"
                y2="4"
                stroke="var(--color-text-secondary)"
                strokeWidth="1.75"
              />
            </svg>
            前置（实线箭头）
          </span>
          <span className="legendLine">
            <svg width="28" height="8" aria-hidden="true">
              <line
                x1="0"
                y1="4"
                x2="28"
                y2="4"
                stroke="var(--color-text-secondary)"
                strokeWidth="1"
                strokeDasharray="5 4"
              />
            </svg>
            其他正式关系（虚线箭头）
          </span>
          <span>悬停边或选中节点才显示关系类型；方向严格为来源 → 目标。</span>
          <span>内容超出画布时可滚动查看，用上方按钮在 0.5—2 倍之间缩放；同一知识版本布局固定。</span>
        </div>
      </div>

      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>
          键盘可访问的知识点列表（当前范围 {visiblePoints.length} 个）
        </summary>
        <ul className="stackSm" style={{ marginTop: 8 }}>
          {visiblePoints.map((point) => (
            <li key={point.kp_id}>
              <button
                type="button"
                className="btn btnSm btnGhost"
                onClick={() => onSelect(point.kp_id)}
              >
                {point.name}
              </button>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
