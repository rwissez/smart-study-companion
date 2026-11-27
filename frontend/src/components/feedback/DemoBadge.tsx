import { isDemo } from "../../api/service";

/** 仅显式 VITE_DATA_SOURCE=demo 时显示，且每页都要显示。 */
export function DemoBadge() {
  if (!isDemo) return null;
  return <span className="demoBadge">演示数据</span>;
}
