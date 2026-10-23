import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ConsoleConfig } from "../config";
import { ProducerApi } from "./client";

const config: ConsoleConfig = {
  consoleRoot: "/weblate/console/",
  apiRoot: "/weblate/api/producer/",
  loginUrl: "/weblate/accounts/login/",
  csrfToken: "csrf-test",
};

const projectA = {
  slug: "pirate-ships",
  name: "Pirate Ships",
  advanced_url: "https://weblate.test/projects/pirate-ships/",
};
const projectB = {
  slug: "star-squad",
  name: "Star Squad",
  advanced_url: "https://weblate.test/projects/star-squad/",
};

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ProducerApi", () => {
  const fetchMock = vi.fn();
  const locationAssign = vi.fn();
  let api: ProducerApi;

  beforeEach(() => {
    fetchMock.mockReset();
    locationAssign.mockReset();
    api = new ProducerApi(config, {
      fetch: fetchMock as unknown as typeof fetch,
      locationAssign,
    });
  });

  it("reads the full catalog with GET and passes the abort signal", async () => {
    const controller = new AbortController();
    const results = [{code:"pt_BR",name:"Portuguese (Brazil)"}];
    fetchMock.mockResolvedValueOnce(jsonResponse({results}));
    expect(await api.getLanguages(controller.signal)).toEqual(results);
    expect(fetchMock).toHaveBeenCalledWith("/weblate/api/producer/languages/",expect.objectContaining({signal:controller.signal,credentials:"same-origin"}));
    expect(fetchMock.mock.calls[0][1].method).toBe("GET");
  });

  it("lists every allowed project across same-origin pages", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          count: 2,
          next: "https://weblate.test/weblate/api/producer/projects/?page=2",
          previous: null,
          results: [projectA],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          count: 2,
          next: null,
          previous: null,
          results: [projectB],
        }),
      );

    await expect(api.listProjects()).resolves.toEqual([projectA, projectB]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects a cross-origin pagination link", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        count: 2,
        next: "https://evil.example/api/producer/projects/?page=2",
        previous: null,
        results: [projectA],
      }),
    );

    await expect(api.listProjects()).rejects.toMatchObject({
      code: "unsafe-next",
    });
  });

  it("creates a project with JSON and the meta CSRF token", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(projectA, 201));

    await api.createProject("Pirate Ships");

    expect(fetchMock).toHaveBeenCalledWith(
      "/weblate/api/producer/projects/",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
        headers: expect.objectContaining({ "X-CSRFToken": "csrf-test" }),
        body: JSON.stringify({ name: "Pirate Ships" }),
      }),
    );
  });

  it("redirects 401 to native login with the current deep link", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401));

    await expect(api.getMe()).rejects.toMatchObject({
      code: "session-expired",
    });
    expect(locationAssign).toHaveBeenCalledWith(
      "/weblate/accounts/login/?next=%2Fweblate%2Fconsole%2Fprojects%2Fpirate-ships",
    );
  });

  it("surfaces 403 and field validation without reporting success", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          type: "client_error",
          errors: [
            { code: "permission_denied", detail: "Forbidden", attr: null },
          ],
        },
        403,
      ),
    );

    await expect(api.createProject("Forbidden")).rejects.toMatchObject({
      status: 403,
    });
  });

  it("uploads a loc-kit as multipart and keeps the server draft authoritative", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ token: "draft-1", revision: "a".repeat(64) }, 201),
    );
    const file = new File(["Key,en\nstart,Start\n"], "kit.csv", {
      type: "text/csv",
    });

    await api.createUpload("pirate-ships", [file], "quick");

    const [, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(fetchMock.mock.calls[0][0]).toBe(
      "/weblate/api/producer/projects/pirate-ships/uploads/",
    );
    expect(request.method).toBe("POST");
    expect(request.headers).toEqual({ "X-CSRFToken": "csrf-test" });
    expect(request.body).toBeInstanceOf(FormData);
    expect((request.body as FormData).get("ui_mode")).toBe("quick");
    expect((request.body as FormData).get("declared_kind")).toBe("string");
  });

  it("loads and revision-checks the producer-owned project profile", async () => {
    const profile = {
      revision: "a".repeat(64),
      answers: {},
      summary: "",
      missing_required: ["register_ui"],
      profile_state: "missing",
      engine: "openrouter",
      judge_context_changed: false,
    };
    const answers = {
      register_ui: "concise",
      register_dialogue: "natural",
      profanity: "preserve",
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse(profile))
      .mockResolvedValueOnce(jsonResponse({ ...profile, answers }));

    await api.getProfile("pirate-ships");
    await api.patchProfile("pirate-ships", profile.revision, answers);

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/weblate/api/producer/projects/pirate-ships/profile/",
    );
    expect(fetchMock.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ revision: profile.revision, answers }),
      }),
    );
  });

  it("patches one revisioned draft and exposes stale-revision", async () => {
    const body = {
      revision: "a".repeat(64),
      ui_mode: "detailed" as const,
      current_step: "select-content" as const,
      answers: {
        selected_files: ["kit.xlsx"],
        selected_sheets: [{ file: "kit.xlsx", sheet: "Units" }],
        source_language: "en",
        included_target_languages: ["ru"],
        column_roles: [],
        duplicate_policy: "separate-components" as const,
        profile_answers: {},
      },
    };
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Reload", code: "stale-revision" }, 409),
    );

    await expect(api.patchUpload("draft-1", body)).rejects.toMatchObject({
      status: 409,
      code: "stale-revision",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/weblate/api/producer/uploads/draft-1/",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    );
  });

  it("starts localization and resumes the persisted attempt", async () => {
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ id: "run-1", status: "queued" }, 201),
      )
      .mockResolvedValueOnce(jsonResponse({ id: "run-1", status: "queued" }));

    await api.startLocalization("pirate-ships", {
      upload_id: "draft-1",
      revision: "b".repeat(64),
    });
    await api.resumeRun("run-1", 2);

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/weblate/api/producer/projects/pirate-ships/localization/",
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/weblate/api/producer/runs/run-1/resume/",
    );
    expect(fetchMock.mock.calls[1][1]).toEqual(
      expect.objectContaining({ body: JSON.stringify({ attempt: 2 }) }),
    );
  });

  it("loads a bounded quarantine page for gate review", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        count: 1,
        next: null,
        previous: null,
        results: [{ coordinate: "Units!2", reason_code: "missing_source" }],
      }),
    );

    await api.getUploadQuarantine("draft-1");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/weblate/api/producer/uploads/draft-1/quarantine/?page_size=200&page=1",
    );
  });

  it("loads a revisioned export summary and downloads the selected content", async () => {
    const revision = "e".repeat(64);
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({
          revision,
          keys: 2,
          empty: 0,
          removed: 0,
          per_language: {},
          formats: ["xlsx", "csv", "json"],
        }),
      )
      .mockResolvedValueOnce(
        new Response(new Blob(["xlsx"]), {
          headers: {
            "Content-Disposition":
              'attachment; filename="pirate-ships-loc-kit.xlsx"',
          },
        }),
      );

    await api.getExportSummary("pirate-ships", ["units", "dialogue"]);
    const download = await api.downloadExport(
      "pirate-ships",
      ["units", "dialogue"],
      "xlsx",
      revision,
    );

    expect(fetchMock.mock.calls[0][0]).toContain(
      "projects/pirate-ships/export/summary/?content=units%2Cdialogue",
    );
    expect(fetchMock.mock.calls[1][0]).toContain("format=xlsx");
    expect(fetchMock.mock.calls[1][0]).toContain(`revision=${revision}`);
    expect(download.filename).toBe("pirate-ships-loc-kit.xlsx");
  });

  it("surfaces a stale export revision", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ code: "stale-revision", detail: "Refresh" }, 409),
    );

    await expect(
      api.downloadExport("pirate-ships", ["units"], "json", "0".repeat(64)),
    ).rejects.toMatchObject({ code: "stale-revision", status: 409 });
  });

  it("redirects an expired export session through the shared login flow", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Session expired" }, 401),
    );

    await expect(
      api.downloadExport("pirate-ships", ["units"], "json", "e".repeat(64)),
    ).rejects.toMatchObject({ code: "session-expired", status: 401 });
    expect(locationAssign).toHaveBeenCalledWith(
      expect.stringContaining("/weblate/accounts/login/?next="),
    );
  });
});

