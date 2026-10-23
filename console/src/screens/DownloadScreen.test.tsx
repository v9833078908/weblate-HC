import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type AxeResults, run as axe } from "axe-core";
import { RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ProducerApi,
  ProducerExportSummary,
  ProducerProjectDetail,
} from "../api/client";
import { createConsoleMemoryRouter } from "../router";

const project: ProducerProjectDetail = {
  slug: "pirate-ships",
  name: "Pirate Ships",
  profile_state: "configured",
  advanced_url: "/weblate/projects/pirate-ships/",
  contents: [
    {
      id: "units",
      type: "loc_kit",
      label: "Units",
      keys: 2,
      uploadable: true,
      exportable: true,
      advanced_url: "/weblate/projects/pirate-ships/units/",
    },
  ],
  languages: [],
};

const summary: ProducerExportSummary = {
  revision: "e".repeat(64),
  keys: 2,
  empty: 1,
  removed: 0,
  per_language: {
    en: { strings: 2, translated: 2, empty: 0, blocked: 0 },
    ru: { strings: 2, translated: 1, empty: 1, blocked: 1 },
  },
  formats: ["xlsx", "csv", "json"],
};

function apiMock(overrides: Partial<ProducerApi> = {}): ProducerApi {
  return {
    getProject: vi.fn().mockResolvedValue(project),
    getExportSummary: vi.fn().mockResolvedValue(summary),
    downloadExport: vi.fn().mockResolvedValue({
      blob: new Blob(["workbook"]),
      filename: "pirate-ships-loc-kit.xlsx",
    }),
    ...overrides,
  } as unknown as ProducerApi;
}

function renderDownload(api: ProducerApi) {
  const router = createConsoleMemoryRouter(api, [
    "/projects/pirate-ships/download",
  ]);
  return render(<RouterProvider router={router} />);
}

describe("DownloadScreen", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue("blob:export"),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      () => undefined,
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows exact totals and downloads the selected source-first format", async () => {
    const user = userEvent.setup();
    const api = apiMock();
    renderDownload(api);

    expect(
      await screen.findByRole("heading", {
        name: "Скачать результаты локализации",
      }),
    ).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Units" })).toBeChecked();
    expect(
      screen.getByText("Удалённые ключи").nextElementSibling,
    ).toHaveTextContent("0");
    expect(screen.getByRole("row", { name: /ru 1 1 1/ })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Скачать" }));

    expect(api.downloadExport).toHaveBeenCalledWith(
      "pirate-ships",
      ["units"],
      "xlsx",
      summary.revision,
    );
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  it("refreshes exact totals after a stale revision", async () => {
    const user = userEvent.setup();
    const stale = Object.assign(new Error("stale"), {
      code: "stale-revision",
      status: 409,
    });
    const api = apiMock({ downloadExport: vi.fn().mockRejectedValue(stale) });
    renderDownload(api);
    await screen.findByRole("heading", {
      name: "Скачать результаты локализации",
    });

    await user.click(screen.getByRole("button", { name: "Скачать" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Данные экспорта изменились",
    );
    await waitFor(() => expect(api.getExportSummary).toHaveBeenCalledTimes(2));
  });

  it("does not let an older summary overwrite a newer selection", async () => {
    const user = userEvent.setup();
    let resolveSummary!: (value: ProducerExportSummary) => void;
    const pendingSummary = new Promise<ProducerExportSummary>((resolve) => {
      resolveSummary = resolve;
    });
    const api = apiMock({
      getExportSummary: vi.fn().mockReturnValueOnce(pendingSummary),
    });
    renderDownload(api);

    const checkbox = await screen.findByRole("checkbox", { name: "Units" });
    await user.click(checkbox);
    expect(checkbox).not.toBeChecked();

    await act(async () => resolveSummary(summary));
    expect(screen.queryByText("Удалённые ключи")).not.toBeInTheDocument();
  });

  it("has no serious automated accessibility violations", async () => {
    const { container } = renderDownload(apiMock());
    await screen.findByRole("heading", {
      name: "Скачать результаты локализации",
    });
    const results: AxeResults = await axe(container);
    expect(
      results.violations.filter(({ impact }) =>
        ["serious", "critical"].includes(impact ?? ""),
      ),
    ).toEqual([]);
  });
});
