import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { StudentRoster } from "./StudentRoster";
import type { RosterStudent } from "../../api/types";

const students: RosterStudent[] = [
  { student_id: "S01", class_id: "DC01", grade: "七年级", observed_event_count: 3, graded_event_count: 2 },
  { student_id: "S02", class_id: "DC01", grade: "七年级", observed_event_count: 0, graded_event_count: 0 },
];

describe("StudentRoster", () => {
  it("无记录学生显示“无记录”，不显示 0 分", () => {
    render(
      <MemoryRouter>
        <StudentRoster
          students={students}
          total={2}
          page={1}
          pageSize={20}
          attention={[]}
          onPage={vi.fn()}
          onOpenStudent={vi.fn()}
          search=""
          onSearch={vi.fn()}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("无记录")).toBeInTheDocument();
    expect(screen.queryByText("0 分")).toBeNull();
    expect(screen.getByText("名册共 2 人（分母含无记录学生）· 第 1 / 1 页")).toBeInTheDocument();
  });
});
