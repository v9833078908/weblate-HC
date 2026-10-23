import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QualityArtifactReport } from "./QualityArtifactReport";

const issue = {
  file: "kit.xlsx", sheet: "Strings", row: 4, column: 3, key: "two", unit_id: 12,
  source: "Play", target: "Spiel", category: "unverified", severity: "not_verified",
  unverified: true, blocking: false, reason: "current_revision_not_verified",
  judge_1: "parsed", judge_2: "missing", action: "complete_verification", group_id: "",
};
const report = { schema_version: 1, run_id: "run-1", export_id: "export:run-1",
  filename: "run-run-1-quality-report.xlsx", provenance: {scope_revision: "frozen-version"},
  summary: { status: "completed_with_exceptions", processed: 2, automatically_corrected: 1,
    blocking_cells: 0, unverified_cells: 1, highlighted_cells: 1, issue_count: 1,
    repeat_groups: 0, judge_verified_units: 1, quarantined_transport: 0, project_issues: [] },
  issues: [issue], total: 1, page: 1, page_size: 50 };

afterEach(() => vi.unstubAllGlobals());

describe("QualityArtifactReport", () => {
  it("explains a disabled repeat check instead of suggesting a translation edit", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ok: true, json: async () => ({...report,
      issues: [{...issue, category: "blocking", blocking: true, unverified: false,
        reason: "translation_propagation_disabled", action: "check_component_repeat_settings"}]})}));
    render(<QualityArtifactReport runId="run-1" />);
    expect(await screen.findByText("Контроль повторов выключен для компонента")).toBeVisible();
    expect(screen.getByText("Проверить настройки контроля повторов компонента")).toBeVisible();
  });
  it("shows saved report, yellow reason, both judge states and downloads without interpreting HTML", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...report,
      issues: [{...issue, source: "<img src=x onerror=alert(1)>"}] }) }));
    const { container } = render(<QualityArtifactReport runId="run-1" />);
    expect(await screen.findByText("run-run-1-quality-report.xlsx")).toBeVisible();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("Ответ разобран (не означает одобрение)")).toBeVisible();
    expect(screen.getByText("Нет актуального ответа")).toBeVisible();
    expect(screen.getByText("Strings!C4")).toBeVisible();
    expect(screen.getByRole("link", {name: "Скачать отчёт"})).toHaveAttribute("href",
      "/api/producer/runs/run-1/quality-report/?format=xlsx");
    expect(screen.getByRole("link", {name: "Скачать итоговый файл"})).toHaveAttribute("href",
      "/api/producer/runs/run-1/export/?format=xlsx");
    expect(screen.getByRole("link", {name: "Скачать всё"})).toHaveAttribute("href",
      "/api/producer/runs/run-1/result-bundle/");
  });

  it("paginates 500 issues and resets page on filter change", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string) => {
      const url = new URL(input, "http://localhost");
      const page = Number(url.searchParams.get("page"));
      const category = url.searchParams.get("category");
      return { ok: true, json: async () => ({...report, page, total: category === "blocking" ? 0 : 500,
        issues: category === "blocking" ? [] : Array.from({length: 50}, (_, i) =>
          ({...issue, key: `key-${(page - 1) * 50 + i}`, row: (page - 1) * 50 + i + 2})) }) };
    }));
    render(<QualityArtifactReport runId="run-1" />);
    expect(await screen.findByText("key-0")).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name: "Следующая страница"}));
    expect(await screen.findByText("key-50")).toBeVisible();
    expect(screen.queryByText("key-0")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Тип замечаний"), {target: {value: "blocking"}});
    expect(await screen.findByText("По выбранному фильтру замечаний нет")).toBeVisible();
    expect(screen.getByRole("button", {name: "Предыдущая страница"})).toBeDisabled();
  });

  it("distinguishes a clean report from missing data and supports retry", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ok: false}).mockResolvedValueOnce({ok: true,
      json: async () => ({...report, issues: [], total: 0,
        summary: {...report.summary, issue_count: 0, unverified_cells: 0, status: "completed"}})});
    vi.stubGlobal("fetch", fetcher);
    render(<QualityArtifactReport runId="run-1" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить сохранённый отчёт");
    expect(screen.queryByText("Остаточных замечаний нет")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "Повторить загрузку"}));
    expect(await screen.findByText("Остаточных замечаний нет")).toBeVisible();
  });

  it("does not show a previous run report after navigation", async () => {
    let resolveFirst: (value: unknown) => void = () => {};
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => url.includes("run-1")
      ? new Promise(resolve => {resolveFirst = resolve;})
      : Promise.resolve({ok: true, json: async () => ({...report, run_id: "run-2", filename: "second.xlsx"})})));
    const view = render(<QualityArtifactReport runId="run-1" />);
    view.rerender(<QualityArtifactReport runId="run-2" />);
    expect(await screen.findByText("second.xlsx")).toBeVisible();
    resolveFirst({ok: true, json: async () => report});
    await waitFor(() => expect(screen.queryByText("run-run-1-quality-report.xlsx")).not.toBeInTheDocument());
  });
});
