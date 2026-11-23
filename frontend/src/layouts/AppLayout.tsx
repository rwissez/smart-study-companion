import { NavLink, Outlet } from "react-router-dom";
import { useSession } from "../app/session";
import { ScopeBar } from "../components/layout/ScopeBar";
import { DemoBadge } from "../components/feedback/DemoBadge";
import styles from "./AppLayout.module.css";

export interface NavItem {
  to: string;
  label: string;
}

interface Props {
  role: "teacher" | "student";
  items: NavItem[];
}

/** 双端共用壳层：左侧导航、页头身份与示例标记、内容区 */
export function AppLayout({ role, items }: Props) {
  const { session, signOut } = useSession();

  const activeClass = role === "teacher" ? styles.navLinkActive : styles.navLinkActiveStudent;

  return (
    <div className={`${styles.shell} ${role === "teacher" ? styles.teacherShell : ""}`}>
      <nav className={styles.nav} aria-label={role === "teacher" ? "教师导航" : "学生导航"}>
        <div className={styles.brand}>
          <span className={styles.brandName}><span className={styles.brandIcon} aria-hidden="true">◧</span>智慧学伴</span>
          <span className={styles.brandSub}>
            {role === "teacher" ? "教师工作台" : "学生学习空间"}
          </span>
        </div>

        <ul className={styles.navList}>
          {items.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                className={({ isActive }) =>
                  [styles.navLink, isActive ? activeClass : ""].filter(Boolean).join(" ")
                }
              >
                {role === "teacher" && <span className={styles.navIcon} aria-hidden="true">{({"班级总览":"▦", "课程体系":"◇", "数据管理":"▤", "教学对话":"◌"} as Record<string, string>)[item.label] ?? "◇"}</span>}<span>{item.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>

        <div className={styles.navFoot}>
          <DemoBadge />
          <button type="button" className={styles.navLink} onClick={signOut}>
            <span>退出并重选身份</span>
          </button>
        </div>
      </nav>

      <main className={styles.main}>
        <div className={styles.mainInner}>
          <div className={styles.topbar}>
            {role === "teacher" ? <><ScopeBar /><DemoBadge /><button type="button" className={styles.headerSignOut} onClick={signOut}>切换身份</button></> : <><div className={styles.titleBlock}><h1 style={{ fontSize: 24 }}>学生端{session?.scope.student_id ? ` · ${session.scope.student_id}` : ""}</h1><span className={styles.subtitle}>查看自己的学习情况与下一步任务</span></div><DemoBadge /><button type="button" className={styles.compactSignOut} onClick={signOut}>退出并重选身份</button></>}
          </div>

          <Outlet />
        </div>
      </main>
    </div>
  );
}
