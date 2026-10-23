import { render, screen } from "@testing-library/react";
import { type AxeResults, run as axe } from "axe-core";
import { RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { ProducerApi, ProducerApiError } from "../api/client";
import { createConsoleMemoryRouter } from "../router";

const project = {
  slug: "pirate-ships",
  name: "Pirate Ships",
  advanced_url: "/weblate/projects/pirate-ships/",
};

function mockApi(getProject: ProducerApi["getProject"]): ProducerApi {
  return {
    getMe: vi.fn(),
    listProjects: vi.fn(),
    getProject,
    createProject: vi.fn(),
  } as unknown as ProducerApi;
}

function renderEmptyProject(api: ProducerApi) {
  const router = createConsoleMemoryRouter(api, ["/projects/pirate-ships"]);
  return render(<RouterProvider router={router} />);
}

describe("EmptyProject", () => {
  it("reloads an empty project by slug and uses its server Advanced URL", async () => {
    const api = mockApi(vi.fn().mockResolvedValue(project));
    renderEmptyProject(api);

    expect(screen.getByRole("status")).toHaveTextContent("Загрузка");
    expect(
      await screen.findByRole("link", { name: "Открыть в Weblate" }),
    ).toHaveAttribute("href", "/weblate/projects/pirate-ships/");
    expect(api.getProject).toHaveBeenCalledWith(
      "pirate-ships",
      expect.any(AbortSignal),
    );
  });

  it.each([404, 403])(
    "announces an unavailable project (%s)",
    async (status) => {
      const error = Object.assign(new Error("Unavailable"), {
        status,
        code: "request-failed",
        payload: {},
      }) as ProducerApiError;
      renderEmptyProject(mockApi(vi.fn().mockRejectedValue(error)));

      expect(await screen.findByRole("alert")).toBeVisible();
      expect(
        screen.queryByRole("link", { name: "Открыть в Weblate" }),
      ).not.toBeInTheDocument();
    },
  );

  it("has no serious automated accessibility violations", async () => {
    const { container } = renderEmptyProject(
      mockApi(vi.fn().mockResolvedValue(project)),
    );
    await screen.findByRole("link", { name: "Открыть в Weblate" });

    const results: AxeResults = await axe(container);
    expect(
      results.violations.filter(({ impact }) =>
        ["serious", "critical"].includes(impact ?? ""),
      ),
    ).toEqual([]);
  });
});
