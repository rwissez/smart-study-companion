import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessageRecord, ConversationSummary } from "../../api/types";
import { SessionProvider } from "../../app/session";

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  getConversationMessages: vi.fn(),
  getCatalog: vi.fn(),
  streamChat: vi.fn(),
  sendChat: vi.fn(),
}));

vi.mock("../../api/service", () => ({
  dataSource: "api",
  isDemo: false,
  createSession: vi.fn(),
  listConversations: (...args: unknown[]) => mocks.listConversations(...args),
  getConversationMessages: (...args: unknown[]) => mocks.getConversationMessages(...args),
  getCatalog: (...args: unknown[]) => mocks.getCatalog(...args),
  streamChat: (...args: unknown[]) => mocks.streamChat(...args),
  sendChat: (...args: unknown[]) => mocks.sendChat(...args),
  getModelSettings: vi.fn(async () => ({
    configured: true,
    provider: "openai_compatible",
    url: "http://127.0.0.1:11434/v1",
    model_id: "test",
    has_api_key: false,
    allow_external_model_input: false,
  })),
  confirmProposal: vi.fn(),
  createProposal: vi.fn(),
}));

import { ChatPanel } from "./ChatPanel";

const { listConversations, getConversationMessages, getCatalog, streamChat } = mocks;
function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function Harness() {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/teacher/chat"]}>
        <SessionProvider>
          <ChatPanel role="teacher" context={{ studentId: "S01", nodeId: "N01" }} />
          <LocationProbe />
        </SessionProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const assistantMessage: ChatMessageRecord = {
  message_id: "msg-2",
  conversation_id: "conv-1",
  sequence: 2,
  role: "assistant",
  content: "## 结论\n\n- 依据 **R01**，先订正符号。",
  status: "completed",
  created_at: "2026-11-27T12:00:00Z",
  segments: [{ kind: "explanation", text: "## 结论\n\n- 依据 **R01**，先订正符号。", evidence_refs: [] }],
  job_id: null,
  tool_trace: [{ tool_name: "get_student_analysis", status: "succeeded" }],
  evidence_refs: [],
  operation_results: [],
};

const userMessage: ChatMessageRecord = {
  message_id: "msg-1",
  conversation_id: "conv-1",
  sequence: 1,
  role: "user",
  content: "S01 为什么丢分？",
  status: "completed",
  created_at: "2026-11-27T12:00:00Z",
  segments: [],
  job_id: null,
  tool_trace: [],
  evidence_refs: [],
  operation_results: [],
};

const summary: ConversationSummary = {
  conversation_id: "conv-1",
  title: "S01 为什么丢分？",
  role: "teacher",
  dataset_id: "synthetic-ds",
  created_at: "2026-11-27T12:00:00Z",
  updated_at: "2026-11-27T12:00:00Z",
  last_message: null,
};

describe("ChatPanel 流式回答与历史刷新", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCatalog.mockResolvedValue({ version: "v1", topics: [], indicators: [], materials: [], links: [] });
    listConversations.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
    getConversationMessages.mockResolvedValue([]);
  });

  it("逐段显示真实工具进度和回答，完成后刷新历史并保存 conversation_id", async () => {
    const user = userEvent.setup();
    streamChat.mockImplementation(async function* () {
      yield {
        event: "step" as const,
        data: { kind: "tool", status: "running", label: "正在调用：读取学生学情分析", tool_name: "get_student_analysis", call_id: "c1" },
      };
      yield { event: "delta" as const, data: { text: "## 结论\n\n- 依据 **R01**", mode: "chunked" } };
      // 给测试留出“流式进行中”的观察窗口，再继续推送第二段和完成事件。
      await new Promise((resolve) => setTimeout(resolve, 300));
      yield { event: "delta" as const, data: { text: "，先订正符号。", mode: "chunked" } };
      listConversations.mockResolvedValue({ items: [summary], total: 1, page: 1, page_size: 20 });
      getConversationMessages.mockResolvedValue([userMessage, assistantMessage]);
      yield {
        event: "done" as const,
        data: {
          text: "## 结论\n\n- 依据 **R01**，先订正符号。",
          conversation_id: "conv-1",
          message_id: "msg-2",
          status: "completed",
          segments: assistantMessage.segments,
        },
      };
    });

    render(<Harness />);
    const input = screen.getByLabelText("输入消息");
    await user.type(input, "S01 为什么丢分？");
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    // 流式过程中：真实工具标签先出现，首个分块已渲染，并标注非模型原生流。
    expect(await screen.findByText("正在调用：读取学生学情分析")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "结论" })).toBeInTheDocument();
    expect(screen.getByText("非模型原生流")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回答中…" })).toBeDisabled();

    // 完成后：历史列表重新拉取，会话可见且 conversation_id 写回 URL。
    expect(await screen.findByRole("button", { name: /S01 为什么丢分？/ })).toBeInTheDocument();
    await waitFor(() => expect(listConversations).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain("conversationId=conv-1"),
    );
    await waitFor(() => expect(screen.queryByRole("button", { name: "回答中…" })).toBeNull());
    expect(screen.getByText(/先订正符号/)).toBeInTheDocument();
  });

  it("发送失败时保留草稿，不丢失用户输入", async () => {
    const user = userEvent.setup();
    streamChat.mockImplementation(async function* () {
      yield { event: "error" as const, data: { code: "MODEL_TIMEOUT", message: "模型请求超时" } };
    });

    render(<Harness />);
    const input = screen.getByLabelText("输入消息");
    await user.type(input, "解释一下这一步");
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText(/模型请求超时/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("输入消息")).toHaveValue("解释一下这一步"));
  });
});
