import { Link } from "react-router-dom";
import type { AttentionStudent, RosterStudent } from "../../api/types";

interface Props {
  students: RosterStudent[];
  total: number;
  page: number;
  pageSize: number;
  attention: AttentionStudent[];
  onPage: (page: number) => void;
  onOpenStudent: (studentId: string) => void;
  search: string;
  onSearch: (value: string) => void;
}

function evidenceText(student: RosterStudent): string {
  if (student.observed_event_count === 0) return "无记录";
  if (student.graded_event_count === 0) return "有观测，未评分";
  return "有已评分记录";
}

/**
 * 名册：分母与总数来自服务端名册（roster_count 含无记录学生），
 * 没有记录显示“无记录”，不显示 0 分；关注标记只用服务端 attention_students。
 */
export function StudentRoster({
  students,
  total,
  page,
  pageSize,
  attention,
  onPage,
  onOpenStudent,
  search,
  onSearch,
}: Props) {
  const attentionIds = new Set(attention.map((item) => item.student_id));
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="stack">
      <div className="rowBetween">
        <p className="small muted">
          名册共 {total} 人（分母含无记录学生）· 第 {page} / {pages} 页
        </p>
        <label className="field" style={{ maxWidth: 240 }}>
          <span className="fieldLabel">按学生编号筛选本班</span>
          <input
            className="input"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
            placeholder="例如 S01"
            type="search"
          />
        </label>
      </div>
      <div className="tableWrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">学生编号</th>
              <th scope="col">班级</th>
              <th scope="col">正式表现摘要</th>
              <th scope="col" className="numeric">观测 / 已评分</th>
              <th scope="col">证据状态</th>
              <th scope="col">关注</th>
              <th scope="col">进入画像</th>
            </tr>
          </thead>
          <tbody>
            {students.length === 0 ? (
              <tr>
                <td colSpan={7} className="cellMuted">当前页没有符合条件的学生。</td>
              </tr>
            ) : (
              students.map((student) => (
                <tr key={student.student_id}>
                  <td>{student.student_id}</td>
                  <td className="small">{student.class_id}</td>
                  <td className="small">{evidenceText(student)}</td>
                  <td className="numeric">{student.observed_event_count} / {student.graded_event_count}</td>
                  <td className="small">{student.observed_event_count === 0 ? "—" : "正式分析已读取"}</td>
                  <td className="small">
                    {attentionIds.has(student.student_id)
                      ? attention.find((item) => item.student_id === student.student_id)?.reason ?? "需要关注"
                      : "—"}
                  </td>
                  <td>
                    <button type="button" className="btn btnSm btnGhost" onClick={() => onOpenStudent(student.student_id)}>
                      查看画像
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button type="button" className="btn btnSm" onClick={() => onPage(page - 1)} disabled={page <= 1}>
          上一页
        </button>
        <button type="button" className="btn btnSm" onClick={() => onPage(page + 1)} disabled={page >= pages}>
          下一页
        </button>
      </div>
    </div>
  );
}

export function AttentionList({ items }: { items: AttentionStudent[] }) {
  if (items.length === 0) {
    return <p className="muted small">本次没有服务端标记的关注学生。关注标记由服务端返回，前端不按得分自行设阈值。</p>;
  }
  return (
    <ul className="stackSm" style={{ listStyle: "none", padding: 0 }}>
      {items.map((item) => (
        <li key={item.student_id} className="rowBetween">
          <span>
            <strong>{item.student_id}</strong> · {item.reason}
          </span>
          <Link className="small" to={`/teacher/students/${encodeURIComponent(item.student_id)}`}>
            进入画像
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function distributionText(bucketCount: number, rosterCount: number): string {
  if (rosterCount <= 0) return "无数据（分母为 0）";
  return `${bucketCount} / ${rosterCount}`;
}
