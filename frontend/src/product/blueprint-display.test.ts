import { describe, expect, it } from "vitest";
import { blueprintDisplayText } from "./blueprint-display";

describe("legacy blueprint Chinese display", () => {
  it("translates known legacy text without inventing a project objective", () => {
    expect(blueprintDisplayText("blueprint")).toContain("尚未明确具体建设目标");
    expect(blueprintDisplayText("project_stakeholders")).toBe("项目相关人员");
    expect(blueprintDisplayText("Describe PLC systems, instrumentation, and control strategies for coal preparation processes.")).toBe("说明选煤工艺中的可编程逻辑控制系统、仪器仪表及控制策略。");
  });
  it("preserves Chinese text and source model identifiers", () => {
    expect(blueprintDisplayText("配置 S7-1200 控制器")).toBe("配置 S7-1200 控制器");
  });
});
