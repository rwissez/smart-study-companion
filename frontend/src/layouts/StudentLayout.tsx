import { ScopeProvider } from "../app/scope";
import { AppLayout, type NavItem } from "./AppLayout";

const items: NavItem[] = [
  { to: "/student/home", label: "我的首页" },
  { to: "/student/plan", label: "学习计划" },
  { to: "/student/chat", label: "学习对话" },
];

/** 学生端壳层：范围由会话绑定，页面内只做节点切换 */
export function StudentLayout() {
  return (
    <ScopeProvider>
      <AppLayout role="student" items={items} />
    </ScopeProvider>
  );
}
