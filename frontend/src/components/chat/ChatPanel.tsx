import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { errorCode, errorRequestId, toErrorMessage } from "../../api/client";
import {
  confirmProposal,
  createProposal,
  getCatalog,
  getConversationMessages,
  listConversations,
  sendChat,
} from "../../api/service";
import type {
  ChatMessageRecord,
  ChatSegment,
  ConfirmationResult,
  ConversationSummary,
  ProposalResponse,
} from "../../api/types";
import { queryKeys } from "../../lib/queryKeys";
import { useSession } from "../../app/session";
import { formatDate } from "../../lib/format";
import { ModelStatus } from "./ModelStatus";
import { StatePanel } from "../feedback/StatePanel";

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

function assistantMeta(message: ChatMessageRecord): string | null {
  if (message.status === "partial") return "规则摘要（未使用模型）";
  if (message.status === "failed") return "回答失败";
  return null;
}

function SegmentList({ segments }: { segments: ChatSegment[] }) {
  const visible = segments.filter((segment) => segment.text.trim());
  if (visible.length === 0) return null;
  return (
    <ul className="segmentList">
      {visible.map((segment, index) => (
        <li key={`${segment.kind}-${index}`} className="segmentItem">
          <span className="tag">{SEGMENT_LABELS[segment.kind] ?? "内容"}</span>
          <span className="segmentText">{segment.text}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * 师生共用对话面板：会话与消息来自服务端历史；模型未配置时回答标注为
 * “规则摘要（未使用模型）”，不把规则结果伪装成模型回答。
 */
export function ChatPanel({ role, context, onConfigure, initialConversationId }: Props) {
  const { scopeKey } = useSession();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState<string | null>(initialConversationId ?? null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [composing, setComposing] = useState(false);
  const [error, setError] = useState<{ text: string; requestId: string | null } | null>(null);
  const [proposal, setProposal] = useState<ProposalResponse | null>(null);
  const [proposalBusy, setProposalBusy] = useState(false);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [decisionResult, setDecisionResult] = useState<ConfirmationResult | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

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
  const sanitize = (text: string): string => {
    let result = text;
    Object.entries(indicatorNames).forEach(([code, name]) => {
      result = result.split(code).join(name);
    });
    return result
      .replace(/节点\s*N\d+/g, "当前观察节点")
      .replace(/\binsufficient\b/g, "证据不足")
      .replace(/\bundetermined\b/g, "暂不定论")
      .replace(/\bsufficient\b/g, "证据充分");
  };

  useEffect(() => {
    if (!selectedId && conversations.data?.items.length) {
      setSelectedId(conversations.data.items[0].conversation_id);
    }
  }, [conversations.data, selectedId]);

  const messages = useQuery<ChatMessageRecord[]>({
    queryKey: queryKeys.conversationMessages(scopeKey, selectedId ?? ""),
    queryFn: () => getConversationMessages(selectedId as string),
    enabled: Boolean(selectedId),
  });

  useEffect(() => {
    window.setTimeout(() => logRef.current?.scrollTo({ top: logRef.current.scrollHeight }), 0);
  }, [messages.data, busy]);

  const startNew = () => {
    setSelectedId(null);
    setDraft("");
    setError(null);
    setProposal(null);
    setDecisionResult(null);
    setProposalError(null);
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

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setError(null);
    setBusy(true);
    setDraft("");
    try {
      const response = await sendChat({
        messages: [{ role: "user", content: text }],
        conversation_id: selectedId,
        run_id: context?.runId ?? null,
        node_id: context?.nodeId ?? null,
        student_id: context?.studentId ?? null,
      });
      const conversationId = response.conversation_id ?? selectedId;
      if (conversationId) {
        setSelectedId(conversationId);
        await queryClient.invalidateQueries({
          queryKey: queryKeys.conversationMessages(scopeKey, conversationId),
        });
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.conversations(scopeKey) });
    } catch (reason) {
      setError({ text: toErrorMessage(reason), requestId: errorRequestId(reason) });
      setDraft(text);
    } finally {
      setBusy(false);
    }
  };

  const items = messages.data ?? [];

  return (
    <div className="chatLayout">
      <aside className="chatSide" aria-label="会话历史">
        <div className="rowBetween">
          <h3 className="chatSideTitle" style={{ marginBottom: 0 }}>会话历史</h3>
          <button type="button" className="btn btnSm btnGhost" onClick={startNew}>新对话</button>
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
              onClick={() => setSelectedId(conversation.conversation_id)}
            >
              <span className="conversationTitle">{conversation.title || "新的对话"}</span>
              <span className="conversationMeta">{formatDate(conversation.updated_at)}</span>
            </button>
          ))}
        </div>
        <div className="stackSm" style={{ marginTop: 16 }}>
          <h3 className="chatSideTitle">当前上下文</h3>
          <p className="xs muted">
            对象：{context?.studentId ? `学生 ${context.studentId}` : role === "student" ? "本人" : "当前数据集"}
            <br />
            节点：{context?.nodeId ? "已绑定当前观察节点" : "未绑定"}
          </p>
          <p className="xs muted">对象与授权由服务端重新校验；教师读对象不等于可以写对象。</p>
        </div>
      </aside>

      <div className="chatMain">
        {role === "teacher" ? <ModelStatus onConfigure={onConfigure} /> : (
          <div className="chatNotice" role="status">
            回答会标注来源。模型未配置时服务端只返回规则摘要，页面会明确标注“未使用模型”，不会把它当成模型回答。
          </div>
        )}

        {role === "teacher" && !context?.studentId ? (
          <div className="chatNotice" role="status">
            教师对话需要先绑定学生：请从学生画像的“就此学生提问”进入，或在地址里带上学生与观察节点。
          </div>
        ) : null}

        {role === "teacher" ? (
          <div className="rowBetween">
            <span className="small muted">
              教师可让 Agent 生成方案候选；确认卡会列出影响学生、准备发布的版本与警告，接受后才发布。
            </span>
            <button
              type="button"
              className="btn btnSm"
              onClick={() => void requestProposal()}
              disabled={proposalBusy || !selectedId || !context?.studentId || !context?.nodeId}
              title={!selectedId ? "先发送一条消息建立会话" : "生成方案候选"}
            >
              {proposalBusy ? "生成中…" : "生成方案候选"}
            </button>
          </div>
        ) : null}

        <div className="chatLog" ref={logRef} aria-live="polite">
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
          {!messages.isPending && items.length === 0 ? (
            <p className="muted small">
              {selectedId ? "这个会话还没有消息。" : "还没有消息。发送后，服务端的真实结果会出现在这里。"}
            </p>
          ) : null}
          {items.map((message) => {
            const meta = message.role === "assistant" ? assistantMeta(message) : null;
            const segments = (message.segments ?? []).map((segment) => ({ ...segment, text: sanitize(segment.text) }));
            return (
              <div className="chatMsg" key={message.message_id}>
                <div className="chatRole">
                  {message.role === "user" ? "我" : "湛知"}
                  {meta ? <span className="tag tagWarning" style={{ marginLeft: 8 }}>{meta}</span> : null}
                  <span className="xs muted" style={{ marginLeft: 8 }}>{formatDate(message.created_at)}</span>
                </div>
                {segments.some((segment) => segment.text.trim()) ? (
                  <SegmentList segments={segments} />
                ) : (
                  <div className="chatText prose">{sanitize(message.content)}</div>
                )}
              </div>
            );
          })}
          {busy ? <p className="small muted">正在等待服务端结果…</p> : null}
        </div>

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
            className="textarea"
            style={{ minHeight: 96 }}
            value={draft}
            maxLength={MAX_LENGTH}
            disabled={busy || (role === "teacher" && !context?.studentId)}
            placeholder={
              role === "teacher" && !context?.studentId
                ? "先绑定学生与观察节点，再开始对话"
                : "输入问题，Enter 发送，Shift+Enter 换行"
            }
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
              {draft.length} / {MAX_LENGTH}
              {role === "teacher" && !context?.studentId ? " · 发送已禁用：未绑定学生" : ""}
            </span>
            <button
              type="button"
              className="btn btnPrimary"
              onClick={() => void send()}
              disabled={busy || !draft.trim() || (role === "teacher" && !context?.studentId)}
            >
              {busy ? "发送中…" : "发送"}
            </button>
          </div>
          {error ? (
            <p className="fieldError" role="alert">
              {error.text}
              {error.requestId ? `（请求编号 ${error.requestId}）` : ""}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
