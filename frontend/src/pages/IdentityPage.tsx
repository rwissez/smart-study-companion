import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createSession, listIdentities, isDemo } from "../api/service";
import { useSession } from "../app/session";
import { DemoBadge } from "../components/feedback/DemoBadge";
import { StatePanel } from "../components/feedback/StatePanel";
import { useQuery } from "@tanstack/react-query";
import styles from "../styles/ui.module.css";
import page from "../styles/pages.module.css";

/**
 * 身份选择：教师或模拟学生。会话只在内存，刷新后回到这里重选。
 * 这是本地演示用的模拟身份，不是生产级登录。
 */
export function IdentityPage() {
  const navigate = useNavigate();
  const { start } = useSession();
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const identities = useQuery({
    queryKey: ["identities"],
    queryFn: listIdentities,
    retry: false,
  });

  const enter = async (role: "teacher" | "student", studentId?: string) => {
    setPending(studentId ?? role);
    setError(null);
    try {
      const session = await createSession(role, studentId);
      start(session);
      navigate(role === "teacher" ? "/teacher/classes" : "/student/home", { replace: true });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setPending(null);
    }
  };

  return (
    <div className={page.identityWrap}>
      <header className={page.identityHead}>
        <div className={styles.row}>
          <h1>智慧学伴</h1>
          <DemoBadge />
        </div>
        <p className={styles.muted}>
          学情诊断与个性化学习演示界面。选择身份进入对应的教师工作台或学生学习空间。
        </p>
      </header>

      {identities.isLoading ? <StatePanel kind="loading" title="正在读取可选身份" /> : null}

      {identities.error ? (
        <StatePanel
          kind="error"
          message={(identities.error as Error).message}
          onRetry={() => identities.refetch()}
        />
      ) : null}

      {identities.data ? (
        <div className={page.identityGrid}>
          <section className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
            <span className={styles.label}>教师</span>
            <h2>进入教师工作台</h2>
            <p className={styles.muted}>
              按班级查看名册与学情，进入学生画像查看诊断依据，阅读并调整学习建议。
            </p>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary} ${styles.buttonBlock}`}
              onClick={() => enter("teacher")}
              disabled={pending !== null}
            >
              {pending === "teacher" ? "正在建立会话…" : "以教师身份进入"}
            </button>
            <p className={`${styles.small} ${styles.muted}`}>
              数据集：{identities.data.dataset.dataset_id} · 学生{" "}
              {identities.data.dataset.student_count} 人
            </p>
          </section>

          <section className={`${styles.card} ${styles.cardPad} ${styles.stack}`}>
            <span className={styles.label}>学生</span>
            <h2>以模拟学生身份进入</h2>
            <p className={styles.muted}>
              学生只能看到本人的学情、建议与提示，看不到其他学生的明细和教师审核信息。
            </p>
            <ul className={styles.stack} style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {identities.data.students.map((student) => (
                <li key={student.student_id}>
                  <button
                    type="button"
                    className={`${styles.button} ${styles.buttonBlock}`}
                    onClick={() => enter("student", student.student_id)}
                    disabled={pending !== null}
                  >
                    {pending === student.student_id
                      ? "正在建立会话…"
                      : `${student.student_id}（${student.class_id}）`}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      ) : null}

      {error ? <StatePanel kind="error" title="没有进入" message={error} /> : null}

      <footer className={`${styles.small} ${styles.muted}`}>
        数据来源：{isDemo ? "固定示例数据（不是真实学生）" : "本地服务接口"}　·　
        模型服务：尚未配置　·　会话刷新后需重新选择身份
      </footer>
    </div>
  );
}
