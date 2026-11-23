import { ScopeProvider } from "../app/scope";
import { AppLayout, type NavItem } from "./AppLayout";

const items: NavItem[] = [
  { to: "/teacher/classes", label: "班级总览" },
  { to: "/teacher/curriculum", label: "课程体系" },
  { to: "/teacher/chat", label: "教学对话" },
  { to: "/teacher/data", label: "数据管理" },
];

/** 教师端壳层：带上数据集、运行与节点范围 */
export function TeacherLayout() {
  return (
    <ScopeProvider>
      <AppLayout role="teacher" items={items} />
    </ScopeProvider>
  );
}
