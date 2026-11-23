import styles from "../../styles/ui.module.css";

/**
 * 示例数据标识：只要页面用的是固定示例，就必须出现这个标记，
 * 避免演示时被误当成真实学生结果（见 docs/product/design_and_interaction.md）。
 */
export function DemoBadge() {
  return (
    <span
      className={`${styles.badge} ${styles.badgeInfo}`}
      title="当前使用固定示例数据，不代表真实学生"
    >
      示例数据
    </span>
  );
}
