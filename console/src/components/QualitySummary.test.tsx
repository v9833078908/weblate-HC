import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { QualitySummary } from "./QualitySummary";

describe("QualitySummary", () => {
  it("explains missing server support without claiming that repair is running", () => {
    render(<QualitySummary runId="run" value={{ status: "waiting_for_configuration", phase: "preflight" }} />);
    expect(screen.getByText("Ожидание настройки API полного цикла Weblate. Обработка ещё не запущена.")).toBeVisible();
    expect(screen.queryByText("Ошибки проверяются и исправляются автоматически.")).not.toBeInTheDocument();
  });
});
