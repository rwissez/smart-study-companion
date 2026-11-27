import type { ReactNode } from "react";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { dataSource } from "../api/service";
import { LAST_PATH_STORAGE_KEY, useSession } from "./session";
import { AppLayout } from "../layouts/AppLayout";
import { IdentityPage } from "../pages/IdentityPage";
import { ClassesPage } from "../pages/teacher/ClassesPage";
import { StudentProfilePage } from "../pages/teacher/StudentProfilePage";
import { CurriculumPage } from "../pages/teacher/CurriculumPage";
import { DataPage } from "../pages/teacher/DataPage";
import { TeacherChatPage } from "../pages/teacher/ChatPage";
import { InterventionPage } from "../pages/teacher/InterventionPage";
import { StudentProfilePage as StudentOwnProfilePage } from "../pages/student/ProfilePage";
import { StudentPlanPage } from "../pages/student/PlanPage";
import { ActivityPage } from "../pages/student/ActivityPage";
import { StudentChatPage } from "../pages/student/ChatPage";
import { StatePanel } from "../components/feedback/StatePanel";

function RequireRole({ role, children }: { role: "teacher" | "student"; children: ReactNode }) {
  const { session, signingIn } = useSession();
  if (!session && signingIn) {
    return (
      <div style={{ maxWidth: 480, margin: "120px auto", padding: 24 }}>
        <StatePanel kind="loading" title="正在重建会话" />
      </div>
    );
  }
  if (!session) return <Navigate to="/identity" replace />;
  if (session.scope.role !== role) {
    return <Navigate to={session.scope.role === "teacher" ? "/teacher/classes" : "/student/profile"} replace />;
  }
  return <>{children}</>;
}

function NotFound() {
  return (
    <div style={{ maxWidth: 640, margin: "80px auto", padding: 24 }}>
      <StatePanel
        kind="empty"
        title="页面不存在"
        message="地址不对，或者这个入口还没有开放。"
        action={<Link className="btn" to="/identity">回到身份选择</Link>}
      />
    </div>
  );
}

function ConfigError() {
  return (
    <div style={{ maxWidth: 640, margin: "80px auto", padding: 24 }}>
      <StatePanel
        kind="error"
        title="数据来源配置有误"
        message="VITE_DATA_SOURCE 只能是 api 或 demo。默认是 api；写错时不会回退到演示数据。"
      />
    </div>
  );
}

function HomeRedirect() {
  const { session } = useSession();
  if (!session) return <Navigate to="/identity" replace />;
  const home = session.scope.role === "teacher" ? "/teacher/classes" : "/student/profile";
  let last: string | null = null;
  try {
    last = window.sessionStorage.getItem(LAST_PATH_STORAGE_KEY);
  } catch {
    last = null;
  }
  const prefix = session.scope.role === "teacher" ? "/teacher" : "/student";
  return <Navigate to={last && last.startsWith(prefix) ? last : home} replace />;
}

export function AppRouter() {
  if (dataSource === "invalid") return <ConfigError />;

  return (
    <Routes>
      <Route path="/" element={<HomeRedirect />} />
      <Route path="/identity" element={<IdentityPage />} />

      <Route
        path="/teacher"
        element={
          <RequireRole role="teacher">
            <AppLayout role="teacher" />
          </RequireRole>
        }
      >
        <Route index element={<Navigate to="classes" replace />} />
        <Route path="classes" element={<ClassesPage />} />
        <Route path="students/:studentId" element={<StudentProfilePage />} />
        <Route path="interventions/:caseId" element={<InterventionPage />} />
        <Route path="curriculum" element={<CurriculumPage />} />
        <Route path="data" element={<DataPage />} />
        <Route path="chat" element={<TeacherChatPage />} />
      </Route>

      <Route
        path="/student"
        element={
          <RequireRole role="student">
            <AppLayout role="student" />
          </RequireRole>
        }
      >
        <Route index element={<Navigate to="profile" replace />} />
        <Route path="profile" element={<StudentOwnProfilePage />} />
        <Route path="plan" element={<StudentPlanPage />} />
        <Route path="plan/:planVersion/activities/:activityId" element={<ActivityPage />} />
        <Route path="chat" element={<StudentChatPage />} />
      </Route>

      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
