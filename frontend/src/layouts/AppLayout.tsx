import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "../app/session";
import { listAlerts, listDatasets } from "../api/service";
import type { AlertItem } from "../api/types";
import { queryKeys } from "../lib/queryKeys";
import { datasetStatusLabel } from "../lib/labels";
import { DemoBadge } from "../components/feedback/DemoBadge";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import {
  IconChat,
  IconClasses,
  IconCurriculum,
  IconData,
  IconLogout,
  IconUser,
} from "../components/icons";

const TEACHER_NAV = [
  { to: "/teacher/classes", label: "班级总览", icon: IconClasses },
  { to: "/teacher/curriculum", label: "课程体系", icon: IconCurriculum },
  { to: "/teacher/data", label: "数据管理", icon: IconData },
  { to: "/teacher/chat", label: "AI 对话", icon: IconChat },
];

const STUDENT_NAV = [
  { to: "/student/profile", label: "个人画像", icon: IconUser },
  { to: "/student/plan", label: "学习计划", icon: IconClasses },
  { to: "/student/chat", label: "AI 对话", icon: IconChat },
];

/** 双端壳层：208px 白侧栏、64px 顶栏、内容左右 32px。 */
export function AppLayout({ role }: { role: "teacher" | "student" }) {
  const { session, signOut, switchDataset, scopeKey } = useSession();
  const navigate = useNavigate();
  const [pendingDataset, setPendingDataset] = useState<string | null>(null);
  const [datasetError, setDatasetError] = useState<string | null>(null);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const datasetId = session?.scope.dataset_id ?? null;

  const datasets = useQuery({
    queryKey: queryKeys.datasets(scopeKey),
    queryFn: listDatasets,
    enabled: role === "teacher",
  });

  const alerts = useQuery<AlertItem[]>({
    queryKey: queryKeys.alerts(scopeKey),
    queryFn: () => listAlerts(datasetId as string),
    enabled: role === "teacher" && Boolean(datasetId),
  });

  const nav = role === "teacher" ? TEACHER_NAV : STUDENT_NAV;
  const runDatasetSwitch = async () => {
    if (!pendingDataset) return;
    setSwitching(true);
    setDatasetError(null);
    try {
      await switchDataset(pendingDataset);
      setPendingDataset(null);
      navigate(role === "teacher" ? "/teacher/classes" : "/student/profile");
    } catch (error) {
      setDatasetError(error instanceof Error ? error.message : "切换数据集失败。");
    } finally {
      setSwitching(false);
    }
  };

  return (
    <div className="shell" data-role={role}>
      <nav className="sidebar" aria-label={role === "teacher" ? "教师导航" : "学生导航"}>
        <div className="sidebarBrand">
          <img className="sidebarLogo" src="/brand-logo.png" alt="湛知" width={112} height={36} />
        </div>

        <div className="sidebarNav">
          {nav.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => (isActive ? "navItem navItemActive" : "navItem")}
              >
                <span className="navIcon"><Icon size={18} /></span>
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </div>

        <div className="sidebarFoot">
          <DemoBadge />
          <span className="roleTag">
            {role === "teacher" ? "教师身份" : `学生 · ${session?.scope.student_id ?? "未绑定"}`}
          </span>
          <div className="sidebarFootActions">
            <button type="button" className="btn btnSm" onClick={() => { signOut(); navigate("/identity"); }}>
              <IconLogout size={16} />
              退出并重选身份
            </button>
          </div>
        </div>
      </nav>

      <div className="main">
        <header className="header">
          <div className="headerLeft">
            {role === "teacher" ? (
              <>
                <label className="field" style={{ minWidth: 220 }}>
                  <span className="srOnly">当前数据集</span>
                  <select
                    className="select"
                    value={datasetId ?? ""}
                    disabled={switching}
                    onChange={(event) => {
                      const next = event.target.value;
                      if (next && next !== datasetId) setPendingDataset(next);
                    }}
                  >
                    <option value="">{datasets.isPending ? "正在读取数据集…" : "选择数据集"}</option>
                    {(datasets.data ?? []).map((dataset) => (
                      <option key={dataset.dataset_id} value={dataset.dataset_id}>
                        {dataset.display_name || "数据集"}（{dataset.student_count} 人 · {datasetStatusLabel(dataset.status)}）
                      </option>
                    ))}
                  </select>
                </label>
                <DemoBadge />
                {datasets.isError ? <span className="xs fieldError">数据集读取失败</span> : null}
              </>
            ) : (
              <span className="headerMeta">
                学生端 · {session?.scope.student_id ?? "未绑定"} · 只读本人数据
              </span>
            )}
          </div>

          <div className="headerRight">
            {role === "teacher" ? (
              <div style={{ position: "relative" }}>
                <button
                  type="button"
                  className="btn btnSm"
                  aria-expanded={alertsOpen}
                  aria-haspopup="true"
                  onClick={() => setAlertsOpen((value) => !value)}
                  disabled={!datasetId}
                >
                  提醒{alerts.data?.length ? `（${alerts.data.length}）` : ""}
                </button>
                {alertsOpen ? (
                  <div
                    className="statePanel"
                    style={{ position: "absolute", right: 0, top: 40, width: 360, zIndex: 30, maxHeight: 360, overflow: "auto" }}
                    role="menu"
                  >
                    <span className="stateTitle">待处理提醒</span>
                    {alerts.isPending ? <span className="small muted">正在读取…</span> : null}
                    {alerts.isError ? <span className="small fieldError">提醒读取失败</span> : null}
                    {alerts.data?.length === 0 ? <span className="small muted">当前没有服务端返回的提醒。</span> : null}
                    {alerts.data?.map((alert) => (
                      <button
                        key={alert.alert_id}
                        type="button"
                        className="btn btnSm btnGhost"
                        style={{ justifyContent: "flex-start", whiteSpace: "normal", textAlign: "left" }}
                        onClick={() => {
                          setAlertsOpen(false);
                          if (alert.student_id) navigate(`/teacher/students/${encodeURIComponent(alert.student_id)}`);
                          else navigate("/teacher/curriculum?tab=reviews");
                        }}
                      >
                        {alert.summary}
                      </button>
                    ))}
                    <span className="xs muted">已读不等于已处理；结案要在对应业务页完成。</span>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </header>

        <main className="content">
          <Outlet />
        </main>
      </div>

      <ConfirmDialog
        open={pendingDataset !== null}
        title="切换数据集"
        confirmLabel="切换并重建会话"
        onConfirm={() => void runDatasetSwitch()}
        onCancel={() => setPendingDataset(null)}
        busy={switching}
      >
        <p>切换数据集会取消当前请求、清空缓存并重建会话；未提交的表单与草稿会丢失。</p>
        {datasetError ? <p className="fieldError" role="alert">{datasetError}</p> : null}
      </ConfirmDialog>
    </div>
  );
}
