import type { RosterStudent } from "../../api/types";
import styles from "../../styles/ui.module.css";

interface Props {
  students: RosterStudent[];
  classNames?: Record<string, string>;
  onSelect: (studentId: string) => void;
  filter: string;
  onFilterChange: (value: string) => void;
}

/**
 * 班级名册：无记录的学生照样出现在名册里，观测记录数显示 0 而不是隐藏该行。
 */
export function StudentRoster({ students, classNames, onSelect, filter, onFilterChange }: Props) {
  const keyword = filter.trim().toUpperCase();
  const visible = keyword
    ? students.filter((s) => s.student_id.toUpperCase().includes(keyword))
    : students;

  return (
    <div className={`${styles.card}`}>
      <div className={styles.cardHead}>
        <span className={`${styles.small} ${styles.muted}`}>
          当前班级名册
        </span>
        <input
          className={styles.input}
          placeholder="按学生编号过滤"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          aria-label="按学生编号过滤名册"
        />
      </div>

      {visible.length === 0 ? (
        <div className={styles.cardBody}>
          <p className={`${styles.small} ${styles.muted}`}>当前筛选下没有学生。</p>
        </div>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th>学生编号</th>
              <th>班级</th>
              <th>年级</th>
              <th>观测记录</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {visible.map((student) => (
              <tr
                key={student.student_id}
                className={styles.tableRowClick}
                onClick={() => onSelect(student.student_id)}
              >
                <td className={styles.num}>{student.student_id}</td>
                <td>{classNames?.[student.class_id] ?? student.class_id}</td>
                <td className={styles.num}>{student.grade}</td>
                <td className={styles.num}>
                  {student.observed_event_count === 0 ? "无记录" : `${student.observed_event_count} 条`}
                </td>
                <td>
                  <button
                    type="button"
                    className={`${styles.button} ${styles.buttonGhost}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(student.student_id);
                    }}
                  >
                    查看画像
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className={styles.cardBody}>
        <p className={`${styles.small} ${styles.muted}`}>
          共 {students.length} 人，当前显示 {visible.length} 人。过滤只作用于当前已加载的名册。
        </p>
      </div>
    </div>
  );
}
