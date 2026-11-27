import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { Catalog, KnowledgePoint, KnowledgeRelation, KnowledgeSnapshotView } from "../../api/types";
import { UNCLASSIFIED_TOPIC_LABEL } from "../../lib/labels";
import {
  computeKnowledgeLayout,
  KnowledgeGraph,
  type LayoutPoint,
  type LayoutRelation,
} from "./KnowledgeGraph";

const CATALOG: Catalog = {
  catalog_version: "catalog-test",
  knowledge_version: "kn-test",
  topics: [
    { topic_id: "T01", title: "代数式与整式运算", goal: "" },
    { topic_id: "T02", title: "方程与方程组", goal: "" },
    { topic_id: "T03", title: "不等式与不等式组", goal: "" },
  ],
  indicators: [],
  materials: [],
  links: [],
};

function point(kpId: string, name: string, topicIds: string[], scopeRole = "core"): KnowledgePoint {
  return {
    kp_id: kpId,
    name,
    aliases: [],
    kind: "concept",
    definition: name,
    scope_role: scopeRole,
    topic_ids: topicIds,
    indicator_ids: [],
    snippet: name,
    source_locators: [],
    review_info: null,
    content_hash: kpId,
  };
}

function relation(sourceIds: string[], targetId: string, type = "prerequisite"): KnowledgeRelation {
  return {
    relation_id: `rel-${sourceIds.join("-")}-${targetId}`,
    source_ids: sourceIds,
    target_id: targetId,
    type,
  };
}

function snapshot(
  points: KnowledgePoint[],
  relations: KnowledgeRelation[] = [],
): KnowledgeSnapshotView {
  return {
    dataset_id: "ds-test",
    catalog_version: "catalog-test",
    knowledge_version: "kn-test",
    parent_version: null,
    status: "published",
    points,
    relations,
    annotations: [],
    created_at: "2026-11-27T00:00:00+00:00",
    review_summary: {},
    limitations: [],
  };
}

function renderGraph(data: KnowledgeSnapshotView) {
  const onSelect = vi.fn();
  const utils = render(
    <KnowledgeGraph snapshot={data} catalog={CATALOG} selectedId={null} onSelect={onSelect} />,
  );
  return { ...utils, onSelect };
}

function visibleKpIds(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("[data-kp-id]"))
    .map((element) => element.getAttribute("data-kp-id"))
    .filter((value): value is string => Boolean(value))
    .sort();
}

describe("KnowledgeGraph 主题分组", () => {
  it("按目录主题生成标签页和计数，默认显示第一个主题的局部图", () => {
    const data = snapshot(
      [
        point("kp-T01I1", "表示数量关系", ["T01"]),
        point("kp-T01I2", "合并同类项", ["T01"]),
        point("kp-T02I1", "解一元一次方程", ["T02"]),
      ],
      [relation(["kp-T01I1"], "kp-T01I2")],
    );
    const { container } = renderGraph(data);

    expect(screen.getByRole("tab", { name: /全部/ })).toHaveTextContent(/3/);
    expect(screen.getByRole("tab", { name: /代数式与整式运算/ })).toHaveTextContent(/2/);
    expect(screen.getByRole("tab", { name: /方程与方程组/ })).toHaveTextContent(/1/);
    expect(screen.queryByRole("tab", { name: /不等式与不等式组/ })).not.toBeInTheDocument();

    expect(screen.getByRole("tab", { name: /代数式与整式运算/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(visibleKpIds(container)).toEqual(["kp-T01I1", "kp-T01I2"]);

    fireEvent.click(screen.getByRole("tab", { name: /全部/ }));
    expect(visibleKpIds(container)).toEqual(["kp-T01I1", "kp-T01I2", "kp-T02I1"]);

    fireEvent.click(screen.getByRole("tab", { name: /方程与方程组/ }));
    expect(visibleKpIds(container)).toEqual(["kp-T02I1"]);
  });

  it("节点可点击，选中回调携带知识点编号", () => {
    const data = snapshot([point("kp-T01I1", "表示数量关系", ["T01"])]);
    const { container, onSelect } = renderGraph(data);
    const node = container.querySelector('[data-kp-id="kp-T01I1"]');
    expect(node).not.toBeNull();
    fireEvent.click(node as Element);
    expect(onSelect).toHaveBeenCalledWith("kp-T01I1");
  });
});

describe("KnowledgeGraph 跨主题关系", () => {
  it("主题视图提示跨主题联系，全部视图保留所有边", () => {
    const data = snapshot(
      [
        point("kp-T01I1", "表示数量关系", ["T01"]),
        point("kp-T01I2", "合并同类项", ["T01"]),
        point("kp-T02I1", "解一元一次方程", ["T02"]),
        point("kp-T02I2", "解二元一次方程组", ["T02"]),
      ],
      [
        relation(["kp-T01I1"], "kp-T01I2"),
        relation(["kp-T01I2"], "kp-T02I1"),
        relation(["kp-T02I1"], "kp-T02I2"),
      ],
    );
    const { container } = renderGraph(data);

    expect(screen.getByText(/另有 1 条跨主题联系/)).toBeInTheDocument();
    expect(container.querySelectorAll("[data-edge]")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /切到「全部」查看/ }));

    expect(screen.getByRole("tab", { name: /全部/ })).toHaveAttribute("aria-selected", "true");
    expect(visibleKpIds(container)).toEqual(["kp-T01I1", "kp-T01I2", "kp-T02I1", "kp-T02I2"]);
    expect(container.querySelectorAll("[data-edge]")).toHaveLength(3);
    expect(screen.queryByText(/另有 1 条跨主题联系/)).not.toBeInTheDocument();
    expect(screen.getByText(/全部视图保留 3 条正式关系/)).toBeInTheDocument();
  });

  it("关系端点不在当前主题时按跨主题处理，不画半截边", () => {
    const data = snapshot(
      [point("kp-T01I1", "表示数量关系", ["T01"]), point("kp-T02I1", "解一元一次方程", ["T02"])],
      [relation(["kp-T01I1"], "kp-UNKNOWN")],
    );
    const { container } = renderGraph(data);

    expect(screen.getByText(/另有 1 条跨主题联系/)).toBeInTheDocument();
    expect(container.querySelectorAll("[data-edge]")).toHaveLength(0);
    expect(container.querySelector('[data-kp-id="kp-T01I1"]')).not.toBeNull();
  });
});

describe("KnowledgeGraph 新数据兜底", () => {
  it("没有 topic_ids 与未知主题的知识点都进入未归类并仍然显示", () => {
    const data = snapshot([
      point("kp-X1", "没有主题的知识点", []),
      point("kp-X2", "未知主题的知识点", ["T99"]),
      point("kp-X3", "同时带未知主题和有效主题", ["T02", "T99"]),
    ]);
    const { container } = renderGraph(data);

    const unclassifiedTab = screen.getByRole("tab", { name: new RegExp(UNCLASSIFIED_TOPIC_LABEL) });
    expect(unclassifiedTab).toHaveTextContent(/2/);
    fireEvent.click(unclassifiedTab);
    expect(visibleKpIds(container)).toEqual(["kp-X1", "kp-X2"]);

    fireEvent.click(screen.getByRole("tab", { name: /方程与方程组/ }));
    expect(visibleKpIds(container)).toEqual(["kp-X3"]);
  });

  it("relations 为空时显示明确空态，不画假线", () => {
    const data = snapshot([
      point("kp-T01I1", "表示数量关系", ["T01"]),
      point("kp-T01I2", "合并同类项", ["T01"]),
    ]);
    const { container } = renderGraph(data);

    expect(screen.getByText(/暂无正式关系/)).toBeInTheDocument();
    expect(container.querySelectorAll("[data-edge]")).toHaveLength(0);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});

describe("KnowledgeGraph 确定性布局", () => {
  const points: LayoutPoint[] = Array.from({ length: 28 }, (_, index) => ({
    kp_id: `kp-${String(index).padStart(2, "0")}`,
    topic_ids: ["T03"],
  }));
  const relations: LayoutRelation[] = Array.from({ length: 19 }, (_, index) => ({
    source_ids: [`kp-${String(index % 14).padStart(2, "0")}`],
    target_id: `kp-${String(14 + (index % 14)).padStart(2, "0")}`,
  }));

  it("同一知识版本与同一主题范围重复计算得到相同坐标", () => {
    const first = computeKnowledgeLayout(points, relations, {
      version: "kn-stable",
      seedKey: "T03",
    });
    const second = computeKnowledgeLayout(points, relations, {
      version: "kn-stable",
      seedKey: "T03",
    });

    expect(second).toEqual(first);
    expect(first.nodes).toHaveLength(28);
    expect(new Set(first.nodes.map((node) => Math.round(node.x))).size).toBeGreaterThan(5);
  });

  it("节点矩形互不重叠，画布能容纳全部节点", () => {
    const layout = computeKnowledgeLayout(points, relations, {
      version: "kn-stable",
      seedKey: "T03",
    });

    for (let left = 0; left < layout.nodes.length; left += 1) {
      for (let right = left + 1; right < layout.nodes.length; right += 1) {
        const a = layout.nodes[left];
        const b = layout.nodes[right];
        const overlapX = a.x < b.x + b.width && b.x < a.x + a.width;
        const overlapY = a.y < b.y + b.height && b.y < a.y + a.height;
        expect(overlapX && overlapY, `${a.kpId} 与 ${b.kpId} 重叠`).toBe(false);
      }
    }

    layout.nodes.forEach((node) => {
      expect(node.x).toBeGreaterThanOrEqual(0);
      expect(node.y).toBeGreaterThanOrEqual(0);
      expect(node.x + node.width).toBeLessThanOrEqual(layout.width);
      expect(node.y + node.height).toBeLessThanOrEqual(layout.height);
    });
  });
});
