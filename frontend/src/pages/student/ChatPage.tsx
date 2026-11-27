import { useSearchParams } from "react-router-dom";
import { ChatPanel } from "../../components/chat/ChatPanel";

export function StudentChatPage() {
  const [searchParams] = useSearchParams();
  return (
    <div className="chatPage">
      <ChatPanel role="student" initialConversationId={searchParams.get("conversationId")} />
    </div>
  );
}
