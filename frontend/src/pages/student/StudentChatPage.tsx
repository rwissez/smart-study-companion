import { useScope } from "../../app/scope";
import { useSession } from "../../app/session";
import { ChatPanel } from "../../components/chat/ChatPanel";
import styles from "../../styles/ui.module.css";

/** 学生对话：对象固定为本人，不能切换到其他学生 */
export function StudentChatPage() {
  const { session } = useSession();
  const { runId, nodeId, nodes } = useScope();
  const nodeName = nodes.find((n) => n.node_id === nodeId)?.name ?? "未选择节点";

  return (
    <div className={styles.stackLg}>
      <ChatPanel
        key={`${session?.session_id}:${runId}:${nodeId}`}
        role="student"
        targetLabel={`${session?.scope.student_id ?? "本人"}（本人）`}
        nodeName={nodeName}
        analysisContext={runId && nodeId && session?.scope.student_id ? {
          run_id: runId, node_id: nodeId, subject_type: "student", subject_id: session.scope.student_id,
        } : undefined}
        backTo={{ label: "回到我的学习计划", to: "/student/plan" }}
      />
      <p className={`${styles.small} ${styles.muted}`}>
        这里不会显示其他同学的记录，也不会显示老师的参考答案与审核信息。
      </p>
    </div>
  );
}
