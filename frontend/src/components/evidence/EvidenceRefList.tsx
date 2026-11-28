import { useEffect, useState } from "react";
import { isAbortError } from "../../api/client";
import { resolveEvidence } from "../../api/service";
import type { EvidenceDetail, EvidenceRef } from "../../api/types";
import { evidenceKindLabel } from "./EvidenceDrawer";
import { evidenceRefKey, type EvidenceContext } from "./types";

interface Props {
  refs: EvidenceRef[];
  context?: EvidenceContext;
  materialNames?: Record<string, string>;
  onOpen: (ref: EvidenceRef) => void;
}

type SummaryState = { status: "ready"; detail: EvidenceDetail } | { status: "error" };

const SUMMARY_CONCURRENCY = 4;

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function recordStudent(detail: EvidenceDetail | null): string | null {
  return detail ? str(detail.detail.student_id) : null;
}

function recordMaterialId(detail: EvidenceDetail | null): string | null {
  if (!detail) return null;
  const question = detail.detail.question;
  if (question && typeof question === "object") {
    const materialId = str((question as Record<string, unknown>).material_id);
    if (materialId) return materialId;
  }
  const material = detail.detail.material;
  if (material && typeof material === "object") {
    return str((material as Record<string, unknown>).material_id);
  }
  return null;
}

/** 并发有限地读取摘要：列表可能很长，避免一次打开就打出上百个请求。 */
async function loadSummaries(
  refs: EvidenceRef[],
  context: EvidenceContext | undefined,
  signal: AbortSignal,
  onResult: (key: string, state: SummaryState) => void,
): Promise<void> {
  let cursor = 0;
  const worker = async () => {
    while (!signal.aborted) {
      const index = cursor;
      cursor += 1;
      if (index >= refs.length) return;
      const ref = refs[index];
      try {
        const detail = await resolveEvidence(ref, context, signal);
        if (!signal.aborted) onResult(evidenceRefKey(ref), { status: "ready", detail });
      } catch (reason) {
        if (!isAbortError(reason) && !signal.aborted) {
          onResult(evidenceRefKey(ref), { status: "error" });
        }
      }
    }
  };
  const workerCount = Math.min(SUMMARY_CONCURRENCY, refs.length);
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
}

/**
 * 指标/发现/进展的完整依据列表。
 * 只负责导航：点击某条把原引用交给现有 EvidenceDrawer，版本字段不改写。
 */
export function EvidenceRefList({ refs, context, materialNames, onOpen }: Props) {
  const [summaries, setSummaries] = useState<Record<string, SummaryState>>({});
  const refsSignature = refs.map(evidenceRefKey).join("|");
  const contextSignature = JSON.stringify(context ?? null);

  useEffect(() => {
    const controller = new AbortController();
    setSummaries({});
    void loadSummaries(refs, context, controller.signal, (key, state) => {
      setSummaries((previous) => ({ ...previous, [key]: state }));
    });
    return () => controller.abort();
    // 用内容签名而不是对象引用做依赖：父组件重渲染时不必重发摘要请求。
  }, [refsSignature, contextSignature]);

  return (
    <div className="tableWrap" style={{ borderTop: 0 }}>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">编号</th>
            <th scope="col">类型</th>
            <th scope="col">对应学生 / 材料</th>
            <th scope="col">当前记录摘要</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          {refs.map((ref, index) => {
            const key = evidenceRefKey(ref);
            const state = summaries[key];
            const detail = state?.status === "ready" ? state.detail : null;
            const student = context?.student_id ?? recordStudent(detail);
            const materialId = ref.material_id ?? recordMaterialId(detail);
            const material = materialId ? materialNames?.[materialId] ?? materialId : null;
            const locatorParts: string[] = [];
            if (student) locatorParts.push(`学生 ${student}`);
            if (material) locatorParts.push(`材料 ${material}`);
            return (
              <tr
                key={`${key}:${index}`}
                style={{ cursor: "pointer" }}
                onClick={() => onOpen(ref)}
              >
                <td className="small tabular">{ref.id}</td>
                <td className="small">{evidenceKindLabel(ref.kind)}</td>
                <td className="small">{locatorParts.length ? locatorParts.join(" · ") : "—"}</td>
                <td className="small">
                  {state ? (state.status === "ready" ? state.detail.summary ?? "—" : "摘要读取失败，仍可查看详情") : "正在读取摘要…"}
                </td>
                <td>
                  <button
                    type="button"
                    className="btn btnSm btnGhost"
                    aria-label={`查看第 ${index + 1} 条依据（${ref.id}）详情`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpen(ref);
                    }}
                  >
                    查看详情
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
