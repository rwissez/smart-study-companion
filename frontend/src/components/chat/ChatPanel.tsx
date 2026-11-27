import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage, ApiError } from "../../api/client";
import {
  confirmProposal,
  createProposal,
  getCatalog,
  getConversationMessages,
  listConversations,
  sendChat,
  streamChat,
  type ChatProgressStep,
  type ChatStreamEvent,
} from "../../api/service";
import type {
  ChatMessageRecord,
  ChatReply,
  ChatSegment,
  ConfirmationResult,
  ConversationSummary,
  ProposalResponse,
} from "../../api/types";
import { queryKeys } from "../../lib/queryKeys";
import { useSession } from "../../app/session";
import { formatDate } from "../../lib/format";
import { ModelStatus } from "./ModelStatus";
import { SafeMarkdown } from "./SafeMarkdown";
import { StatePanel } from "../feedback/StatePanel";
import { IconAlert, IconCheck } from "../icons";

interface Props {
  role: "teacher" | "student";
  context?: { runId?: string | null; nodeId?: string | null; studentId?: string | null };
  onConfigure?: () => void;
  initialConversationId?: string | null;
}

const MAX_LENGTH = 8000;

const SEGMENT_LABELS: Record<string, string> = {
  fact: "事实",
  explanation: "解释",
  suggestion: "建议",
  operation: "操作结果",
  limitation: "限制",
};

const TOOL_LABELS: Record<string, string> = {
  get_student_analysis: "读取学生学情分析",
  get_answer_evidence: "读取作答证据",
  get_quality_issues: "读取数据质量问题",
  get_class_analysis: "读取班级分析",
  get_student_plan: "读取正式学习方案",
  get_recent_learning_events: "读取最近学习事件",
  get_micro_lesson: "读取微课堂摘要",
  get_open_interventions: "读取开放介入案例",
  search_knowledge: "检索课程知识",
  lookup_prerequisite: "查询前置知识",
  get_knowledge_relations: "读取知识关系",
  rule_summary: "正式学情规则摘要",
  task_route: "识别任务",
};

const PLACEHOLDER_TEACHER =
  "直接提问，例如：D05 这次为什么丢分？帮我核对一下证据。\nEnter 发送，Shift+Enter 换行。";
const PLACEHOLDER_STUDENT =
  "说说你在哪里卡住了，例如：这道题去括号时符号总是写错。\nEnter 发送，Shift+Enter 换行。";

function assistantMeta(message: ChatMessageRecord): string | null {
  if (message.status === "partial") return "规则摘要（未使用模型）";
  if (message.status === "failed") return "回答失败";
  return null;
}

function toolLabel(name: string): string {
  return TOOL_LABELS[name] ?? "受控工具";
}

function traceLabel(trace: Record<string, unknown>): string {
  const name = typeof trace.tool_name === "string" ? trace.tool_name : "";
  const status = typeof trace.status === "string" ? trace.status : "succeeded";
  const prefix = status === "failed" ? "失败" : "完成";
  return `${prefix}：${toolLabel(name)}`;
}

function SegmentList({ segments }: { segments: ChatSegment[] }) {
  const visible = segments.filter((segment) => segment.text.trim());
  if (visible.length === 0) return null;
  return (
    <div className="segmentList">
      {visible.map((segment, index) => (
        <section key={`${segment.kind}-${index}`} className="segmentItem">
          <span className="tag">{SEGMENT_LABELS[segment.kind] ?? "内容"}</span>
          <div className="segmentText">
            <SafeMarkdown text={segment.text} />
          </div>
        </section>
      ))}
    </div>
  );
}

function StepTimeline({ steps, live }: { steps: ChatProgressStep[]; live: boolean }) {
  if (steps.length === 0) return null;
  return (
    <ol className={`stepTimeline${live ? " stepTimelineLive" : ""}`} aria-live="polite" aria-label="工具执行进度">
      {steps.map((step, index) => (
        <li key={`${step.kind}-${step.tool_name ?? ""}-${index}`} className={`stepTimelineItem step-${step.status}`}>
          <span className="stepTimelineIcon" aria-hidden="true">
            {step.status === "succeeded" ? <IconCheck size={13} /> : step.status === "failed" ? <IconAlert size={13} /> : <span className="stepDot" />}
          </span>
          <span className="stepTimelineText">
            {step.label}
            {step.message ? <span className="xs muted"> · {step.message}</span> : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

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

/**
 * 师生共用对话面板：中间是主要回答区，历史在左侧；模型状态缩成右上角标记。
 * 发送后优先读取 POST /chat/stream 的 SSE：工具进度来自 AgentLoop 真实事件，
 * 回答逐段出现；服务端不支持流式时降级为旧 POST /chat，不伪造进度。
 */
export function ChatPanel({ role, context, onConfigure, initialConversationId }: Props) {
  const { scopeKey } = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(initialConversationId ?? null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [composing, setComposing] = useState(false);
  const [error, setError] = useState<{ text: string; requestId: string | null; details: Array<{ field?: string; reason?: string }> } | null>(null);
  const [proposal, setProposal] = useState<ProposalResponse | null>(null);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [decisionResult, setDecisionResult] = useState<ConfirmationResult | null>(null);
  const [liveText, setLiveText] = useState("");
  const [liveSteps, setLiveSteps] = useState<ChatProgressStep[]>([]);
  const [liveMode, setLiveMode] = useState<"native" | "chunked" | null>(null);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [finishedMessageId, setFinishedMessageId] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const autoSelectedRef = useRef(false);
  const stickToBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const onLogScroll = () => {
    const node = logRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    const stick = distance < 80;
    stickToBottom.current = stick;
    setShowJump(!stick);
  };

  const jumpToBottom = () => {
    const node = logRef.current;
    if (!node) return;
    stickToBottom.current = true;
    setShowJump(false);
    if (typeof node.scrollTo === "function") node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    else node.scrollTop = node.scrollHeight;
  };

  const conversations = useQuery<{ items: ConversationSummary[] }>({
    queryKey: queryKeys.conversations(scopeKey),
    queryFn: listConversations,
  });

  const catalog = useQuery({
    queryKey: queryKeys.catalog(scopeKey, null),
    queryFn: () => getCatalog(),
  });

  const indicatorNames = useMemo(() => {
    const map: Record<string, string> = {};
    (catalog.data?.indicators ?? []).forEach((indicator) => {
      map[indicator.indicator_id] = indicator.description;
    });
    return map;
  }, [catalog.data]);

  // 服务端规则摘要会带指标编号与节点编号；页面展示时换成可读名称，不改变服务端判定。
  const sanitize = useCallback(
    (text: string): string => {
      let result = text;
      Object.entries(indicatorNames).forEach(([code, name]) => {
        result = result.split(code).join(name);
      });
      return result
        .replace(/节点\s*N\d+/g, "当前观察节点")
        .replace(/\binsufficient\b/g, "证据不足")
        .replace(/\bundetermined\b/g, "暂不定论")
        .replace(/\bsufficient\b/g, "证据充分");
    },
    [indicatorNames],
  );

  useEffect(() => {
    if (!selectedId && conversations.data?.items.length && !autoSelectedRef.current) {
      autoSelectedRef.current = true;
      setSelectedId(conversations.data.items[0].conversation_id);
    }
  }, [conversations.data, selectedId]);

  const messages = useQuery<ChatMessageRecord[]>({
    queryKey: queryKeys.conversationMessages(scopeKey, selectedId ?? ""),
    queryFn: () => getConversationMessages(selectedId as string),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    if (!stickToBottom.current) return;
    const timer = window.setTimeout(() => {
      const node = logRef.current;
      if (!node) return;
      if (typeof node.scrollTo === "function") node.scrollTo({ top: node.scrollHeight });
      else node.scrollTop = node.scrollHeight;
    }, 0);
    return () => window.clearTimeout(timer);
  }, [messages.data, busy, liveText, liveSteps]);

  // conversation_id 写回 URL，刷新后仍能打开同一会话。
  useEffect(() => {
    if (!selectedId) return;
    if (searchParams.get("conversationId") === selectedId) return;
    const next = new URLSearchParams(searchParams);
    next.set("conversationId", selectedId);
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  // 新回答已进入服务端历史后再撤掉本地流式预览，避免闪空或重复。
  useEffect(() => {
    if (!finishedMessageId) return;
    if (messages.data?.some((message) => message.message_id === finishedMessageId)) {
      setLiveText("");
      setLiveSteps([]);
      setLiveMode(null);
      setPendingUser(null);
      setFinishedMessageId(null);
    }
  }, [finishedMessageId, messages.data]);

  const selectConversation = (conversationId: string) => {
    autoSelectedRef.current = true;
    setSelectedId(conversationId);
    setError(null);
    setDecisionResult(null);
  };

  const startNew = () => {
    autoSelectedRef.current = true;
    setSelectedId(null);
    setDraft("");
    setError(null);
    setProposal(null);
    setDecisionResult(null);
    setProposalError(null);
    setLiveText("");
    setLiveSteps([]);
    setLiveMode(null);
    setPendingUser(null);
    setFinishedMessageId(null);
    const next = new URLSearchParams(searchParams);
    next.delete("conversationId");
    setSearchParams(next, { replace: true });
  };

  const requestProposal = async () => {
    if (!selectedId) {
      setProposalError("请先发送一条消息建立会话，再生成方案候选。");
      return;
    }
    if (!context?.studentId || !context?.nodeId) {
      setProposalError("需要先在学生画像里选择学生与观察节点，才能生成方案候选。");
      return;
    }
    setProposalBusy(true);
    setProposalError(null);
    setDecisionResult(null);
    try {
      const result = await createProposal(selectedId, {
        studentId: context.studentId,
        nodeId: context.nodeId,
        intent: "按当前学情重新生成一版安排",
      });
      setProposal(result);
    } catch (reason) {
      setProposalError(
        `${toErrorMessage(reason)}${errorRequestId(reason) ? `（请求编号 ${errorRequestId(reason)}）` : ""}`,
      );
    } finally {
      setProposalBusy(false);
    }
  };

  const decideProposal = async (allow: boolean) => {
    if (!proposal || !selectedId || !context?.nodeId) return;
    setProposalBusy(true);
    setProposalError(null);
    try {
      const result = await confirmProposal(selectedId, context.nodeId, {
        confirmationId: proposal.confirmation.confirmation_id,
        allow,
      });
      setDecisionResult(result);
      setProposal(null);
      await queryClient.invalidateQueries({ queryKey: [scopeKey.mode, scopeKey.epoch] });
    } catch (reason) {
      if (errorCode(reason) === "CONFLICT" || errorCode(reason) === "REVISION_CONFLICT") {
        setProposalError("确认已过期或方案已更新，请重新生成候选。");
      } else {
        setProposalError(`${toErrorMessage(reason)}${errorRequestId(reason) ? `（请求编号 ${errorRequestId(reason)}）` : ""}`);
      }
    } finally {
      setProposalBusy(false);
    }
  };

  const applyDone = useCallback(
    async (done: ChatReply, fallbackText: string) => {
      const conversationId = typeof done.conversation_id === "string" ? done.conversation_id : selectedId;
      const messageId = typeof done.message_id === "string" ? done.message_id : null;
      if (!done.text && fallbackText) {
        setLiveText((previous) => previous || fallbackText);
      }
      if (conversationId) {
        autoSelectedRef.current = true;
        setSelectedId(conversationId);
        if (messageId) {
          setFinishedMessageId(messageId);
        } else {
          setLiveText("");
          setLiveSteps([]);
          setLiveMode(null);
          setPendingUser(null);
        }
        await queryClient.invalidateQueries({ queryKey: queryKeys.conversations(scopeKey) });
        await queryClient.invalidateQueries({
          queryKey: queryKeys.conversationMessages(scopeKey, conversationId),
        });
      } else {
        // 演示模式或旧服务没有返回会话编号：仍然刷新历史与消息。
        await queryClient.invalidateQueries({ queryKey: queryKeys.conversations(scopeKey) });
        setLiveText("");
        setLiveSteps([]);
        setPendingUser(null);
      }
    },
    [queryClient, scopeKey, selectedId],
  );

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    const previousConversation = selectedId;
    setError(null);
    setBusy(true);
    setDraft("");
    setPendingUser(text);
    setLiveText("");
    setLiveSteps([]);
    setLiveMode(null);
    setFinishedMessageId(null);

    let accumulated = "";
    let finished = false;
    let pendingText = "";
    let pendingMode: "native" | "chunked" = "native";
    let flushTimer: number | null = null;
    const flushLive = () => {
      if (!pendingText) return;
      const chunk = pendingText;
      pendingText = "";
      setLiveText((previous) => previous + chunk);
      setLiveMode(pendingMode);
    };
    const scheduleFlush = () => {
      if (flushTimer !== null) return;
      flushTimer = window.setTimeout(() => {
        flushTimer = null;
        flushLive();
      }, 40);
    };

    const handleEvent = async (event: ChatStreamEvent) => {
      if (event.event === "step") {
        const step = toStep(event.data);
        if (step) {
          setLiveSteps((previous) => mergeStep(previous, step));
        }
        return;
      }
      if (event.event === "delta") {
        const chunk = typeof event.data.text === "string" ? event.data.text : "";
        accumulated += chunk;
        pendingText += chunk;
        pendingMode = event.data.mode === "chunked" ? "chunked" : "native";
        // 原生流可能逐字到达；合并到约 25 帧/秒刷新，避免每个字符重排整段 Markdown。
        scheduleFlush();
        return;
      }
      if (event.event === "discard") {
        accumulated = "";
        pendingText = "";
        setLiveText("");
        return;
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
        flushLive();
        await applyDone(event.data as unknown as ChatReply, accumulated);
      }
    };

    try {
      for await (const event of streamChat({
        message: text,
        conversation_id: previousConversation,
        node_id: context?.nodeId ?? null,
        student_id: context?.studentId ?? null,
      })) {
        await handleEvent(event);
      }
      if (!finished) {
        throw new ApiError("STREAM_INTERRUPTED", "流式连接提前结束，回答可能未完成。", 0);
      }
    } catch (reason) {
      const code = errorCode(reason);
      const streamMissing = code === "HTTP_404" || code === "HTTP_405";
      if (streamMissing) {
        // 旧后端没有 SSE 端点：退回旧 POST /chat，工具进度只在完成后由真实 tool_trace 展示。
        try {
          const reply = await sendChat({
            messages: [{ role: "user", content: text }],
            conversation_id: previousConversation,
            node_id: context?.nodeId ?? null,
            student_id: context?.studentId ?? null,
          });
          await applyDone(reply, reply.text);
          return;
        } catch (legacyReason) {
          setError({
            text: toErrorMessage(legacyReason),
            requestId: errorRequestId(legacyReason),
            details: (legacyReason as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
          });
        }
      } else {
        setError({
          text: toErrorMessage(reason),
          requestId: errorRequestId(reason),
          details: (reason as { details?: Array<{ field?: string; reason?: string }> }).details ?? [],
        });
      }
      setDraft(text);
      pendingText = "";
      setLiveText("");
      setLiveSteps([]);
      setLiveMode(null);
      setPendingUser(null);
      setFinishedMessageId(null);
    } finally {
      if (flushTimer !== null) window.clearTimeout(flushTimer);
      setBusy(false);
    }
  };

  const items = messages.data ?? [];
  const streaming = busy;
  const liveActive = Boolean(pendingUser) || Boolean(liveText) || liveSteps.length > 0;
  const canPropose = role === "teacher" && Boolean(context?.studentId && context?.nodeId);

  return (
    <div className="chatLayout">
      <aside className="chatSide" aria-label="会话历史">
        <div className="rowBetween">
          <h3 className="chatSideTitle" style={{ marginBottom: 0 }}>会话历史</h3>
          <button type="button" className="btn btnSm btnGhost" onClick={startNew} disabled={busy}>新对话</button>
        </div>
        {conversations.isPending ? <p className="chatHistoryEmpty">正在读取会话…</p> : null}
        {conversations.isError ? (
          <p className="small fieldError">会话历史读取失败：{toErrorMessage(conversations.error)}</p>
        ) : null}
        {conversations.data && conversations.data.items.length === 0 ? (
          <p className="chatHistoryEmpty">还没有历史会话。发送第一条消息后会出现在这里。</p>
        ) : null}
        <div className="conversationList">
          {(conversations.data?.items ?? []).map((conversation) => (
            <button
              key={conversation.conversation_id}
              type="button"
              className={selectedId === conversation.conversation_id ? "conversationItem conversationItemActive" : "conversationItem"}
              aria-pressed={selectedId === conversation.conversation_id}
              disabled={busy}
              onClick={() => selectConversation(conversation.conversation_id)}
            >
              <span className="conversationTitle">{conversation.title || "新的对话"}</span>
              <span className="conversationMeta">{formatDate(conversation.updated_at)}</span>
            </button>
          ))}
        </div>
        <div className="chatSideMeta">
          <p className="xs muted">
            对象：{context?.studentId ? `学生 ${context.studentId}` : role === "student" ? "本人" : "当前数据集"}
            <br />
            节点：{context?.nodeId ? "已绑定当前观察节点" : "未绑定"}
          </p>
          <p className="xs muted">对象与授权由服务端重新校验；历史文本不能当作新的授权。</p>
        </div>
      </aside>

      <div className="chatMain">
        <div className="chatTopBar">
          <div className="chatTopMeta">
            <span className="chatTopTitle">湛知</span>
            <span className="xs muted">
              {role === "teacher" ? "按当前数据集与授权对象回答" : "只使用本人数据，回答会标注来源"}
            </span>
          </div>
          <div className="row" style={{ gap: 8 }}>
            {canPropose ? (
              <button
                type="button"
                className="btn btnSm"
                onClick={() => void requestProposal()}
                disabled={proposalBusy || busy || !selectedId}
                title={!selectedId ? "先发送一条消息建立会话" : "基于当前学情生成方案候选"}
              >
                {proposalBusy ? "生成中…" : "生成方案候选"}
              </button>
            ) : null}
            {role === "teacher" ? <ModelStatus onConfigure={onConfigure ?? (() => undefined)} /> : null}
          </div>
        </div>

        <div className="chatLog" ref={logRef} aria-live="polite" onScroll={onLogScroll}>
          {messages.isPending && selectedId ? <StatePanel kind="loading" title="正在读取会话消息" /> : null}
          {messages.isError ? (
            <StatePanel
              kind="error"
              title="会话消息读取失败"
              message={toErrorMessage(messages.error)}
              requestId={errorRequestId(messages.error)}
              onRetry={() => void messages.refetch()}
            />
          ) : null}
          {!messages.isPending && items.length === 0 && !liveActive ? (
            <div className="chatEmpty">
              <p className="chatEmptyTitle">{selectedId ? "这个会话还没有消息" : "开始一次新的对话"}</p>
              <p className="small muted">
                {role === "teacher"
                  ? "可以问学生为什么丢分、核对证据、查看数据质量问题或学习方案。"
                  : "可以问自己的学习进展、题目思路或下一步学什么。"}
              </p>
            </div>
          ) : null}
          {items.map((message) => {
            const meta = message.role === "assistant" ? assistantMeta(message) : null;
            const segments = (message.segments ?? []).map((segment) => ({ ...segment, text: sanitize(segment.text) }));
            const traces = message.tool_trace ?? [];
            return (
              <article className={`chatMsg chatMsg-${message.role}`} key={message.message_id}>
                <div className="chatRole">
                  <span>{message.role === "user" ? "我" : "湛知"}</span>
                  {meta ? <span className={message.status === "failed" ? "tag tagError" : "tag tagWarning"}>{meta}</span> : null}
                  <span className="xs muted">{formatDate(message.created_at)}</span>
                </div>
                {message.role === "assistant" && traces.length ? (
                  <details className="traceDetails">
                    <summary className="xs muted">工具进度（{traces.length} 步，来自服务端记录）</summary>
                    <ul className="traceList">
                      {traces.map((trace, index) => (
                        <li key={index} className="xs muted">{traceLabel(trace as Record<string, unknown>)}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
                {segments.some((segment) => segment.text.trim()) ? (
                  <SegmentList segments={segments} />
                ) : (
                  <div className={message.role === "user" ? "chatText chatTextUser" : "chatText"}>
                    <SafeMarkdown text={sanitize(message.content)} />
                  </div>
                )}
              </article>
            );
          })}

          {pendingUser ? (
            <article className="chatMsg chatMsg-user chatMsgLive">
              <div className="chatRole">
                <span>我</span>
                {streaming ? <span className="xs muted">等待回答…</span> : null}
              </div>
              <div className="chatText chatTextUser">
                <SafeMarkdown text={pendingUser} />
              </div>
            </article>
          ) : null}

          {liveText || liveSteps.length ? (
            <article className="chatMsg chatMsg-assistant chatMsgLive">
              <div className="chatRole">
                <span>湛知</span>
                {liveMode === "chunked" ? <span className="tag tagWarning">非模型原生流</span> : null}
                {streaming ? <span className="xs muted">生成中…</span> : null}
              </div>
              {liveSteps.length ? <StepTimeline steps={liveSteps} live={streaming} /> : null}
              {liveText ? (
                <div className="chatText chatTextStreaming">
                  <SafeMarkdown text={sanitize(liveText)} />
                </div>
              ) : null}
            </article>
          ) : null}
        </div>
        {showJump ? (
          <button type="button" className="btn btnSm chatJumpBottom" onClick={jumpToBottom}>
            回到底部
          </button>
        ) : null}

        {proposalError ? <p className="fieldError" role="alert">{proposalError}</p> : null}

        {proposal ? (
          <div className="confirmCard" role="group" aria-label="方案候选确认">
            <div className="rowBetween">
              <strong>需要确认：发布新的学习方案</strong>
              <span className="badge badgeWarning">等待确认</span>
            </div>
            <dl className="kv">
              <dt>影响学生</dt><dd>{proposal.confirmation.targets.map((target) => target.student_id).join("、") || context?.studentId}</dd>
              <dt>候选版本</dt><dd>基于当前正式方案生成的新版本（确认后由服务端分配版本号）</dd>
              <dt>警告条数</dt><dd className="tabular">{proposal.proposal.warnings.length}</dd>
              <dt>预计用时</dt><dd className="tabular">{proposal.proposal.estimated_minutes ?? "—"} 分钟</dd>
              <dt>过期时间</dt><dd>{formatDate(proposal.confirmation.expires_at)}</dd>
            </dl>
            <p className="small">{proposal.confirmation.summary}</p>
            {proposal.proposal.blocked_reasons.length ? (
              <p className="small fieldError">阻断原因：{proposal.proposal.blocked_reasons.join("；")}</p>
            ) : null}
            <div className="row">
              <button type="button" className="btn btnPrimary" onClick={() => void decideProposal(true)} disabled={proposalBusy}>
                接受并发布
              </button>
              <button type="button" className="btn" onClick={() => void decideProposal(false)} disabled={proposalBusy}>
                拒绝
              </button>
              <span className="xs muted">接受与拒绝都会记录；不能通过普通聊天回复代替确认。</span>
            </div>
          </div>
        ) : null}

        {decisionResult ? (
          <div className="statePanel" role="status">
            <span className="stateTitle">
              {decisionResult.decision === "accepted" ? "已接受并执行发布" : "已拒绝，方案不变"}
            </span>
            {decisionResult.operations.length ? (
              <div className="tableWrap">
                <table className="table">
                  <thead>
                    <tr><th scope="col">学生</th><th scope="col">状态</th><th scope="col">发布状态</th></tr>
                  </thead>
                  <tbody>
                    {decisionResult.operations.map((operation) => (
                      <tr key={operation.student_id}>
                        <td className="small">{operation.student_id}</td>
                        <td className="small">
                          {operation.status === "succeeded" || operation.status === "published"
                            ? "已完成"
                            : operation.status === "failed"
                              ? "失败"
                              : "处理中"}
                        </td>
                        <td className="small">
                          {operation.publication_status === "published" ? "已发布" : operation.publication_status ?? "—"}
                          {operation.new_plan_version ? (
                            <div className="xs muted">新方案版本：{operation.new_plan_version}</div>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <span className="small muted">服务端没有返回操作结果。</span>
            )}
            {context?.studentId ? (
              <div className="row">
                <button
                  type="button"
                  className="btn btnSm"
                  onClick={() => navigate(`/teacher/students/${encodeURIComponent(context.studentId as string)}?tab=plan`)}
                >
                  查看该学生的正式方案
                </button>
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="chatComposer">
          <label className="srOnly" htmlFor="chat-input">输入消息</label>
          <textarea
            id="chat-input"
            className="textarea chatInput"
            value={draft}
            maxLength={MAX_LENGTH}
            disabled={busy}
            placeholder={role === "teacher" ? PLACEHOLDER_TEACHER : PLACEHOLDER_STUDENT}
            onChange={(event) => setDraft(event.target.value)}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !composing) {
                event.preventDefault();
                void send();
              }
            }}
          />
          <div className="chatToolbar">
            <span className="xs muted">
              {role === "student"
                ? "只使用本人数据；获取帮助会记入真实作答状态。"
                : "回答由受控工具与模型生成，工具进度是真实执行事件。"}
            </span>
            <span className="row" style={{ gap: 12 }}>
              <span className="xs muted tabular">{draft.length} / {MAX_LENGTH}</span>
              <button
                type="button"
                className="btn btnPrimary"
                onClick={() => void send()}
                disabled={busy || !draft.trim()}
              >
                {busy ? "回答中…" : "发送"}
              </button>
            </span>
          </div>
          {error ? (
            <div className="fieldError" role="alert">
              {error.text}
              {error.requestId ? `（请求编号 ${error.requestId}）` : ""}
              {error.details.length ? (
                <ul>{error.details.map((detail, index) => <li key={index} className="small">{detail.field ? `${detail.field}：` : ""}{detail.reason}</li>)}</ul>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
