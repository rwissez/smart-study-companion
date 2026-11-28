import { describe, expect, it } from "vitest";

import { tableLabel } from "./labels";

describe("tableLabel", () => {
  it("把课程标准与教材分开显示", () => {
    expect(tableLabel("standard")).toBe("课程标准");
    expect(tableLabel("textbook")).toBe("教材");
  });

  it("未知角色回退为数据表", () => {
    expect(tableLabel("something-else")).toBe("数据表");
  });
});
