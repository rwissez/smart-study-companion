import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { getNodes, listDatasets, listRuns, type NodeOption } from "../api/service";
import { queryKeys } from "../api/queryKeys";
import { useSession } from "./session";

interface ScopeValue {
  datasetId: string | null;
  runId: string | null;
  nodeId: string | null;
  nodes: NodeOption[];
  datasets: Array<{ dataset_id: string; status: string }>;
  runs: Array<{ run_id: string; status: string }>;
  setDatasetId: (id: string) => void;
  setRunId: (id: string) => void;
  setNodeId: (id: string) => void;
  ready: boolean;
  bootstrapError: string | null;
}

const ScopeContext = createContext<ScopeValue | null>(null);

/**
 * 版本范围：数据集 → 运行 → 节点。
 * 换数据集清空运行与节点，换运行清空节点，换节点由页面自行清理依附内容。
 */
export function ScopeProvider({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const [datasetId, setDatasetIdState] = useState<string | null>(null);
  const [runId, setRunIdState] = useState<string | null>(null);
  const [nodeId, setNodeIdState] = useState<string | null>(null);

  const datasets = useQuery({
    queryKey: queryKeys.datasets(session),
    queryFn: async () => {
      const options = await listDatasets();
      return session?.scope.dataset_id && session.role === "student"
        ? options.filter((item) => item.dataset_id === session.scope.dataset_id)
        : options;
    },
    enabled: Boolean(session),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!datasetId && datasets.data && datasets.data.length > 0) {
      setDatasetIdState(datasets.data[0].dataset_id);
    }
  }, [datasets.data, datasetId]);

  const runs = useQuery({
    queryKey: queryKeys.runs(session, datasetId),
    queryFn: () => listRuns(datasetId as string),
    enabled: Boolean(session && datasetId),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!runId && runs.data && runs.data.length > 0) {
      const ready = runs.data.find((r) => r.status === "succeeded") ?? runs.data[0];
      setRunIdState(ready.run_id);
    }
  }, [runs.data, runId]);

  const nodes = useQuery({
    queryKey: [...queryKeys.all, "nodes", runId] as const,
    queryFn: () => getNodes(runId as string),
    enabled: Boolean(runId),
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!nodeId && nodes.data && nodes.data.length > 0) {
      setNodeIdState(nodes.data[0].node_id);
    }
  }, [nodes.data, nodeId]);

  const setDatasetId = (id: string) => {
    if (id === datasetId) return;
    setDatasetIdState(id);
    setRunIdState(null);
    setNodeIdState(null);
  };

  const setRunId = (id: string) => {
    if (id === runId) return;
    setRunIdState(id);
    setNodeIdState(null);
  };

  const value = useMemo<ScopeValue>(
    () => ({
      datasetId,
      runId,
      nodeId,
      nodes: nodes.data ?? [],
      datasets: (datasets.data ?? []).map((d) => ({
        dataset_id: d.dataset_id,
        status: d.status,
      })),
      runs: (runs.data ?? []).map((r) => ({ run_id: r.run_id, status: r.status })),
      setDatasetId,
      setRunId,
      setNodeId: setNodeIdState,
      ready: Boolean(datasetId && runId && nodeId),
      bootstrapError: datasets.error
        ? (datasets.error as Error).message
        : runs.error
          ? (runs.error as Error).message
          : nodes.error
            ? (nodes.error as Error).message
            : null,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      datasetId,
      runId,
      nodeId,
      nodes.data,
      datasets.data,
      runs.data,
      datasets.error,
      runs.error,
      nodes.error,
    ],
  );

  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeValue {
  const value = useContext(ScopeContext);
  if (!value) throw new Error("useScope 必须在 ScopeProvider 内使用");
  return value;
}
