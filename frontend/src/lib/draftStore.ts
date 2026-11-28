/**
 * 作答草稿：按 会话代次 + 数据来源 + 数据集 + 学生 + 方案版本 + 活动 + 尝试 隔离。
 * 只在当前标签页的 sessionStorage 中保存文本，不写密钥、标准答案或任何服务端裁剪内容。
 */
export interface DraftScope {
  mode: string;
  epoch: number;
  datasetId: string | null;
  studentId: string | null;
  planVersion: string;
  activityId: string;
  attemptId: string;
}

function draftKey(scope: DraftScope): string {
  return [
    "zz-draft",
    scope.mode,
    scope.epoch,
    scope.datasetId ?? "no-dataset",
    scope.studentId ?? "no-student",
    scope.planVersion,
    scope.activityId,
    scope.attemptId,
  ].join(":");
}

export interface AttemptDraft {
  finalAnswer: string;
  solutionSteps: string;
  choice: string;
}

export function readDraft(scope: DraftScope): AttemptDraft | null {
  try {
    const raw = window.sessionStorage.getItem(draftKey(scope));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AttemptDraft>;
    return {
      finalAnswer: typeof parsed.finalAnswer === "string" ? parsed.finalAnswer : "",
      solutionSteps: typeof parsed.solutionSteps === "string" ? parsed.solutionSteps : "",
      choice: typeof parsed.choice === "string" ? parsed.choice : "",
    };
  } catch {
    return null;
  }
}

export function writeDraft(scope: DraftScope, draft: AttemptDraft): void {
  try {
    window.sessionStorage.setItem(draftKey(scope), JSON.stringify(draft));
  } catch {
    // 浏览器限制存储时不影响作答，草稿仅保留在内存。
  }
}

export function clearDraft(scope: DraftScope): void {
  try {
    window.sessionStorage.removeItem(draftKey(scope));
  } catch {
    // 同上。
  }
}
