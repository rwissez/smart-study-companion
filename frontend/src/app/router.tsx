import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { useSession } from "./session";
import { dataSource } from "../api/service";
import { IdentityPage } from "../pages/IdentityPage";
import { TeacherLayout } from "../layouts/TeacherLayout";
import { StudentLayout } from "../layouts/StudentLayout";
import { ClassesPage } from "../pages/teacher/ClassesPage";
import { StudentProfilePage } from "../pages/teacher/StudentProfilePage";
import { CurriculumPage } from "../pages/teacher/CurriculumPage";
import { ChatPage } from "../pages/teacher/ChatPage";
import { DataPage } from "../pages/teacher/DataPage";
import { StudentHomePage } from "../pages/student/StudentHomePage";
import { StudentPlanPage } from "../pages/student/StudentPlanPage";
import { StudentChatPage } from "../pages/student/StudentChatPage";
import { StatePanel } from "../components/feedback/StatePanel";
import { Link } from "react-router-dom";

function RequireRole({ role, children }: { role: "teacher" | "student"; children: ReactNode }) {
  const { session } = useSession();
  if (!session) return <Navigate to="/" replace />;
  if (session.role !== role) {
    return (
      <Navigate to={session.role === "teacher" ? "/teacher/classes" : "/student/home"} replace />
    );
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
        action={
          <Link className="button" to="/">
            回到身份选择
          </Link>
        }
      />
    </div>
  );
}

export function AppRouter() {
  if (dataSource === "invalid") {
    return (
      <div style={{ maxWidth: 640, margin: "80px auto", padding: 24 }}>
        <StatePanel
          kind="error"
          title="数据来源配置有误"
          message="VITE_DATA_SOURCE 只能是 demo 或 api，请检查项目根目录下的 .env 配置。"
        />
      </div>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<IdentityPage />} />

        <Route
          path="/teacher"
          element={
            <RequireRole role="teacher">
              <TeacherLayout />
            </RequireRole>
          }
        >
          <Route index element={<Navigate to="classes" replace />} />
          <Route path="classes" element={<ClassesPage />} />
          <Route path="students/:studentId" element={<StudentProfilePage />} />
          <Route path="curriculum" element={<CurriculumPage />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="data" element={<DataPage />} />
        </Route>

        <Route
          path="/student"
          element={
            <RequireRole role="student">
              <StudentLayout />
            </RequireRole>
          }
        >
          <Route index element={<Navigate to="home" replace />} />
          <Route path="home" element={<StudentHomePage />} />
          <Route path="plan" element={<StudentPlanPage />} />
          <Route path="chat" element={<StudentChatPage />} />
        </Route>

        <Route path="*" element={<NotFound />} />
      </Routes>
    </BrowserRouter>
  );
}
