import { useScope } from "../../app/scope";
import styles from "./ScopeBar.module.css";

/** 当前分析范围；高级数据选择收纳在可展开的设置中。 */
export function ScopeBar() {
  const { datasetId, runId, nodeId, nodes, datasets, runs, setDatasetId, setRunId, setNodeId } = useScope();
  const currentNode = nodes.find((node) => node.node_id === nodeId);
  return <div className={styles.scope}>
    <details className={styles.settings}>
      <summary>数据范围</summary>
      <div className={styles.popover}>
        <label>数据集<select value={datasetId ?? ""} onChange={(event) => setDatasetId(event.target.value)}>{datasets.map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.dataset_id}</option>)}</select></label>
        <label>分析结果<select value={runId ?? ""} onChange={(event) => setRunId(event.target.value)}>{runs.map((item) => <option key={item.run_id} value={item.run_id}>{item.run_id}（{item.status === "succeeded" ? "已完成" : item.status}）</option>)}</select></label>
        {currentNode?.teaching_scope && <p>测验范围：{currentNode.teaching_scope}</p>}
      </div>
    </details>
    <label className={styles.node}><span>测验</span><select aria-label="测验节点" value={nodeId ?? ""} onChange={(event) => setNodeId(event.target.value)}>{nodes.map((node) => <option key={node.node_id} value={node.node_id}>{node.name}</option>)}</select></label>
  </div>;
}
