import type { ConsoleConfig } from "../config";
import type { components } from "./generated";

export interface RemoteUnit { key: string; source: string; targets: Record<string, string>; note?: string; }
export interface RemoteComponent { slug: string; name: string; source: string; glossary: boolean; complete: boolean; languages: string[]; units: RemoteUnit[]; }
export interface RemoteSnapshot { slug: string; name: string; languages: string[]; components: Record<string, RemoteComponent>; profile?: Record<string, unknown>; issues: Array<{component: string; detail: string}>; }
export interface ProjectConnection { remote_slug: string; read_only: boolean; snapshot: RemoteSnapshot; component_map: Record<string, string>; observed: number; }
export interface ConnectionPreview { id: string; snapshot_hash: string; snapshot: RemoteSnapshot; }
export interface ChangePlan { glossary_preview?: {source:string; targets:string[]; components:Array<{sheet:string;units:Array<{key:string;values:Record<string,string>}>}>} | null; id: string; revision: string; read_only: boolean; confirmed?: boolean; profile_preview?: {engine:string;before:Record<string,unknown>;after:Record<string,unknown>}; components: Array<{ source?:string; remote_id?:string; create_component?:boolean; sheet: string; new: Array<{key: string; source?:string; explanation?:string}>; unchanged: string[]; changed: string[]; retained: string[]; translate: Record<string, string[]>; seed?: Record<string, Record<string,string>>; old_empty: Record<string, string[]>; }>; blocked: Array<{sheet: string; reason: string}>; }

export type ProducerMe = components["schemas"]["ProducerMe"];
export type ProducerLanguage = components["schemas"]["ProducerLanguage"];
export type ProducerProjectCreate =
  components["schemas"]["ProducerProjectCreate"];
export type ProducerProjectSummary =
  components["schemas"]["ProducerProjectSummary"];
export type ProducerProjectDetail =
  components["schemas"]["ProducerProjectDetail"];
export type ProducerUploadAnalysis =
  components["schemas"]["ProducerUploadAnalysis"];
export type ProducerUploadAnswers =
  components["schemas"]["ProducerUploadAnswers"];
export type ProducerUploadPage = components["schemas"]["ProducerUploadPage"];
export type ProducerRun = components["schemas"]["ProducerRun"];
export type ProducerExportSummary =
  components["schemas"]["ProducerExportSummary"];
export type ProducerLocalizationRequest =
  components["schemas"]["ProducerLocalizationRequest"];
export type ProducerProfile = Omit<components["schemas"]["ProducerProfile"], "answers" | "generated"> & {
  answers: ProducerProfileAnswers;
  generated?: { persona: string; style: string; language_instructions: Record<string, string> } | null;
  field_origins?: Record<string, string>;
  needs_review_fields?: string[];
};
export type ContextResolution = components["schemas"]["ProducerContextResolution"];
export type ProfilePreparationRequest = components["schemas"]["ProfilePrepareRequest"];
export type ProfilePreparation = components["schemas"]["ProducerProfilePreparation"];
export type ProducerProfileAnswers =
  Partial<components["schemas"]["ProducerProfileAnswers"]>;
export type ProducerUiMode = components["schemas"]["UiModeEnum"];
export type ProducerUploadStep = components["schemas"]["CurrentStepEnum"];
export interface ProducerUploadPatch {
  revision: string;
  answers?: ProducerUploadAnswers;
  ui_mode: ProducerUiMode;
  current_step: ProducerUploadStep;
}
type PaginatedProducerProjects =
  components["schemas"]["PaginatedProducerProjectSummaryList"];

interface ProducerApiDependencies {
  fetch: typeof fetch;
  locationAssign: (url: string) => void;
}

export class ProducerApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly payload: unknown,
  ) {
    super(message);
    this.name = "ProducerApiError";
  }
}

function defaultDependencies(): ProducerApiDependencies {
  return {
    fetch: window.fetch.bind(window),
    locationAssign: window.location.assign.bind(window.location),
  };
}

function errorDetail(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") {
    return fallback;
  }
  if ("detail" in payload && typeof payload.detail === "string") {
    return payload.detail;
  }
  if ("errors" in payload && Array.isArray(payload.errors)) {
    const first = payload.errors[0];
    if (
      first &&
      typeof first === "object" &&
      "detail" in first &&
      typeof first.detail === "string"
    ) {
      return first.detail;
    }
  }
  return fallback;
}

function errorCode(payload: unknown, fallback: string): string {
  if (
    payload &&
    typeof payload === "object" &&
    "code" in payload &&
    typeof payload.code === "string"
  ) {
    return payload.code;
  }
  return fallback;
}

export class ProducerApi {
  readonly #config: ConsoleConfig;
  readonly #dependencies: ProducerApiDependencies;

  async getLanguages(signal?: AbortSignal): Promise<ProducerLanguage[]> {
    const value = await this.#request<components["schemas"]["ProducerLanguageSearch"]>("languages/", {signal});
    return value.results;
  }

  getIntegrationPolicy(): Promise<{external_writes: boolean}> {
    return this.#request("integration-policy/");
  }

  async discoverRemoteProjects(name: string, match: "exact" | "contains" = "exact", signal?: AbortSignal): Promise<Array<{name: string; slug: string}>> {
    const value = await this.#request<{results: Array<{name: string; slug: string}>}>(`remote-projects/?name=${encodeURIComponent(name)}${match === "contains" ? "&match=contains" : ""}`, {signal});
    return value.results;
  }

  getConnection(slug: string): Promise<ProjectConnection | null> {
    return this.#request(`projects/${encodeURIComponent(slug)}/connection/`);
  }

  previewConnection(slug: string, remoteSlug: string): Promise<ConnectionPreview> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/connection/preview/`, {remote_slug: remoteSlug});
  }

  confirmConnection(slug: string, preview: ConnectionPreview): Promise<ProjectConnection> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/connection/confirm/`, {candidate_id: preview.id, expected_snapshot_hash: preview.snapshot_hash});
  }

  refreshConnection(slug: string): Promise<ProjectConnection> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/connection/refresh/`, {});
  }

  previewChanges(slug: string, uploadId: string, revision: string, mapping: Record<string,string>, includeOldEmpty: boolean, glossary?: {token:string;revision:string}): Promise<ChangePlan> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/changes/preview/`, {upload_id: uploadId, revision, mapping, include_old_empty: includeOldEmpty, ...(glossary ? {glossary_upload_id:glossary.token,glossary_revision:glossary.revision}: {})});
  }

  confirmChanges(slug: string, plan: ChangePlan): Promise<ChangePlan> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/changes/confirm/`, {plan_id: plan.id, revision: plan.revision});
  }

  private localPost<T>(path: string, body: unknown): Promise<T> {
    return this.#request(path, {method: "POST", body: JSON.stringify(body), headers: {"Content-Type": "application/json", "X-CSRFToken": this.#config.csrfToken}});
  }

  restoreUpload(slug: string, signal?: AbortSignal): Promise<ProducerUploadAnalysis> {
    return this.#request(`projects/${encodeURIComponent(slug)}/uploads/restore/`, {
      method: "POST", signal,
      headers: {"X-CSRFToken": this.#config.csrfToken},
    });
  }

  resolveContext(slug: string, body: {revision:string;upload_token:string;query?:string;title_id?:string}): Promise<ContextResolution> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/context/resolve/`, body);
  }

  prepareProfile(slug: string, body: ProfilePreparationRequest): Promise<ProfilePreparation> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/profile/prepare/`, body);
  }

  getProfilePreparation(slug: string, jobId: string): Promise<ProfilePreparation> {
    return this.#request<ProfilePreparation>(`projects/${encodeURIComponent(slug)}/profile/preparations/${encodeURIComponent(jobId)}/`);
  }

  commitProfile(slug: string, body: ProfilePreparationRequest & {job_id?:string;generated:NonNullable<ProducerProfile["generated"]>;edited_fields:string[]}): Promise<ProducerProfile> {
    return this.localPost(`projects/${encodeURIComponent(slug)}/profile/commit/`, body);
  }

  constructor(
    config: ConsoleConfig,
    dependencies: ProducerApiDependencies = defaultDependencies(),
  ) {
    this.#config = config;
    this.#dependencies = dependencies;
  }

  async getMe(signal?: AbortSignal): Promise<ProducerMe> {
    return this.#request<ProducerMe>("me/", { signal });
  }

  async listProjects(signal?: AbortSignal): Promise<ProducerProjectSummary[]> {
    const projects: ProducerProjectSummary[] = [];
    let next: string | null | undefined = this.#endpoint("projects/");
    let continuation = false;

    while (next) {
      let requestUrl: string = next;
      if (continuation) {
        const continuationUrl: URL = new URL(
          requestUrl,
          window.location.origin,
        );
        if (continuationUrl.origin !== window.location.origin) {
          throw new ProducerApiError(
            "The server returned a cross-origin pagination link.",
            0,
            "unsafe-next",
            { next },
          );
        }
        requestUrl = continuationUrl.href;
      }
      const page: PaginatedProducerProjects =
        await this.#request<PaginatedProducerProjects>(requestUrl, {
          signal,
          absolute: true,
        });
      projects.push(...page.results);
      next = page.next;
      continuation = true;
    }

    return projects;
  }

  async getProject(
    slug: string,
    signal?: AbortSignal,
  ): Promise<ProducerProjectDetail> {
    return this.#request<ProducerProjectDetail>(
      `projects/${encodeURIComponent(slug)}/`,
      { signal },
    );
  }

  async createProject(
    name: string,
    signal?: AbortSignal,
  ): Promise<ProducerProjectSummary> {
    const body: ProducerProjectCreate = { name };
    return this.#request<ProducerProjectSummary>("projects/", {
      signal,
      method: "POST",
      body: JSON.stringify(body),
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": this.#config.csrfToken,
      },
    });
  }

  async getProfile(
    slug: string,
    signal?: AbortSignal,
  ): Promise<ProducerProfile> {
    return this.#request<ProducerProfile>(
      `projects/${encodeURIComponent(slug)}/profile/`,
      { signal },
    );
  }

  async patchProfile(
    slug: string,
    revision: string,
    answers: ProducerProfileAnswers,
    signal?: AbortSignal,
  ): Promise<ProducerProfile> {
    return this.#request<ProducerProfile>(
      `projects/${encodeURIComponent(slug)}/profile/`,
      {
        signal,
        method: "PATCH",
        body: JSON.stringify({ revision, answers }),
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": this.#config.csrfToken,
        },
      },
    );
  }

  async getActiveUpload(
    slug: string,
    signal?: AbortSignal,
  ): Promise<ProducerUploadAnalysis> {
    return this.#request<ProducerUploadAnalysis>(
      `projects/${encodeURIComponent(slug)}/uploads/active/`,
      { signal },
    );
  }

  async createUpload(
    slug: string,
    files: File[],
    uiMode: ProducerUiMode,
    declaredKind: "string" | "glossary" = "string",
    signal?: AbortSignal,
  ): Promise<ProducerUploadAnalysis> {
    const body = new FormData();
    for (const file of files) {
      body.append("files", file, file.name);
    }
    body.append("declared_kind", declaredKind);
    body.append("ui_mode", uiMode);
    return this.#request<ProducerUploadAnalysis>(
      `projects/${encodeURIComponent(slug)}/uploads/`,
      {
        signal,
        method: "POST",
        body,
        headers: { "X-CSRFToken": this.#config.csrfToken },
      },
    );
  }

  async getUpload(
    token: string,
    signal?: AbortSignal,
  ): Promise<ProducerUploadAnalysis> {
    return this.#request<ProducerUploadAnalysis>(
      `uploads/${encodeURIComponent(token)}/`,
      { signal },
    );
  }

  async patchUpload(
    token: string,
    body: ProducerUploadPatch,
    signal?: AbortSignal,
  ): Promise<ProducerUploadAnalysis> {
    return this.#request<ProducerUploadAnalysis>(
      `uploads/${encodeURIComponent(token)}/`,
      {
        signal,
        method: "PATCH",
        body: JSON.stringify(body),
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": this.#config.csrfToken,
        },
      },
    );
  }

  async getUploadQuarantine(
    token: string,
    signal?: AbortSignal,
    page = 1,
  ): Promise<ProducerUploadPage> {
    return this.#request<ProducerUploadPage>(
      `uploads/${encodeURIComponent(token)}/quarantine/?page_size=200&page=${page}`,
      { signal },
    );
  }

  async startLocalization(
    slug: string,
    body: ProducerLocalizationRequest & {change_plan_id?: string; change_plan_revision?: string},
    signal?: AbortSignal,
  ): Promise<ProducerRun> {
    return this.#request<ProducerRun>(
      `projects/${encodeURIComponent(slug)}/localization/`,
      {
        signal,
        method: "POST",
        body: JSON.stringify(body),
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": this.#config.csrfToken,
        },
      },
    );
  }

  async getRun(id: string, signal?: AbortSignal): Promise<ProducerRun> {
    return this.#request<ProducerRun>(`runs/${encodeURIComponent(id)}/`, {
      signal,
    });
  }

  getHistory(slug: string): Promise<{results: ProducerRun[]}> {
    return this.#request(`projects/${encodeURIComponent(slug)}/history/`);
  }

  async cancelRun(id: string): Promise<ProducerRun> {
    return this.#request(`runs/${encodeURIComponent(id)}/cancel/`, {
      method: "POST", headers: { "X-CSRFToken": this.#config.csrfToken },
    });
  }

  async resumeRun(
    id: string,
    attempt: number,
    signal?: AbortSignal,
  ): Promise<ProducerRun> {
    return this.#request<ProducerRun>(
      `runs/${encodeURIComponent(id)}/resume/`,
      {
        signal,
        method: "POST",
        body: JSON.stringify({ attempt }),
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": this.#config.csrfToken,
        },
      },
    );
  }

  async getExportSummary(
    slug: string,
    content: string[],
    signal?: AbortSignal,
  ): Promise<ProducerExportSummary> {
    const query = new URLSearchParams();
    if (content.length) query.set("content", content.join(","));
    return this.#request<ProducerExportSummary>(
      `projects/${encodeURIComponent(slug)}/export/summary/?${query}`,
      { signal },
    );
  }

  async downloadExport(
    slug: string,
    content: string[],
    format: "xlsx" | "csv" | "json",
    revision: string,
    signal?: AbortSignal,
  ): Promise<{ blob: Blob; filename: string }> {
    const query = new URLSearchParams({ format, revision });
    if (content.length) query.set("content", content.join(","));
    const response = await this.#dependencies.fetch(
      this.#endpoint(`projects/${encodeURIComponent(slug)}/export/?${query}`),
      { method: "GET", credentials: "same-origin", signal },
    );
    if (!response.ok) {
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        payload = undefined;
      }
      this.#throwIfSessionExpired(response.status, payload);
      throw new ProducerApiError(
        errorDetail(payload, `Producer export failed (${response.status}).`),
        response.status,
        errorCode(payload, "request-failed"),
        payload,
      );
    }
    const disposition = response.headers.get("Content-Disposition") ?? "";
    const filename =
      /filename="([^"]+)"/.exec(disposition)?.[1] ??
      `${slug}-loc-kit.${format}`;
    return { blob: await response.blob(), filename };
  }

  async searchBdhc(query: string): Promise<{ results: Array<{ name: string; ref: string }> }> {
    return this.#request(`bdhc/titles/?q=${encodeURIComponent(query)}`);
  }

  async previewBdhc(slug: string, titleId: string, revision: string): Promise<{ preview_id: string; diff: Array<{ field: string; before: string; after: string }>; warnings: string[]; locale_rules_diff: Array<{ language: string; before: Record<string, unknown>; after: Record<string, unknown> }>; provenance: { title_id: string; schema_version: string | null; context_hash: string | null } }> {
    return this.#request(`projects/${encodeURIComponent(slug)}/bdhc-preview/`, {
      method: "POST", body: JSON.stringify({ title_id: titleId, revision }),
      headers: { "Content-Type": "application/json", "X-CSRFToken": this.#config.csrfToken },
    });
  }

  async applyBdhc(slug: string, previewId: string, revision: string): Promise<ProducerProfile> {
    return this.#request(`projects/${encodeURIComponent(slug)}/bdhc-apply/`, {
      method: "POST", body: JSON.stringify({ preview_id: previewId, revision }),
      headers: { "Content-Type": "application/json", "X-CSRFToken": this.#config.csrfToken },
    });
  }

  async setManualProfile(slug: string, revision: string, generated: NonNullable<ProducerProfile["generated"]>): Promise<ProducerProfile> {
    return this.#request(`projects/${encodeURIComponent(slug)}/profile/manual/`, {
      method: "PATCH", body: JSON.stringify({ revision, generated }),
      headers: { "Content-Type": "application/json", "X-CSRFToken": this.#config.csrfToken },
    });
  }

  #endpoint(path: string): string {
    return `${this.#config.apiRoot.replace(/\/?$/, "/")}${path}`;
  }

  #throwIfSessionExpired(status: number, payload: unknown): void {
    if (status !== 401) return;
    const next = `${window.location.pathname}${window.location.search}`;
    const separator = this.#config.loginUrl.includes("?") ? "&" : "?";
    this.#dependencies.locationAssign(
      `${this.#config.loginUrl}${separator}next=${encodeURIComponent(next)}`,
    );
    throw new ProducerApiError(
      errorDetail(payload, "Your session has expired."),
      status,
      "session-expired",
      payload,
    );
  }

  async #request<T>(
    path: string,
    options: RequestInit & { absolute?: boolean } = {},
  ): Promise<T> {
    const { absolute = false, ...requestOptions } = options;
    const response = await this.#dependencies.fetch(
      absolute ? path : this.#endpoint(path),
      {
        method: "GET",
        credentials: "same-origin",
        ...requestOptions,
      },
    );
    const payload: unknown = await response.json();

    this.#throwIfSessionExpired(response.status, payload);
    if (!response.ok) {
      throw new ProducerApiError(
        errorDetail(
          payload,
          `Producer API request failed (${response.status}).`,
        ),
        response.status,
        errorCode(payload, "request-failed"),
        payload,
      );
    }

    return payload as T;
  }
}
