import { ApiError, errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import {
  sendChat,
  streamChat,
  type ChatProgressStep,
  type ChatTargetPayload,
} from "../../api/service";
import type { ChatReply } from "../../api/types";

/**
 * 对话流不放在组件 state 里：路由切换会卸载 ChatPanel，但流要继续跑。
 * 这个模块按身份键保存“当前活动流”，组件只订阅并渲染；卸载不清空、不中止。
 */
export interface ChatScopeIdentity {
  mode: string;
  epoch: number;
  datasetId: string | null;
  role: "teacher" | "student";
  studentId: string | null;
  nodeId: string | null;
}

export type ChatStreamStatus = "idle" | "streaming" | "done" | "error";

export interface ChatStreamError {
  text: string;
  requestId: string | null;
  details: Array<{ field?: string; reason?: string }>;
}

export interface ChatRequestBinding {
  nodeId: string | null;
  studentId: string | null;
  target: ChatTargetPayload | null;
  taskType: string | null;
}

export interface ChatStreamState {
  key: string;
  identity: ChatScopeIdentity | null;
  status: ChatStreamStatus;
  pendingUser: string | null;
  liveText: string;
  liveSteps: ChatProgressStep[];
  liveMode: "native" | "chunked" | null;
  conversationId: string | null;
  startedAt: number;
  finishedMessageId: string | null;
  error: ChatStreamError | null;
  /** 失败时要放回输入框的原文；成功或空闲时为 null。 */
  draftText: string | null;
}

/**
 * 身份键由角色、数据集、学生、节点，加上数据来源与会话代次组成。
 * owner key 是服务端按角色 + 数据集 + 本人派生的，同一组身份对应同一个 owner，
 * 所以不再重复放进键里，避免 owner key 迟到导致键漂移、旧流对不上。
 */
export function chatStreamKey(identity: ChatScopeIdentity): string {
  return JSON.stringify([
    identity.mode,
    identity.epoch,
    identity.datasetId,
    identity.role,
    identity.studentId,
    identity.nodeId,
  ]);
}

const IDLE: ChatStreamState = Object.freeze({
  key: "",
  identity: null,
  status: "idle",
  pendingUser: null,
  liveText: "",
  liveSteps: [] as ChatProgressStep[],
  liveMode: null,
  conversationId: null,
  startedAt: 0,
  finishedMessageId: null,
  error: null,
  draftText: null,
});

function mergeStep(previous: ChatProgressStep[], incoming: ChatProgressStep): ChatProgressStep[] {
  const next = [...previous];
  if (incoming.kind === "tool" && incoming.call_id) {
    const index = next.findIndex((item) => item.kind === "tool" && item.call_id === incoming.call_id);
    if (index >= 0) {
      next[index] = incoming;
      return next;
    }
  }
  if (
    incoming.kind === "model" &&
    next.length > 0 &&
    next[next.length - 1].kind === "model" &&
    next[next.length - 1].status === "running"
  ) {
    next[next.length - 1] = incoming;
    return next;
  }
  next.push(incoming);
  return next;
}

function toStep(raw: Record<string, unknown>): ChatProgressStep | null {
  const label = typeof raw.label === "string" ? raw.label : "";
  if (!label) return null;
  const status = raw.status === "running" || raw.status === "failed" ? raw.status : "succeeded";
  const kind =
    raw.kind === "status" || raw.kind === "model" || raw.kind === "tool" || raw.kind === "limit" ? raw.kind : "status";
  return {
    kind,
    status,
    label,
    message: typeof raw.message === "string" ? raw.message : undefined,
    tool_name: typeof raw.tool_name === "string" ? raw.tool_name : null,
    call_id: typeof raw.call_id === "string" ? raw.call_id : null,
  };
}

function toStreamError(reason: unknown): ChatStreamError {
  return {
    text: toErrorMessage(reason),
    requestId: errorRequestId(reason),
    details: (reason as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
  };
}

class ChatStreamStore {
  private states = new Map<string, ChatStreamState>();
  private listeners = new Set<() => void>();
  /** 每个键当前允许写入的流编号；clear 或新开流后旧流的写入会被丢弃。 */
  private runTokens = new Map<string, number>();
  private tokenSequence = 0;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (key: string): ChatStreamState => this.states.get(key) ?? IDLE;

  /**
   * 面板按当前身份取状态。节点还没确定（首屏 runs 尚未返回）时发出的流，
   * 会落在节点为 null 的键上；等节点补上后用它兜底，避免刚发出的回答突然消失。
   * 兜底只看节点：角色、数据集、学生不同的键永远不会互相命中。
   */
  resolveKey(identity: ChatScopeIdentity): string {
    const exact = chatStreamKey(identity);
    if (this.states.has(exact)) return exact;
    if (identity.nodeId !== null) {
      const withoutNode = chatStreamKey({ ...identity, nodeId: null });
      if (this.states.has(withoutNode)) return withoutNode;
    }
    return exact;
  }

  /** 开始一次流；同一身份键上已有的状态被替换，旧流的后续写入按流编号丢弃。 */
  start(input: {
    identity: ChatScopeIdentity;
    text: string;
    conversationId: string | null;
    request?: ChatRequestBinding;
  }): string {
    const key = chatStreamKey(input.identity);
    const token = ++this.tokenSequence;
    this.runTokens.set(key, token);
    this.states.set(key, {
      key,
      identity: input.identity,
      status: "streaming",
      pendingUser: input.text,
      liveText: "",
      liveSteps: [],
      liveMode: null,
      conversationId: input.conversationId,
      startedAt: Date.now(),
      finishedMessageId: null,
      error: null,
      draftText: null,
    });
    this.emit();
    void this.run(
      key,
      token,
      input.text,
      input.conversationId,
      input.request ?? {
        nodeId: input.identity.nodeId,
        studentId: input.identity.studentId,
        target: null,
        taskType: null,
      },
    );
    return key;
  }

  append(key: string, chunk: string, mode: "native" | "chunked"): void {
    if (!chunk) return;
    this.update(key, (previous) =>
      previous.status === "streaming"
        ? { ...previous, liveText: previous.liveText + chunk, liveMode: mode }
        : previous,
    );
  }

  updateSteps(key: string, step: ChatProgressStep): void {
    this.update(key, (previous) =>
      previous.status === "streaming"
        ? { ...previous, liveSteps: mergeStep(previous.liveSteps, step) }
        : previous,
    );
  }

  /** 服务端要求丢弃已推送的文本（例如判定为不合格草稿）时使用。 */
  discardText(key: string): void {
    this.update(key, (previous) =>
      previous.status === "streaming" ? { ...previous, liveText: "" } : previous,
    );
  }

  finish(key: string, reply: ChatReply, fallbackText: string): void {
    this.update(key, (previous) => {
      const conversationId =
        typeof reply.conversation_id === "string" && reply.conversation_id
          ? reply.conversation_id
          : previous.conversationId;
      const messageId = typeof reply.message_id === "string" && reply.message_id ? reply.message_id : null;
      const replyText = typeof reply.text === "string" ? reply.text : "";
      const finalText = previous.liveText || replyText || fallbackText;
      return {
        ...previous,
        status: "done",
        liveText: finalText,
        conversationId,
        finishedMessageId: messageId,
        error: null,
        draftText: null,
      };
    });
  }

  fail(key: string, error: ChatStreamError, draftText: string | null): void {
    this.update(key, (previous) => ({
      ...previous,
      status: "error",
      pendingUser: null,
      liveText: "",
      liveSteps: [],
      liveMode: null,
      finishedMessageId: null,
      error,
      draftText,
    }));
  }

  /** 用户显式切走（新对话）或历史已落地后清空；clear 同时让在跑的旧流停止写入。 */
  clear(key: string): void {
    this.runTokens.delete(key);
    if (!this.states.delete(key)) return;
    this.emit();
  }

  /** 测试用：清掉所有身份键，避免用例之间互相影响。 */
  clearAll(): void {
    this.states.clear();
    this.runTokens.clear();
    this.emit();
  }

  private emit(): void {
    this.listeners.forEach((listener) => listener());
  }

  private update(key: string, updater: (previous: ChatStreamState) => ChatStreamState): void {
    const previous = this.states.get(key);
    if (!previous) return;
    const next = updater(previous);
    if (next === previous) return;
    this.states.set(key, next);
    this.emit();
  }

  private isCurrent(key: string, token: number): boolean {
    return this.runTokens.get(key) === token;
  }

  private async run(
    key: string,
    token: number,
    text: string,
    conversationId: string | null,
    request: ChatRequestBinding,
  ): Promise<void> {
    let accumulated = "";
    let finished = false;
    let pendingText = "";
    let pendingMode: "native" | "chunked" = "native";
    let flushTimer: number | null = null;

    const flush = (): void => {
      if (flushTimer !== null) {
        window.clearTimeout(flushTimer);
        flushTimer = null;
      }
      if (!pendingText || !this.isCurrent(key, token)) return;
      const chunk = pendingText;
      pendingText = "";
      this.append(key, chunk, pendingMode);
    };
    // 原生流可能逐字到达；合并到约 25 帧/秒写入，避免每个字符重排整段 Markdown。
    const schedule = (): void => {
      if (flushTimer !== null) return;
      flushTimer = window.setTimeout(() => {
        flushTimer = null;
        flush();
      }, 40);
    };

    try {
      for await (const event of streamChat({
        message: text,
        conversation_id: conversationId,
        node_id: request.nodeId,
        student_id: request.studentId,
        target: request.target,
        task_type: request.taskType,
      })) {
        if (!this.isCurrent(key, token)) return;
        if (event.event === "step") {
          const step = toStep(event.data);
          if (step) this.updateSteps(key, step);
          continue;
        }
        if (event.event === "delta") {
          const chunk = typeof event.data.text === "string" ? event.data.text : "";
          accumulated += chunk;
          pendingText += chunk;
          pendingMode = event.data.mode === "chunked" ? "chunked" : "native";
          schedule();
          continue;
        }
        if (event.event === "discard") {
          accumulated = "";
          pendingText = "";
          this.discardText(key);
          continue;
        }
        if (event.event === "error") {
          const code = typeof event.data.code === "string" ? event.data.code : "UNKNOWN";
          throw new ApiError(
            code,
            typeof event.data.message === "string" ? event.data.message : "流式回答失败。",
            0,
          );
        }
        if (event.event === "done") {
          finished = true;
          flush();
          this.finish(key, event.data as unknown as ChatReply, accumulated);
        }
      }
      if (!finished) {
        throw new ApiError("STREAM_INTERRUPTED", "流式连接提前结束，回答可能未完成。", 0);
      }
    } catch (reason) {
      if (finished || !this.isCurrent(key, token)) return;
      if (flushTimer !== null) {
        window.clearTimeout(flushTimer);
        flushTimer = null;
      }
      const code = errorCode(reason);
      if (code === "HTTP_404" || code === "HTTP_405") {
        // 旧后端没有 SSE 端点：退回旧 POST /chat，工具进度只在完成后由真实 tool_trace 展示。
        try {
          const reply = await sendChat({
            messages: [{ role: "user", content: text }],
            conversation_id: conversationId,
            node_id: request.nodeId,
            student_id: request.studentId,
            target: request.target,
            task_type: request.taskType,
          });
          if (!this.isCurrent(key, token)) return;
          this.finish(key, reply, reply.text);
          return;
        } catch (legacyReason) {
          if (!this.isCurrent(key, token)) return;
          this.fail(key, toStreamError(legacyReason), text);
          return;
        }
      }
      this.fail(key, toStreamError(reason), text);
    }
  }
}

export const chatStreamStore = new ChatStreamStore();
