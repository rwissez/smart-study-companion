import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { ChatMessageRecord, ConversationSummary } from "../../api/types";

// 会话恢复在模块求值时读取 sessionStorage，单测里直接固定 scopeKey，
// 让教师端选择条的数据查询（班级/名册/运行）可用。
vi.mock("../../app/session", () => ({
  useSession: () => ({
    scopeKey: { mode: "api", epoch: 1, datasetId: "ds-chat" },
    rememberOwnerKey: vi.fn(),
  }),
  SessionProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

import { SessionProvider } from "../../app/session";

const mocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  getConversationMessages: vi.fn(),
  getCatalog: vi.fn(),
  streamChat: vi.fn(),
  sendChat: vi.fn(),
  listClasses: vi.fn(),
  listStudents: vi.fn(),
  listRuns: vi.fn(),
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
  listClasses: (...args: unknown[]) => mocks.listClasses(...args),
  listStudents: (...args: unknown[]) => mocks.listStudents(...args),
  listRuns: (...args: unknown[]) => mocks.listRuns(...args),
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

const { listConversations, getConversationMessages, getCatalog, streamChat, listClasses, listStudents, listRuns } = mocks;
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
    listClasses.mockResolvedValue([]);
    listStudents.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200 });
    listRuns.mockResolvedValue([]);
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

const classList = [
  { class_id: "DC01", display_name: "DC01", roster_count: 2 },
  { class_id: "DC02", display_name: "DC02", roster_count: 1 },
];

const rosterPage = {
  items: [
    { student_id: "S01", class_id: "DC01", grade: "七年级", observed_event_count: 3, graded_event_count: 2 },
    { student_id: "S02", class_id: "DC01", grade: "七年级", observed_event_count: 1, graded_event_count: 1 },
  ],
  total: 2,
  page: 1,
  page_size: 50,
};

const runList = [
  {
    run_id: "run-1",
    status: "succeeded",
    node_ids: ["N01", "N02"],
    catalog_version: "v1",
    knowledge_version: "v1",
    created_at: "2026-11-28T09:00:00Z",
  },
];

function TeacherHarness({ studentId }: { studentId?: string }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/teacher/chat"]}>
        <SessionProvider>
          <ChatPanel role="teacher" context={{ studentId: studentId ?? null, nodeId: "N01" }} />
        </SessionProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("教师端分析对象选择条", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCatalog.mockResolvedValue({ version: "v1", topics: [], indicators: [], materials: [], links: [] });
    listConversations.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
    getConversationMessages.mockResolvedValue([]);
    listClasses.mockResolvedValue(classList);
    listStudents.mockResolvedValue(rosterPage);
    listRuns.mockResolvedValue(runList);
    streamChat.mockImplementation(async function* () {
      yield {
        event: "done" as const,
        data: { text: "好的。", conversation_id: "conv-1", message_id: "msg-2", status: "completed" },
      };
    });
  });

  it("默认选中第一个班级；快捷任务按班级携带 task_type 与 target，不混入学生绑定", async () => {
    const user = userEvent.setup();
    render(<TeacherHarness />);
    await waitFor(() => expect(screen.getByLabelText("分析对象班级")).toHaveValue("DC01"));
    await user.click(screen.getByRole("button", { name: "分析班级共性" }));
    await waitFor(() => expect(streamChat).toHaveBeenCalledTimes(1));
    const payload = streamChat.mock.calls[0][0];
    expect(payload.task_type).toBe("class_analysis");
    expect(payload.target).toEqual({ type: "class", id: "DC01" });
    expect(payload.student_id).toBeNull();
    expect(payload.message).toContain("DC01");
  });

  it("消息点名其他学生时优先按点名绑定学生", async () => {
    const user = userEvent.setup();
    render(<TeacherHarness studentId="S01" />);
    await screen.findByLabelText("分析对象班级");
    await user.type(screen.getByLabelText("输入消息"), "S02 这次为什么丢分？");
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(streamChat).toHaveBeenCalledTimes(1));
    const payload = streamChat.mock.calls[0][0];
    expect(payload.student_id).toBe("S02");
    expect(payload.target).toBeNull();
  });

  it("“解释失分原因”在未选择学生时给出引导且不发送", async () => {
    const user = userEvent.setup();
    render(<TeacherHarness studentId={undefined} />);
    await screen.findByLabelText("分析对象班级");
    await user.click(screen.getByRole("button", { name: "解释失分原因" }));
    expect(await screen.findByText(/需要指定学生/)).toBeInTheDocument();
    expect(streamChat).not.toHaveBeenCalled();
  });

  it("“解释失分原因”在已选学生时携带学生与任务类型", async () => {
    const user = userEvent.setup();
    render(<TeacherHarness studentId="S01" />);
    await screen.findByLabelText("分析对象班级");
    await user.click(screen.getByRole("button", { name: "解释失分原因" }));
    await waitFor(() => expect(streamChat).toHaveBeenCalledTimes(1));
    const payload = streamChat.mock.calls[0][0];
    expect(payload.task_type).toBe("student_loss_explanation");
    expect(payload.student_id).toBe("S01");
    expect(payload.target).toBeNull();
    expect(payload.message).toContain("S01");
  });

  it("target 校验错误转为自然语言并给出“选择分析对象”入口", async () => {
    streamChat.mockImplementation(async function* () {
      yield {
        event: "error" as const,
        data: {
          code: "VALIDATION_ERROR",
          message: "这个分析需要指定具体学生：请先在对话页上方选择学生后再提问。",
        },
      };
    });
    const user = userEvent.setup();
    render(<TeacherHarness studentId={undefined} />);
    await screen.findByLabelText("分析对象班级");
    await user.type(screen.getByLabelText("输入消息"), "解释一下");
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("button", { name: "选择分析对象" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/需要指定具体学生/);
  });
});
