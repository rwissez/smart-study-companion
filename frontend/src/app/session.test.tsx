import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/service", () => ({
  dataSource: "api",
  isDemo: false,
  createSession: vi.fn(),
}));

const STORED = {
  session_token: "token-restored",
  expires_at: "2099-01-01T00:00:00+00:00",
  scope: {
    session_id: "sess-1",
    role: "teacher" as const,
    dataset_id: "dataset-1",
    dataset_family_id: "family-1",
    student_id: null,
    allowed_class_ids: ["class-1"],
    bootstrap: false,
    capabilities: ["chat_teacher"],
  },
};

function makeProbe(useSession: () => { session: unknown; signOut: () => void }) {
  return function Probe() {
    const { session, signOut } = useSession();
    const role = (session as { scope?: { role?: string } } | null)?.scope?.role ?? "none";
    return (
      <div>
        <span data-testid="role">{role}</span>
        <button type="button" onClick={signOut}>退出身份</button>
      </div>
    );
  };
}

async function renderRestored() {
  const sessionModule = await import("./session");
  const Probe = makeProbe(sessionModule.useSession as never);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <sessionModule.SessionProvider>
          <Probe />
        </sessionModule.SessionProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return sessionModule;
}

describe("会话持久化", () => {
  beforeEach(() => {
    vi.resetModules();
    window.sessionStorage.clear();
  });

  it("刷新后从同一标签页会话恢复身份、数据集与学生范围", async () => {
    window.sessionStorage.setItem("zz-session-v1", JSON.stringify(STORED));
    const module = await renderRestored();
    expect(screen.getByTestId("role")).toHaveTextContent("teacher");
    expect(module.LAST_PATH_STORAGE_KEY).toBe("zz-last-path-v1");
  });

  it("没有保存的会话时保持未登录，不伪造身份", async () => {
    await renderRestored();
    expect(screen.getByTestId("role")).toHaveTextContent("none");
  });

  it("切换/退出身份时清理保存的会话与页面上下文", async () => {
    window.sessionStorage.setItem("zz-session-v1", JSON.stringify(STORED));
    window.sessionStorage.setItem("zz-last-path-v1", "/teacher/chat?conversationId=conv-1");
    await renderRestored();
    await userEvent.click(screen.getByRole("button", { name: "退出身份" }));
    expect(window.sessionStorage.getItem("zz-session-v1")).toBeNull();
    expect(window.sessionStorage.getItem("zz-last-path-v1")).toBeNull();
    expect(screen.getByTestId("role")).toHaveTextContent("none");
  });
});
