import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type AxeResults, run as axe } from "axe-core";
import { RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { ProducerApi, ProducerApiError } from "../api/client";
import { createConsoleMemoryRouter } from "../router";

const projectA = {
  slug: "pirate-ships",
  name: "Pirate Ships",
  advanced_url: "/weblate/projects/pirate-ships/",
};
const projectB = {
  slug: "other-project",
  name: "Other Project",
  advanced_url: "/weblate/projects/other-project/",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function mockApi(overrides: Partial<ProducerApi> = {}): ProducerApi {
  return {
    getMe: vi.fn(),
    listProjects: vi.fn().mockResolvedValue([]),
    getProject: vi.fn(),
    createProject: vi.fn(),
    ...overrides,
  } as unknown as ProducerApi;
}

function renderProjects(api: ProducerApi) {
  const router = createConsoleMemoryRouter(api, ["/projects"]);
  render(<RouterProvider router={router} />);
  return router;
}

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    await screen.findByRole("button", { name: "Создать проект" }),
  );
}

async function submitName(
  user: ReturnType<typeof userEvent.setup>,
  name: string,
) {
  await openDialog(user);
  await user.type(
    screen.getByRole("textbox", { name: "Название проекта" }),
    name,
  );
  await user.click(screen.getByRole("button", { name: "Создать" }));
}

describe("ProjectsList", () => {
  it.each(["button", "enter"])("finds external projects with %s without creating or connecting them", async (trigger) => {
    const discover = vi.fn().mockResolvedValue([{name: "Pirate Ships", slug: "pirate-ships"}]);
    const api = mockApi({discoverRemoteProjects: discover});
    const user = userEvent.setup();
    renderProjects(api);
    const input = screen.getByRole("searchbox", {name: "Поиск проекта"});
    await user.type(input, "Pirate");
    expect(discover).not.toHaveBeenCalled();
    if (trigger === "button") await user.click(screen.getByRole("button", {name: "Найти"}));
    else await user.keyboard("{Enter}");
    expect(await screen.findByText("Pirate Ships — pirate-ships")).toBeVisible();
    expect(discover).toHaveBeenCalledWith("Pirate", "contains", expect.any(AbortSignal));
    expect(api.createProject).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", {name: "Подключить для чтения"}));
    expect(screen.getByRole("dialog")).toHaveTextContent("Данные существующего проекта останутся без изменений");
    expect(screen.getByRole("textbox", {name: "Название проекта"})).toHaveValue("Pirate Ships");
    expect(screen.getByRole("button", {name: "Подтвердить подключение"})).toBeEnabled();
    expect(api.createProject).not.toHaveBeenCalled();
  });

  it("ignores stale search results and reports external search errors", async () => {
    const old = deferred<Array<{name: string; slug: string}>>();
    const discover = vi.fn().mockReturnValueOnce(old.promise).mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    renderProjects(mockApi({discoverRemoteProjects: discover}));
    const input = screen.getByRole("searchbox", {name: "Поиск проекта"});
    await user.type(input, "Pirate");
    await user.click(screen.getByRole("button", {name: "Найти"}));
    await user.clear(input);
    await user.type(input, "Dead Shell");
    await act(async () => old.resolve([{name: "Pirate Ships",slug: "pirate-ships"}]));
    expect(screen.queryByText("Pirate Ships — pirate-ships")).not.toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось выполнить поиск в Weblate");
  });

  it("renders loading then all projects and filters by accessible name", async () => {
    const projects = deferred<(typeof projectA)[]>();
    const api = mockApi({ listProjects: vi.fn(() => projects.promise) });
    const user = userEvent.setup();
    renderProjects(api);

    expect(screen.getByRole("status")).toHaveTextContent("Загрузка");
    projects.resolve([projectA, projectB]);
    expect(
      await screen.findByRole("link", { name: "Pirate Ships" }),
    ).toBeVisible();
    await user.type(
      screen.getByRole("searchbox", { name: "Поиск проекта" }),
      "other",
    );
    expect(
      screen.queryByRole("link", { name: "Pirate Ships" }),
    ).not.toBeInTheDocument();
  });

  it("opens the create dialog, labels and focuses the name field", async () => {
    const user = userEvent.setup();
    renderProjects(mockApi());

    await openDialog(user);

    expect(screen.getByRole("dialog")).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "Название проекта" }),
    ).toHaveFocus();
  });

  it("submits once, disables duplicate submit, and navigates after refetch", async () => {
    const created = deferred<typeof projectA>();
    const api = mockApi({
      createProject: vi.fn(() => created.promise),
      getProject: vi.fn().mockResolvedValue(projectA),
    });
    const user = userEvent.setup();
    const router = renderProjects(api);

    await submitName(user, "Pirate Ships");
    const pending = screen.getByRole("button", { name: "Создание проекта…" });
    expect(pending).toBeDisabled();
    await user.click(pending);
    expect(api.createProject).toHaveBeenCalledTimes(1);

    await act(async () => created.resolve(projectA));
    await waitFor(() => {
      expect(api.getProject).toHaveBeenCalledWith(
        "pirate-ships",
        expect.any(AbortSignal),
      );
      expect(router.state.location.pathname).toBe("/projects/pirate-ships");
    });
  });

  it.each([
    [400, "Проверьте"],
    [403, "Недостаточно"],
    [401, "Сессия"],
  ])(
    "announces validation, permission, and session errors without navigation (%s)",
    async (status, expected) => {
      const error = Object.assign(new Error("Rejected"), {
        status,
        code: "request-failed",
        payload: {},
      }) as ProducerApiError;
      const api = mockApi({ createProject: vi.fn().mockRejectedValue(error) });
      const user = userEvent.setup();
      const router = renderProjects(api);

      await submitName(user, "Forbidden");

      expect(await screen.findByRole("alert")).toHaveTextContent(expected);
      expect(router.state.location.pathname).toBe("/projects");
    },
  );

  it("directs ambiguous workspace creation to Advanced Weblate", async () => {
    const error = Object.assign(new Error("Rejected"), {
      status: 400,
      code: "request-failed",
      payload: {
        errors: [
          {
            code: "invalid",
            detail: "Specify a workspace when multiple workspaces can be used.",
            attr: "workspace",
          },
        ],
      },
    }) as ProducerApiError;
    const api = mockApi({ createProject: vi.fn().mockRejectedValue(error) });
    const user = userEvent.setup();

    renderProjects(api);
    await submitName(user, "Workspace project");

    expect(await screen.findByRole("alert")).toHaveTextContent("Weblate");
  });

  it("returns focus to the create button when the dialog closes", async () => {
    const user = userEvent.setup();
    renderProjects(mockApi());
    await openDialog(user);

    await user.click(screen.getByRole("button", { name: "Отмена" }));

    expect(
      screen.getByRole("button", { name: "Создать проект" }),
    ).toHaveFocus();
  });

  it("keeps semantic controls and status text at a narrow viewport", async () => {
    Object.defineProperty(window, "innerWidth", {
      value: 320,
      configurable: true,
    });
    Object.defineProperty(window, "innerHeight", {
      value: 640,
      configurable: true,
    });
    renderProjects(mockApi());

    expect(
      await screen.findByRole("button", { name: "Создать проект" }),
    ).toBeVisible();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
  });

  it("has no serious automated accessibility violations", async () => {
    const api = mockApi({
      listProjects: vi.fn().mockResolvedValue([projectA]),
    });
    const { container } = render(
      <RouterProvider router={createConsoleMemoryRouter(api, ["/projects"])} />,
    );
    await screen.findByRole("link", { name: "Pirate Ships" });

    const results: AxeResults = await axe(container);
    expect(
      results.violations.filter(({ impact }) =>
        ["serious", "critical"].includes(impact ?? ""),
      ),
    ).toEqual([]);
  });
});
