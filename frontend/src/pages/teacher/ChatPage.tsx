import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { listRuns } from "../../api/service";
import { useSession } from "../../app/session";
import { queryKeys } from "../../lib/queryKeys";
import { ChatPanel } from "../../components/chat/ChatPanel";
import { ModelSettingsDialog } from "../../components/chat/ModelSettingsDialog";

export function TeacherChatPage() {
  const { scopeKey } = useSession();
  const [searchParams] = useSearchParams();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const studentId = searchParams.get("student");
  const nodeParam = searchParams.get("node");
  const conversationId = searchParams.get("conversationId");

  const runs = useQuery({
    queryKey: queryKeys.runs(scopeKey),
    queryFn: () => listRuns(scopeKey.datasetId as string),
    enabled: Boolean(scopeKey.datasetId),
  });
  const run = runs.data?.find((item) => item.status === "succeeded") ?? runs.data?.[0] ?? null;

  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">AI 对话</h1>
          <p className="pageSub">
            {studentId ? `当前对象：学生 ${studentId}` : "按教师身份与当前数据集对话"}
          </p>
        </div>
      </div>
      <ChatPanel
        role="teacher"
        context={{
          runId: run?.run_id ?? null,
          nodeId: nodeParam || run?.node_ids[0] || null,
          studentId,
        }}
        initialConversationId={conversationId}
        onConfigure={() => setSettingsOpen(true)}
      />
      <ModelSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
