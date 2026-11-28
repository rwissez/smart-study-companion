import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getModelSettings: vi.fn(),
}));

vi.mock("../../api/service", () => ({
  getModelSettings: (...args: unknown[]) => mocks.getModelSettings(...args),
}));

vi.mock("../../api/client", () => ({
  toErrorMessage: vi.fn(() => "模型配置状态读取失败"),
}));

vi.mock("../../app/session", () => ({
  useSession: () => ({ scopeKey: { mode: "api", epoch: 1, datasetId: "ds-1" } }),
}));

vi.mock("../../lib/queryKeys", () => ({
  queryKeys: { modelSettings: () => ["model", "settings"] as const },
}));

import { ModelStatus } from "./ModelStatus";

describe("ModelStatus 模型配置入口", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function renderMark(onConfigure = vi.fn()) {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ModelStatus onConfigure={onConfigure} />
      </QueryClientProvider>,
    );
    return onConfigure;
  }

  it("已配置时仍可点击打开模型设置（可随时更换模型、地址或密钥）", async () => {
    mocks.getModelSettings.mockResolvedValue({
      configured: true,
      provider: "openai_compatible",
      url: "http://127.0.0.1:11434/v1",
      model_id: "test",
      has_api_key: true,
      allow_external_model_input: false,
    });
    const onConfigure = renderMark();
    const mark = await screen.findByRole("button", { name: "模型已配置，打开模型设置" });
    await userEvent.setup().click(mark);
    expect(onConfigure).toHaveBeenCalledTimes(1);
  });

  it("未配置时点击打开模型设置与外发授权", async () => {
    mocks.getModelSettings.mockResolvedValue({
      configured: false,
      provider: null,
      url: null,
      model_id: null,
      has_api_key: false,
      allow_external_model_input: false,
    });
    const onConfigure = renderMark();
    const mark = await screen.findByRole("button", { name: "模型未配置，打开模型设置与外发授权" });
    await userEvent.setup().click(mark);
    expect(onConfigure).toHaveBeenCalledTimes(1);
  });
});
