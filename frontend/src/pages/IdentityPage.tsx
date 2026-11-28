import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { errorCode, errorRequestId, setAuthToken, toErrorMessage } from "../api/client";
import {
  createSession,
  getDemoRoster,
  isDemo,
  listDatasets,
} from "../api/service";
import type { DatasetSummary, DemoRoster } from "../api/types";
import { useSession } from "../app/session";
import { DemoBadge } from "../components/feedback/DemoBadge";
import { StatePanel } from "../components/feedback/StatePanel";
import { IconArrowRight, IconClasses, IconUser } from "../components/icons";
import { datasetStatusLabel } from "../lib/labels";

/**
 * 身份入口：白底居中双栏。左品牌与结构示意，右教师/学生两个入口。
 * 学生入口从名册（demo 模式受控接口）选择已登记身份，再创建会话。
 */
export function IdentityPage() {
  const navigate = useNavigate();
  const { signIn } = useSession();
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<unknown>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [roster, setRoster] = useState<DemoRoster | null>(null);
  const [classId, setClassId] = useState<string | null>(null);
  const [studentId, setStudentId] = useState<string | null>(null);
  const [mode, setMode] = useState<"none" | "teacher" | "student">("none");
  const [busy, setBusy] = useState(false);
  const [entryError, setEntryError] = useState<{ text: string; requestId: string | null } | null>(null);

  const load = useCallback(async () => {
    setStatus("loading");
    setError(null);
    try {
      const bootstrapToken = async () => {
        if (isDemo) return;
        const bootstrap = await createSession("teacher");
        setAuthToken(bootstrap.session_token);
      };
      await bootstrapToken();
      // 身份页可能在退出会话的瞬间挂载；若请求以“缺少会话令牌”失败，重建一次受限会话再试。
      const withBootstrapRetry = async <T,>(run: () => Promise<T>): Promise<T> => {
        try {
          return await run();
        } catch (reason) {
          if (errorCode(reason) === "FORBIDDEN") {
            await bootstrapToken();
            return run();
          }
          throw reason;
        }
      };
      const list = await withBootstrapRetry(() => listDatasets());
      setDatasets(list);
      const first = list[0]?.dataset_id ?? null;
      setDatasetId(first);
      if (first) {
        const nextRoster = await withBootstrapRetry(() => getDemoRoster(first));
        setRoster(nextRoster);
        setClassId(nextRoster.classes[0]?.class_id ?? null);
        setStudentId(nextRoster.classes[0]?.student_ids[0] ?? null);
      } else {
        setRoster(null);
      }
      setStatus("ready");
    } catch (reason) {
      setError(reason);
      setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const chooseDataset = async (nextDatasetId: string) => {
    setDatasetId(nextDatasetId);
    setEntryError(null);
    try {
      const nextRoster = await getDemoRoster(nextDatasetId);
      setRoster(nextRoster);
      setClassId(nextRoster.classes[0]?.class_id ?? null);
      setStudentId(nextRoster.classes[0]?.student_ids[0] ?? null);
    } catch (reason) {
      setEntryError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    }
  };

  const enter = async (role: "teacher" | "student") => {
    if (role === "student" && !studentId) {
      setEntryError({ text: "请先在名册里选择一名学生。", requestId: null });
      return;
    }
    setBusy(true);
    setEntryError(null);
    try {
      await signIn(role, {
        datasetId,
        studentId: role === "student" ? studentId : null,
      });
      navigate(role === "teacher" ? "/teacher/classes" : "/student/profile", { replace: true });
    } catch (reason) {
      setEntryError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
    } finally {
      setBusy(false);
    }
  };

  const datasetLabel = datasets.find((item) => item.dataset_id === datasetId);
  const classes = roster?.classes ?? [];

  return (
    <div className="identityPage">
      <div className="identityGrid">
        <div className="identityBrand">
          <img className="identityLogo" src="/brand-logo.png" alt="湛知" width={160} height={52} />
          <div className="identityDiagram" aria-label="产品结构示意：数据导入到学情分析到学习方案">
            <span className="diagramNode">数据导入</span>
            <span className="diagramArrow"><IconArrowRight size={16} /></span>
            <span className="diagramNode">学情分析</span>
            <span className="diagramArrow"><IconArrowRight size={16} /></span>
            <span className="diagramNode">学习方案</span>
          </div>
          <p className="identityHint">
            教师看班级与个人学情，学生看自己的诊断与下一步任务；所有结论来自服务端的正式版本。
          </p>
          <DemoBadge />
        </div>

        <div className="identityOptions">
          <h1 className="identityOptionsTitle">选择身份</h1>

          {status === "loading" ? <StatePanel kind="loading" title="正在读取可用身份" /> : null}

          {status === "error" ? (
            <StatePanel
              kind="error"
              title="无法连接后端服务"
              message={
                <>
                  {toErrorMessage(error)}
                  <br />
                  当前数据来源是「真实接口」，不会回退到演示数据。请先启动后端：`uv run uvicorn study_companion.main:app --port 8000`。
                </>
              }
              requestId={errorRequestId(error)}
              onRetry={() => void load()}
            />
          ) : null}

          {status === "ready" ? (
            <>
              <div className="field">
                <label className="fieldLabel" htmlFor="identity-dataset">数据集</label>
                <select
                  id="identity-dataset"
                  className="select"
                  value={datasetId ?? ""}
                  onChange={(event) => void chooseDataset(event.target.value)}
                >
                  {datasets.length === 0 ? <option value="">还没有可用数据集</option> : null}
                  {datasets.map((dataset) => (
                    <option key={dataset.dataset_id} value={dataset.dataset_id}>
                      {dataset.display_name || "数据集"}（{datasetStatusLabel(dataset.status)}）
                    </option>
                  ))}
                </select>
                {datasetLabel ? (
                  <span className="fieldHint">
                    {datasetLabel.student_count} 名学生 · {datasetLabel.class_count} 个班级 · 课程目录{datasetLabel.catalog_version ? "已建立" : "尚未建立"}
                  </span>
                ) : null}
              </div>

              <button
                type="button"
                className={mode === "teacher" ? "identityOption identityOptionActive" : "identityOption"}
                onClick={() => setMode("teacher")}
                aria-pressed={mode === "teacher"}
              >
                <span className="row" style={{ gap: 12 }}>
                  <IconClasses size={20} />
                  <span className="stackSm">
                    <span className="identityOptionLabel">教师</span>
                    <span className="identityOptionMeta">班级总览、课程体系、数据管理与 AI 对话</span>
                  </span>
                </span>
                <IconArrowRight size={18} />
              </button>

              {mode === "teacher" ? (
                <div className="stackSm">
                  <button type="button" className="btn btnPrimary btnLg" disabled={busy} onClick={() => void enter("teacher")}>
                    {busy ? "正在建立会话…" : datasets.length ? "以教师身份进入" : "以教师身份进入（先导入数据）"}
                  </button>
                  {datasets.length === 0 ? (
                    <span className="fieldHint">还没有数据集：进入后可在数据管理上传 CSV / XLSX / PDF。</span>
                  ) : null}
                </div>
              ) : null}

              <button
                type="button"
                className={mode === "student" ? "identityOption identityOptionActive" : "identityOption"}
                onClick={() => setMode("student")}
                aria-pressed={mode === "student"}
              >
                <span className="row" style={{ gap: 12 }}>
                  <IconUser size={20} />
                  <span className="stackSm">
                    <span className="identityOptionLabel">学生</span>
                    <span className="identityOptionMeta">个人画像、学习计划与在线作答</span>
                  </span>
                </span>
                <IconArrowRight size={18} />
              </button>

              {mode === "student" ? (
                <div className="stackSm">
                  {classes.length === 0 ? (
                    <StatePanel kind="empty" title="这个数据集还没有学生名册" message="请先让教师导入学生与记录数据。" />
                  ) : (
                    <div className="rosterWrap" aria-label="学生名册">
                      {classes.map((group) => (
                        <div className="rosterClass" key={group.class_id}>
                          <span className="rosterClassTitle">班级 {group.class_id} · {group.student_ids.length} 人</span>
                          <div className="rosterItems">
                            {group.student_ids.map((id) => (
                              <button
                                key={id}
                                type="button"
                                className={studentId === id ? "rosterItem rosterItemActive" : "rosterItem"}
                                aria-pressed={studentId === id}
                                onClick={() => {
                                  setStudentId(id);
                                  setClassId(group.class_id);
                                }}
                              >
                                {id}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="row">
                    <button
                      type="button"
                      className="btn btnPrimary btnLg"
                      disabled={busy || !studentId}
                      onClick={() => void enter("student")}
                    >
                      {busy ? "正在建立会话…" : `以学生 ${studentId ?? ""} 身份进入`}
                    </button>
                    <span className="fieldHint">学生只能读取本人数据，班级按授权固定。</span>
                  </div>
                </div>
              ) : null}

              {classId ? <span className="srOnly">当前班级 {classId}</span> : null}
              {entryError ? (
                <p className="fieldError" role="alert">
                  {entryError.text}
                  {entryError.requestId ? `（请求编号 ${entryError.requestId}）` : ""}
                </p>
              ) : null}
              <p className="identityHint">
                切换身份会清空本地查询缓存并重建会话；旧会话令牌不会跨身份复用。
              </p>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
