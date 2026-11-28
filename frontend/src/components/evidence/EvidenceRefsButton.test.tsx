import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { resolveEvidence } from "../../api/service";
import type { EvidenceDetail, EvidenceRef } from "../../api/types";
import { EvidenceRefsButton } from "./EvidenceRefsButton";

vi.mock("../../api/service", () => ({ resolveEvidence: vi.fn() }));

const mockedResolve = vi.mocked(resolveEvidence);

const REFS: EvidenceRef[] = [
  { kind: "record", id: "DR0296", run_id: "R-1", analysis_version: "A-1", material_id: "M-1", attempt_id: "AT-1" },
  { kind: "record", id: "DR0100", run_id: "R-1", analysis_version: "A-1", material_id: "M-1", attempt_id: "AT-2" },
  { kind: "record", id: "DR0096", run_id: "R-1", analysis_version: "A-1", material_id: "M-2", attempt_id: "AT-3" },
  { kind: "record", id: "DR0095", run_id: "R-1", analysis_version: "A-1", material_id: "M-2", attempt_id: "AT-4" },
  { kind: "record", id: "DR0055", run_id: "R-1", analysis_version: "A-1", material_id: "M-3", attempt_id: "AT-5" },
];

beforeEach(() => {
  mockedResolve.mockReset();
  mockedResolve.mockImplementation(async (ref) => ({
    ref,
    title: `作答记录 ${ref.id}`,
    detail_type: "record",
    detail: { student_id: "DC01", question: { material_id: "M-1" } },
    summary: `摘要 ${ref.id}`,
    related_refs: [],
    limitations: [],
    visibility: "full",
  }) satisfies EvidenceDetail);
});

describe("EvidenceRefsButton", () => {
  it("多条依据时显示计数，列表列出全部引用并可按原引用打开详情", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <EvidenceRefsButton
        refs={REFS}
        onOpen={onOpen}
        context={{ class_id: "DC01", student_id: null }}
        materialNames={{ "M-1": "一元一次方程练习" }}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看依据（5）" }));
    expect(screen.getByRole("dialog", { name: "依据列表" })).toBeInTheDocument();
    REFS.forEach((ref) => expect(screen.getByText(ref.id)).toBeInTheDocument());
    expect(await screen.findAllByText(/^摘要 DR/)).toHaveLength(5);
    expect(await screen.findAllByText("学生 DC01 · 材料 一元一次方程练习")).toHaveLength(2);
    expect(await screen.findAllByText("学生 DC01 · 材料 M-2")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "查看第 3 条依据（DR0096）详情" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith(REFS[2]);
    expect(onOpen.mock.calls[0][0]).toBe(REFS[2]);
    expect(screen.queryByRole("dialog", { name: "依据列表" })).toBeNull();
  });

  it("点击列表行同样打开详情", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<EvidenceRefsButton refs={REFS.slice(0, 2)} onOpen={onOpen} />);

    await user.click(screen.getByRole("button", { name: "查看依据（2）" }));
    await user.click(screen.getByText("DR0100"));
    expect(onOpen).toHaveBeenCalledWith(REFS[1]);
  });

  it("单条依据保持直接打开详情，不出现列表", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<EvidenceRefsButton refs={[REFS[0]]} onOpen={onOpen} />);

    await user.click(screen.getByRole("button", { name: "查看依据" }));
    expect(onOpen).toHaveBeenCalledWith(REFS[0]);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockedResolve).not.toHaveBeenCalled();
  });

  it("没有引用时不显示按钮", () => {
    const { container } = render(<EvidenceRefsButton refs={[]} onOpen={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
