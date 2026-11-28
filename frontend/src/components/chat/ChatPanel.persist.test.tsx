import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatMessageRecord, ConversationSummary } from "../../api/types";
import type { ChatStreamEvent } from "../../api/service";
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
import { chatStreamKey, chatStreamStore, type ChatScopeIdentity } from "./chatStreamStore";

const { listConversations, getConversationMessages, getCatalog, streamChat } = mocks;

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

/** 用按钮在“对话页”和“别的页面”之间切换，模拟路由卸载与重新挂载。 */
function PanelHost({ studentId }: { studentId: string }) {
  const [onChat, setOnChat] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOnChat((value) => !value)}>
        {onChat ? "切换到班级页" : "切回对话页"}
      </button>
      {onChat ? (
        <ChatPanel role="teacher" context={{ studentId, nodeId: "N01" }} />
      ) : (
        <p>班级页占位</p>
      )}
    </>
  );
}

function renderPanel(studentId: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/teacher/chat"]}>
        <SessionProvider>
          <PanelHost studentId={studentId} />
          <LocationProbe />
        </SessionProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

interface ControlledStream {
  push(event: ChatStreamEvent): void;
  close(): void;
  generator(): AsyncGenerator<ChatStreamEvent>;
}

/** 可控异步生成器：测试决定什么时候推事件，从而观察“流还没结束时组件已卸载”的窗口。 */
function createControlledStream(): ControlledStream {
  const queue: ChatStreamEvent[] = [];
  let wake: (() => void) | null = null;
  let closed = false;
  const wakeUp = (): void => {
    const pending = wake;
    wake = null;
    pending?.();
  };
  return {
    push(event) {
      queue.push(event);
      wakeUp();
    },
    close() {
      closed = true;
      wakeUp();
    },
    async *generator() {
      while (true) {
        if (queue.length) {
          yield queue.shift() as ChatStreamEvent;
          continue;
        }
        if (closed) return;
        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }
    },
  };
}

function scopeKeyOf(studentId: string): string {
  const identity: ChatScopeIdentity = {
    mode: "api",
    epoch: 1,
    datasetId: null,
    role: "teacher",
    studentId,
    nodeId: "N01",
  };
  return chatStreamKey(identity);
}

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

function assistantMessage(content: string): ChatMessageRecord {
  return {
    message_id: "msg-2",
    conversation_id: "conv-1",
    sequence: 2,
    role: "assistant",
    content,
    status: "completed",
    created_at: "2026-11-27T12:00:00Z",
    segments: [],
    job_id: null,
    tool_trace: [{ tool_name: "get_student_analysis", status: "succeeded" }],
    evidence_refs: [],
    operation_results: [],
  };
}

const summary: ConversationSummary = {
  conversation_id: "conv-1",
  title: "S01 为什么丢分？",
  role: "teacher",
  dataset_id: "synthetic-ds",
  created_at: "2026-11-27T12:00:00Z",
  updated_at: "2026-11-27T12:00:00Z",
  last_message: null,
};

async function sendMessage(user: ReturnType<typeof userEvent.setup>, text: string): Promise<void> {
  await user.type(screen.getByLabelText("输入消息"), text);
  await user.click(screen.getByRole("button", { name: "发送" }));
}

/** store 的通知会直接触发组件重渲染，放到 act 里推送避免测试告警。 */
async function pushEvents(stream: ControlledStream, ...events: ChatStreamEvent[]): Promise<void> {
  await act(async () => {
    events.forEach((event) => stream.push(event));
    // 让 run() 的 for-await 把本轮事件和随之而来的状态更新都处理完。
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("ChatPanel 跨路由的流式状态保留", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    chatStreamStore.clearAll();
    getCatalog.mockResolvedValue({ version: "v1", topics: [], indicators: [], materials: [], links: [] });
    listConversations.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
    getConversationMessages.mockResolvedValue([]);
  });

  afterEach(() => {
    // cleanup 之前还有组件订阅着 store，清空动作也要在 act 内完成。
    act(() => {
      chatStreamStore.clearAll();
    });
  });

  it("卸载后流继续写 store，重新挂载显示同一段 liveText 并继续接收", async () => {
    const user = userEvent.setup();
    const stream = createControlledStream();
    streamChat.mockImplementation(() => stream.generator());

    renderPanel("S01");
    await sendMessage(user, "S01 为什么丢分？");

    await pushEvents(
      stream,
      {
        event: "step",
        data: { kind: "tool", status: "running", label: "正在调用：读取学生学情分析", tool_name: "get_student_analysis", call_id: "c1" },
      },
      { event: "delta", data: { text: "第一段", mode: "chunked" } },
    );
    expect(await screen.findByText("第一段")).toBeInTheDocument();
    expect(await screen.findByText("正在调用：读取学生学情分析")).toBeInTheDocument();

    // 离开对话页：ChatPanel 卸载，但流没有中止。
    await user.click(screen.getByRole("button", { name: "切换到班级页" }));
    expect(screen.queryByLabelText("输入消息")).toBeNull();

    await pushEvents(stream, { event: "delta", data: { text: "，第二段", mode: "chunked" } });
    const key = scopeKeyOf("S01");
    await waitFor(() => expect(chatStreamStore.getSnapshot(key).liveText).toBe("第一段，第二段"));

    // 回到对话页：同一身份键上的流重新渲染，仍在流式中。
    await user.click(screen.getByRole("button", { name: "切回对话页" }));
    expect(await screen.findByText("第一段，第二段")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "回答中…" })).toBeDisabled();

    // 完成：历史里出现 msg-2 之后才撤掉本地预览，正文只出现一次。
    listConversations.mockResolvedValue({ items: [summary], total: 1, page: 1, page_size: 20 });
    getConversationMessages.mockResolvedValue([userMessage, assistantMessage("第一段，第二段")]);
    await pushEvents(stream, {
      event: "done",
      data: {
        text: "第一段，第二段",
        conversation_id: "conv-1",
        message_id: "msg-2",
        status: "completed",
        segments: [],
      },
    });
    await act(async () => {
      stream.close();
    });

    await waitFor(() => expect(chatStreamStore.getSnapshot(key).status).toBe("idle"));
    await waitFor(() => expect(screen.getAllByText("第一段，第二段")).toHaveLength(1));
    // busy 复位：不再显示“回答中…”，输入框重新可用（草稿为空时发送按钮本身仍然禁用）。
    expect(screen.queryByRole("button", { name: "回答中…" })).toBeNull();
    expect(screen.getByLabelText("输入消息")).toBeEnabled();
    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toContain("conversationId=conv-1"),
    );
  });

  it("完成后等历史里真的出现 finishedMessageId 再清空，避免闪空", async () => {
    const user = userEvent.setup();
    const stream = createControlledStream();
    streamChat.mockImplementation(() => stream.generator());
    let resolveHistory: ((value: ChatMessageRecord[]) => void) | null = null;
    getConversationMessages.mockReturnValue(
      new Promise<ChatMessageRecord[]>((resolve) => {
        resolveHistory = resolve;
      }),
    );

    renderPanel("S01");
    await sendMessage(user, "S01 为什么丢分？");
    await pushEvents(stream, { event: "delta", data: { text: "回答正文", mode: "native" } });
    expect(await screen.findByText("回答正文")).toBeInTheDocument();

    await pushEvents(stream, {
      event: "done",
      data: { text: "回答正文", conversation_id: "conv-1", message_id: "msg-2", status: "completed" },
    });
    await act(async () => {
      stream.close();
    });

    const key = scopeKeyOf("S01");
    await waitFor(() => expect(chatStreamStore.getSnapshot(key).status).toBe("done"));
    // 历史还没回来：预览必须保留，不能先闪空。
    expect(screen.getByText("回答正文")).toBeInTheDocument();
    await waitFor(() => expect(resolveHistory).not.toBeNull());

    await act(async () => {
      resolveHistory?.([userMessage, assistantMessage("回答正文")]);
    });

    await waitFor(() => expect(chatStreamStore.getSnapshot(key).status).toBe("idle"));
    expect(screen.getAllByText("回答正文")).toHaveLength(1);
  });

  it("不同学生的流互不显示，切回原学生仍能看到自己的回答", async () => {
    const user = userEvent.setup();
    const stream = createControlledStream();
    streamChat.mockImplementation(() => stream.generator());

    const first = renderPanel("S01");
    await sendMessage(user, "S01 为什么丢分？");
    await pushEvents(stream, { event: "delta", data: { text: "S01 的回答", mode: "native" } });
    expect(await screen.findByText("S01 的回答")).toBeInTheDocument();
    first.unmount();

    // S02 页面不能看到 S01 的流，S02 自己的身份键保持空闲。
    const second = renderPanel("S02");
    await waitFor(() => expect(getCatalog).toHaveBeenCalled());
    expect(screen.queryByText("S01 的回答")).toBeNull();
    expect(chatStreamStore.getSnapshot(scopeKeyOf("S02")).status).toBe("idle");
    expect(chatStreamStore.getSnapshot(scopeKeyOf("S01")).status).toBe("streaming");
    second.unmount();

    // 切回 S01：原流还在，继续显示。
    renderPanel("S01");
    expect(await screen.findByText("S01 的回答")).toBeInTheDocument();
    await act(async () => {
      chatStreamStore.clearAll();
      stream.close();
    });
  });

  it("错误路径复位 busy，并把原文放回输入框", async () => {
    const user = userEvent.setup();
    const stream = createControlledStream();
    streamChat.mockImplementation(() => stream.generator());

    renderPanel("S01");
    await sendMessage(user, "解释一下这一步");
    await pushEvents(
      stream,
      { event: "delta", data: { text: "半截回答", mode: "native" } },
      { event: "error", data: { code: "MODEL_TIMEOUT", message: "模型请求超时" } },
    );

    expect(chatStreamStore.getSnapshot(scopeKeyOf("S01")).status).toBe("error");
    expect(screen.getByRole("alert")).toHaveTextContent("模型请求超时");
    await waitFor(() => expect(screen.getByLabelText("输入消息")).toHaveValue("解释一下这一步"));
    expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
    expect(screen.getByLabelText("输入消息")).toBeEnabled();

    const state = chatStreamStore.getSnapshot(scopeKeyOf("S01"));
    expect(state.status).toBe("error");
    expect(state.pendingUser).toBeNull();
    // 失败时已经推送的半截文本也要撤掉，避免把未完成内容当成回答。
    expect(screen.queryByText("半截回答")).toBeNull();
  });
});
