import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import NetworkNotice from "./NetworkNotice";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("NetworkNotice", () => {
  it("shows offline feedback and only requests refresh after an explicit click", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
    const refresh = vi.fn();
    window.addEventListener("focus", refresh);
    const view = render(<NetworkNotice/>);
    expect(screen.queryByRole("status")).toBeNull();
    act(() => window.dispatchEvent(new Event("offline")));
    expect(screen.getByRole("status").textContent).toContain("网络已断开");
    act(() => window.dispatchEvent(new Event("online")));
    expect(refresh).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    expect(refresh).toHaveBeenCalledOnce();
    expect(screen.queryByRole("status")).toBeNull();
    view.unmount();
    window.removeEventListener("focus", refresh);
  });

  it("detects an initially offline browser", () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    render(<NetworkNotice/>);
    expect(screen.getByRole("status").textContent).toContain("网络已断开");
  });
});
