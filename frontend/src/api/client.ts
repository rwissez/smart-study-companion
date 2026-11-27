import type { ApiEnvelope, ApiErrorBody, ApiWarning } from "./types";

/** 默认连接本机后端；可用 VITE_API_BASE_URL 覆盖。 */
export const API_BASE_URL = (
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim() ||
  "http://127.0.0.1:8000/api/v1"
).replace(/\/+$/, "");

const CODE_MESSAGES: Record<string, string> = {
  UNAUTHORIZED: "登录状态已失效，请重新选择身份。",
  FORBIDDEN: "当前身份无权查看这部分内容。",
  NOT_FOUND: "请求的内容不存在。",
  CONFLICT: "内容已更新，请先读取最新版本。",
  VALIDATION_ERROR: "提交内容不符合要求，请检查标出的字段。",
  UNPROCESSABLE: "服务器无法处理这次请求。",
  RESULT_NOT_READY: "结果尚未就绪。",
  KB_NOT_FOUND: "知识版本不存在。",
  PLAN_SUSPENDED: "该活动已暂停。",
  MODEL_NOT_CONFIGURED: "模型服务尚未配置。",
  CONTEXT_MISMATCH: "当前会话与请求的对象不一致。",
  TASK_NOT_AVAILABLE: "该任务暂不可用。",
  IDEMPOTENCY_CONFLICT: "相同的请求编号已用于不同内容。",
  PAYLOAD_TOO_LARGE: "上传文件超过大小限制。",
  INTERNAL_ERROR: "服务内部错误。",
};

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId: string | null;
  readonly details: Array<{ field?: string; reason?: string }>;

  constructor(
    code: string,
    message: string,
    status: number,
    requestId: string | null = null,
    details: Array<{ field?: string; reason?: string }> = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
    this.details = details;
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

let authToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

/** token 只保存在内存，刷新页面即失效并回到身份入口。 */
export function setAuthToken(token: string | null) {
  authToken = token;
}

export function getAuthToken(): string | null {
  return authToken;
}

/** 401 统一回到身份页并清缓存；由会话 Provider 注册。 */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

interface RequestOptions extends RequestInit {
  /** 稳定的幂等键；同一次业务意图重试必须复用。 */
  idempotencyKey?: string;
}

export interface ApiResult<T> {
  data: T;
  warnings: ApiWarning[];
  requestId: string | null;
}

function parseJson(text: string, status: number, requestId: string | null): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError("INVALID_RESPONSE", "服务返回的内容无法解析。", status, requestId);
  }
}

export async function requestEnvelope<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const { idempotencyKey, ...init } = options;
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new NetworkError("无法连接服务，请确认后端已启动并检查网络。");
  }

  const text = await response.text();
  const headerRequestId = response.headers.get("X-Request-ID");
  const body = text ? (parseJson(text, response.status, headerRequestId) as Record<string, unknown>) : null;

  if (response.status === 401) {
    unauthorizedHandler?.();
  }

  if (!response.ok || (body && "error" in body)) {
    const errorBody = (body as ApiErrorBody | null)?.error;
    const code = errorBody?.code ?? `HTTP_${response.status}`;
    const message = CODE_MESSAGES[code] ?? errorBody?.message ?? "请求失败，请稍后再试。";
    throw new ApiError(
      code,
      message,
      response.status,
      (body as ApiErrorBody | null)?.request_id ?? headerRequestId,
      errorBody?.details ?? [],
    );
  }

  const envelope = body as ApiEnvelope<T> | null;
  if (!envelope || typeof envelope !== "object" || !("data" in envelope)) {
    throw new ApiError("INVALID_RESPONSE", "服务返回的包络缺少 data。", response.status, headerRequestId);
  }
  return { data: envelope.data, warnings: envelope.warnings ?? [], requestId: envelope.request_id ?? null };
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const result = await requestEnvelope<T>(path, options);
  return result.data;
}

/** 授权下载：与其他请求共用 Bearer token；调用方负责用后 revoke Blob URL。 */
export async function requestBlob(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const headers = new Headers();
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { headers });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new NetworkError("无法连接服务，下载没有开始。");
  }
  if (!response.ok) {
    const text = await response.text();
    let body: ApiErrorBody | null = null;
    try {
      body = text ? (JSON.parse(text) as ApiErrorBody) : null;
    } catch {
      body = null;
    }
    const code = body?.error?.code ?? `HTTP_${response.status}`;
    throw new ApiError(
      code,
      CODE_MESSAGES[code] ?? body?.error?.message ?? "下载失败。",
      response.status,
      body?.request_id ?? response.headers.get("X-Request-ID"),
      body?.error?.details ?? [],
    );
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  return { blob: await response.blob(), filename: match ? match[1] : null };
}

const STATUS_FALLBACK: Record<number, string> = {
  401: "登录状态已失效，请重新选择身份。",
  403: "当前身份无权查看这部分内容。",
  404: "请求的内容不存在。",
  409: "内容已更新或尚未就绪，请读取最新状态。",
  413: "上传内容超过大小限制。",
  422: "提交内容不符合要求。",
  503: "服务暂不可用，请稍后再试。",
};

export function toErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const base = error.message || STATUS_FALLBACK[error.status] || "请求失败。";
    if (error.status >= 500 || error.code === "INTERNAL_ERROR" || error.code === "INVALID_RESPONSE") {
      return error.requestId ? `${base}（请求编号 ${error.requestId}）` : base;
    }
    return base;
  }
  if (error instanceof NetworkError) return error.message;
  if (error instanceof Error) return error.message;
  return "请求失败，请稍后再试。";
}

export function errorRequestId(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null;
}

export function errorCode(error: unknown): string {
  if (error instanceof ApiError) return error.code;
  if (error instanceof NetworkError) return "NETWORK_ERROR";
  return "UNKNOWN";
}

export function errorDetails(error: unknown): Array<{ field?: string; reason?: string }> {
  return error instanceof ApiError ? error.details : [];
}

/** 一次明确业务意图对应一个幂等键；重试复用同一个键。 */
export function newIdempotencyKey(prefix: string): string {
  const random =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}
