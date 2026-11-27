import { ChatPanel } from "../../components/chat/ChatPanel";

export function StudentChatPage() {
  return (
    <div className="stackLg">
      <div className="pageHeader">
        <div>
          <h1 className="pageTitle">AI 对话</h1>
          <p className="pageSub">只使用本人数据；回答会标注来源，获取帮助会记入真实作答状态。</p>
        </div>
      </div>
      <ChatPanel role="student" />
    </div>
  );
}
