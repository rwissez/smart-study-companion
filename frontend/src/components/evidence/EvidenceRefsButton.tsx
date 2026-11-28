import { useState } from "react";
import type { EvidenceRef } from "../../api/types";
import { Modal } from "../ui/Modal";
import { EvidenceRefList } from "./EvidenceRefList";
import type { EvidenceContext } from "./types";

interface Props {
  refs: EvidenceRef[];
  onOpen: (ref: EvidenceRef) => void;
  context?: EvidenceContext;
  materialNames?: Record<string, string>;
}

/**
 * 统一的“查看依据”入口。
 * 单条引用保持直接打开详情；多条先列出全部引用，再按需进入现有证据抽屉。
 */
export function EvidenceRefsButton({ refs, onOpen, context, materialNames }: Props) {
  const [listOpen, setListOpen] = useState(false);
  if (refs.length === 0) return null;

  if (refs.length === 1) {
    return (
      <button type="button" className="btn btnSm btnGhost" onClick={() => onOpen(refs[0])}>
        查看依据
      </button>
    );
  }

  return (
    <>
      <button type="button" className="btn btnSm btnGhost" onClick={() => setListOpen(true)}>
        查看依据（{refs.length}）
      </button>
      <Modal
        open={listOpen}
        wide
        title="依据列表"
        description={`共 ${refs.length} 条依据；点选一条查看完整详情，引用版本按提交时保留。`}
        onClose={() => setListOpen(false)}
        footer={<button type="button" className="btn btnSm" onClick={() => setListOpen(false)}>关闭</button>}
      >
        <EvidenceRefList
          refs={refs}
          context={context}
          materialNames={materialNames}
          onOpen={(ref) => {
            setListOpen(false);
            onOpen(ref);
          }}
        />
      </Modal>
    </>
  );
}
