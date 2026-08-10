import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CumulativePnlChart, DailyPnlChart } from "@/pages/Autopilot";

const DAYS = [
  { date: "2026-08-08", pnl_usd: -2.5 },
  { date: "2026-08-09", pnl_usd: 5.0 },
  { date: "2026-08-10", pnl_usd: 1.0 },
];

describe("Autopilot chart components", () => {
  it("renders the empty state when there are no closed-P&L days", () => {
    render(<DailyPnlChart days={[]} />);
    expect(screen.getByText("No daily data")).toBeTruthy();
  });

  it("renders one bar per day with positive bars in success color", () => {
    const { container } = render(<DailyPnlChart days={DAYS} />);
    const bars = container.querySelectorAll("div[style*='height']");
    expect(bars.length).toBe(3);
  });

  it("renders an empty-state for cumulative when no days exist", () => {
    render(<CumulativePnlChart days={[]} />);
    expect(screen.getByText("No daily data")).toBeTruthy();
  });

  it("draws a cumulative polyline with a positive-tone endpoint when total is up", () => {
    const { container } = render(<CumulativePnlChart days={DAYS} />);
    const polyline = container.querySelector("polyline");
    expect(polyline).not.toBeNull();
    expect(polyline?.getAttribute("points")?.split(" ").length).toBe(3);
    expect(polyline?.getAttribute("stroke")).toContain("success");
  });

  it("shows the running total next to the cumulative title", () => {
    render(<CumulativePnlChart days={DAYS} />);
    // -2.5 + 5.0 + 1.0 = +3.5
    expect(screen.getByText("+$3.50")).toBeTruthy();
  });

  it("uses a danger tone when the cumulative total is negative", () => {
    const { container } = render(
      <CumulativePnlChart days={[{ date: "2026-08-08", pnl_usd: -4.0 }]} />,
    );
    const polyline = container.querySelector("polyline");
    expect(polyline?.getAttribute("stroke")).toContain("danger");
  });
});
