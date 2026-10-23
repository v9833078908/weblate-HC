import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QualityDetails } from "./QualityDetails";

afterEach(() => vi.unstubAllGlobals());

describe("QualityDetails", () => {
  it("shows row reasons, both judge responses and a Weblate application", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      status: "completed_with_exceptions", counts: { processed: 2 },
      weblate_project_url: "https://l10n.herocraft.com/projects/test/",
      units: [{ unit_id: 1, outcome: "clean", application_id: 501,
        seat_results: { "1": "parsed", "2": "parsed" }, final_reasons: [] },
        { unit_id: 2, outcome: "residual_issues", application_id: null,
          seat_results: { "1": "unparsed", "2": "missing" }, final_reasons: ["glossary"] }],
      groups: [{ group_id: "repeat:1", outcome: "needs_review" }],
    }) }));
    render(<QualityDetails runId="run-1" />);
    fireEvent.click(screen.getByRole("button", { name: "Показать результаты по строкам" }));
    expect(await screen.findByText("Проблема глоссария")).toBeVisible();
    expect(screen.getAllByText("Ответ получен")).toHaveLength(2);
    expect(screen.getByText("Ответ не разобран")).toBeVisible();
    expect(screen.getByRole("link", { name: "501" })).toHaveAttribute("href", "https://l10n.herocraft.com/projects/test/");
    expect(screen.getByText("Требует проверки")).toBeVisible();
  });
});
