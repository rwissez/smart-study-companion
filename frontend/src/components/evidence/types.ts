import type { EvidenceRef } from "../../api/types";

/** 证据查看上下文：服务端按角色与范围裁剪，前端只原样传递。 */
export interface EvidenceContext {
  student_id?: string | null;
  class_id?: string | null;
  activity_id?: string | null;
  plan_version?: string | null;
}

/**
 * 引用去重键：同一 kind、id 与精确版本才算同一条。
 * 列表展示与抽屉入栈共用，避免两处对“同一条引用”的判断不一致。
 */
export function evidenceRefKey(ref: EvidenceRef): string {
  return [
    ref.kind,
    ref.id,
    ref.run_id ?? "",
    ref.analysis_version ?? "",
    ref.catalog_version ?? "",
    ref.knowledge_version ?? "",
    ref.plan_version ?? "",
    ref.material_id ?? "",
    ref.attempt_id ?? "",
  ].join(":");
}
