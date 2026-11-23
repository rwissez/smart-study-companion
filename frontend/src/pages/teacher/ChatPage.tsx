import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listClasses, listRoster } from "../../api/service";
import { queryKeys } from "../../api/queryKeys";
import { useScope } from "../../app/scope";
import { useSession } from "../../app/session";
import { ChatPanel } from "../../components/chat/ChatPanel";
import { StatePanel } from "../../components/feedback/StatePanel";
import styles from "../../styles/ui.module.css";

/** 教师对话：先明确对象与节点，再进入对话视图 */
export function ChatPage() {
  const { session } = useSession();
  const { datasetId, nodeId, nodes } = useScope();
  const [search, setSearch] = useSearchParams();
  const classes = useQuery({
    queryKey: queryKeys.classes(session, datasetId),
    queryFn: () => listClasses(datasetId as string),
    enabled: Boolean(session && datasetId),
  });

  const roster = useQuery({
    queryKey: queryKeys.roster(session, datasetId, null, 1),
    queryFn: () => listRoster(datasetId as string, null, 1),
    enabled: Boolean(session && datasetId),
    retry: false,
  });

  const nodeName = nodes.find((n) => n.node_id === nodeId)?.name ?? nodeId ?? "未选择";
  const options = [
    ...(classes.data ?? []).map((item) => ({
      value: `class:${item.class_id}`, label: `${item.class_id}（班级）`,
    })),
    ...(roster.data?.items ?? []).map((s) => ({
      value: `student:${s.student_id}`,
      label: `${s.student_id}（学生）`,
    })),
  ];
  const requestedStudent = search.get("studentId");
  const requested = requestedStudent ? `student:${requestedStudent}` : search.get("target");
  const selected = options.find((option) => option.value === requested)
    ?? (requested ? undefined : options[0]);
  const target = selected?.value ?? "";
  const targetLabel = selected?.label ?? "请选择范围内的对象";
  const rosterStudent = roster.data?.items.find((item) => target === `student:${item.student_id}`);
  const returnTo = rosterStudent
    ? `/teacher/students/${encodeURIComponent(rosterStudent.student_id)}?${new URLSearchParams({ classId: rosterStudent.class_id })}`
    : `/teacher/classes?${new URLSearchParams(target.startsWith("class:") ? { classId: target.slice(6) } : {})}`;

  return (
    <div className={styles.stackLg}>

      <div className={`${styles.card} ${styles.cardPad} ${styles.rowBetween}`}>
        <label className={styles.field}>
          <span className={styles.fieldLabel}>对话对象</span>
          <select
            className={styles.select}
            value={target}
            onChange={(e) => setSearch({ target: e.target.value })}
          >
            <option value="" disabled>请选择对象</option>
            {options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <p className={`${styles.small} ${styles.muted}`} style={{ maxWidth: 420 }}>
          对话的范围会固定在当前数据集、运行与节点上。切换对象或节点后旧对话清空。
        </p>
      </div>

      {roster.error ? (
        <StatePanel kind="error" message={(roster.error as Error).message} />
      ) : null}

      <ChatPanel
        role="teacher"
        targetLabel={targetLabel}
        nodeName={nodeName}
        backTo={{ label: "查看该对象的学情", to: returnTo }}
      />
    </div>
  );
}
