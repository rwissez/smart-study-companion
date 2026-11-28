import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ streamChat: vi.fn(), sendChat: vi.fn() }));

vi.mock("../../api/service", () => ({
  streamChat: (...args: unknown[]) => mocks.streamChat(...args),
  sendChat: (...args: unknown[]) => mocks.sendChat(...args),
}));

import { chatStreamKey, chatStreamStore, type ChatScopeIdentity } from "./chatStreamStore";

const identity: ChatScopeIdentity = {
  mode: "api",
  epoch: 1,
  datasetId: "ds",
  role: "teacher",
  studentId: "S01",
  nodeId: "N01",
};
const key = chatStreamKey(identity);
const otherIdentity: ChatScopeIdentity = { ...identity, studentId: "S02" };

/** 让流挂着不结束，方便手动驱动 append/finish/fail，观察状态机本身。 */
function hangStream(): void {
  mocks.streamChat.mockImplementation(() => (async function* () {
    await new Promise<void>(() => undefined);
  })());
}

describe("chatStreamStore 状态机", () => {
  beforeEach(() => {
    chatStreamStore.clearAll();
    hangStream();
  });

  afterEach(() => {
    chatStreamStore.clearAll();
  });

  it("append 与 updateSteps 只写当前身份键，其他身份保持空闲", () => {
    chatStreamStore.start({ identity, text: "问题", conversationId: null });
    chatStreamStore.append(key, "回答", "native");
    chatStreamStore.updateSteps(key, {
      kind: "tool",
      status: "running",
      label: "读取",
      tool_name: "get_student_analysis",
      call_id: "c1",
    });
    chatStreamStore.updateSteps(key, {
      kind: "tool",
      status: "succeeded",
      label: "读取完成",
      tool_name: "get_student_analysis",
      call_id: "c1",
    });

    const state = chatStreamStore.getSnapshot(key);
    expect(state.status).toBe("streaming");
    expect(state.pendingUser).toBe("问题");
    expect(state.liveText).toBe("回答");
    expect(state.liveSteps).toHaveLength(1);
    expect(state.liveSteps[0].status).toBe("succeeded");

    const other = chatStreamStore.getSnapshot(chatStreamKey(otherIdentity));
    expect(other.status).toBe("idle");
    expect(other.liveText).toBe("");
  });

  it("finish 记录会话与消息编号，状态离开 streaming，busy 随之复位", () => {
    chatStreamStore.start({ identity, text: "问题", conversationId: null });
    chatStreamStore.append(key, "逐段回答", "chunked");
    chatStreamStore.finish(
      key,
      { text: "逐段回答", conversation_id: "conv-1", message_id: "msg-2" },
      "",
    );

    const state = chatStreamStore.getSnapshot(key);
    expect(state.status).toBe("done");
    expect(state.conversationId).toBe("conv-1");
    expect(state.finishedMessageId).toBe("msg-2");
    expect(state.liveText).toBe("逐段回答");
    expect(state.liveMode).toBe("chunked");
    // 只有 streaming 算忙；完成态不应继续锁住输入框。
    expect(state.status === "streaming").toBe(false);
  });

  it("done 没带正文时用兜底文本，避免预览空白", () => {
    chatStreamStore.start({ identity, text: "问题", conversationId: "conv-0" });
    chatStreamStore.finish(key, { text: "", conversation_id: "conv-1" }, "兜底正文");

    const state = chatStreamStore.getSnapshot(key);
    expect(state.liveText).toBe("兜底正文");
    expect(state.finishedMessageId).toBeNull();
  });

  it("fail 清掉 pendingUser 和半截文本，留下错误与原文", () => {
    chatStreamStore.start({ identity, text: "问题", conversationId: null });
    chatStreamStore.append(key, "半截", "native");
    chatStreamStore.fail(
      key,
      { text: "模型请求超时", requestId: "req-1", details: [] },
      "问题",
    );

    const state = chatStreamStore.getSnapshot(key);
    expect(state.status).toBe("error");
    expect(state.pendingUser).toBeNull();
    expect(state.liveText).toBe("");
    expect(state.liveSteps).toHaveLength(0);
    expect(state.error?.text).toBe("模型请求超时");
    expect(state.draftText).toBe("问题");
  });

  it("clear 之后旧的 append/finish 不会把状态写回来", () => {
    chatStreamStore.start({ identity, text: "问题", conversationId: null });
    chatStreamStore.append(key, "回答", "native");
    chatStreamStore.clear(key);

    chatStreamStore.append(key, "旧流后到的文本", "native");
    chatStreamStore.updateSteps(key, { kind: "status", status: "running", label: "旧进度" });
    chatStreamStore.finish(key, { text: "旧回答", conversation_id: "conv-1", message_id: "msg-2" }, "");

    const state = chatStreamStore.getSnapshot(key);
    expect(state.status).toBe("idle");
    expect(state.liveText).toBe("");
  });

  it("节点未确定时发出的流，节点补上后仍按同一身份取到；其他学生不兜底", () => {
    const withoutNode: ChatScopeIdentity = { ...identity, nodeId: null };
    chatStreamStore.start({ identity: withoutNode, text: "问题", conversationId: null });

    expect(chatStreamStore.resolveKey(identity)).toBe(chatStreamKey(withoutNode));
    expect(chatStreamStore.resolveKey(otherIdentity)).toBe(chatStreamKey(otherIdentity));
  });

  it("流正常结束落到 done；error 事件落到 error", async () => {
    mocks.streamChat.mockImplementation(async function* () {
      yield { event: "delta" as const, data: { text: "回答", mode: "native" } };
      yield { event: "done" as const, data: { text: "回答", conversation_id: "conv-1", message_id: "msg-9" } };
    });
    chatStreamStore.start({ identity, text: "问题", conversationId: null });
    await vi.waitFor(() => expect(chatStreamStore.getSnapshot(key).status).toBe("done"));
    expect(chatStreamStore.getSnapshot(key).liveText).toBe("回答");

    chatStreamStore.clear(key);
    mocks.streamChat.mockImplementation(async function* () {
      yield { event: "error" as const, data: { code: "MODEL_TIMEOUT", message: "模型请求超时" } };
    });
    chatStreamStore.start({ identity, text: "再问一次", conversationId: "conv-1" });
    await vi.waitFor(() => expect(chatStreamStore.getSnapshot(key).status).toBe("error"));
    expect(chatStreamStore.getSnapshot(key).error?.text).toBe("模型请求超时");
    expect(chatStreamStore.getSnapshot(key).draftText).toBe("再问一次");
  });
});
