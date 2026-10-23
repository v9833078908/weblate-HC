import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type AxeResults, run as axe } from "axe-core";
import { RouterProvider } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProducerApi, ProducerRun } from "../api/client";
import { createConsoleMemoryRouter } from "../router";

const runningStages = {
  execution: { attempt: 1 },
  components: [{ status: "completed" }],
  languages: { status: "running", translation_ids: [1, 2] },
};

const running: ProducerRun = {
  id: "11111111-1111-1111-1111-111111111111",
  status: "running",
  requested_mode: "localize",
  created: "2026-09-29T00:00:00Z",
  started: "2026-09-29T00:00:01Z",
  finished: null,
  summary: {},
  failure: "",
  warnings: [],
  stages: runningStages,
  liveness: "running",
  outcome: null,
  resume_allowed: false,
  resume_reason: null,
  preparation_phase: "complete",
  coverage: {},
};

function apiMock(run: ProducerRun): ProducerApi {
  return {
    getRun: vi.fn().mockResolvedValue(run),
    resumeRun: vi.fn().mockResolvedValue({
      ...run,
      status: "queued",
      resume_allowed: false,
      stages: { ...runningStages, execution: { attempt: 2 } },
    }),
  } as unknown as ProducerApi;
}

function renderRun(api: ProducerApi) {
  return render(
    <RouterProvider
      router={createConsoleMemoryRouter(api, [
        `/projects/pirate-ships/runs/${running.id}`,
      ])}
    />,
  );
}

afterEach(() => vi.useRealTimers());

describe("RunCard", () => {
  it("shows persisted MT reconciliation progress without claiming a new request", async () => {
    const run = { ...running, stages: { ...runningStages, mt_observation: {
      translated: 550, total: 1662, scopes: { "kit-1:de": {
        state: "reconciling", filled: 550, total: 1662,
        last_progress: 1792550400, next_poll_at: 1792550430,
      } },
    } } };
    renderRun(apiMock(run));
    expect(await screen.findByText("550 / 1662")).toBeVisible();
    expect(screen.getByText("Сверка результата после тайм-аута; новый перевод не отправляется.")).toBeVisible();
    expect(screen.getByText(/Следующая проверка:/)).toBeVisible();
  });
  it("polls an active run every five seconds", async () => {
    vi.useFakeTimers();
    const api = apiMock(running);
    renderRun(api);
    await act(async () => Promise.resolve());
    expect(api.getRun).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(5000));

    expect(api.getRun).toHaveBeenCalledTimes(2);
    expect(screen.getByText("1/1")).toBeVisible();
  });

  it("explains no-update state and resumes the fenced attempt", async () => {
    const user = userEvent.setup();
    const stalled = {
      ...running,
      status: "no-update",
      liveness: "no-update",
      resume_allowed: true,
      resume_reason: "attempt-failed",
      stages: { ...runningStages, execution: { attempt: 3 } },
    };
    const api = apiMock(stalled);
    renderRun(api);

    expect(await screen.findByRole("alert")).toHaveTextContent("Нет обновлений");
    await user.click(screen.getByRole("button", { name: "Продолжить незавершённое задание" }));

    expect(api.resumeRun).toHaveBeenCalledWith(stalled.id, 3);
  });

  it("has no serious automated accessibility violations", async () => {
    const { container } = renderRun(apiMock(running));
    await screen.findByRole("heading", { name: "Первая локализация" });

    const results: AxeResults = await axe(container);
    expect(
      results.violations.filter(({ impact }) =>
        ["serious", "critical"].includes(impact ?? ""),
      ),
    ).toEqual([]);
  });
});
