import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { ProducerApi, ProducerProjectSummary } from "../api/client";
import { AppShell } from "../components/AppShell";
import { AsyncNotice } from "../components/AsyncNotice";
import { CreateProjectDialog } from "../components/CreateProjectDialog";
import { t } from "../i18n";

interface ProjectsListProps {
  api: ProducerApi;
}

export function ProjectsList({ api }: ProjectsListProps) {
  const [projects, setProjects] = useState<ProducerProjectSummary[]>();
  const [query, setQuery] = useState("");
  const [loadError, setLoadError] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [initialProject, setInitialProject] = useState<{name: string; slug: string}>();
  const [remoteProjects, setRemoteProjects] = useState<Array<{name: string; slug: string}>>();
  const [searchPending, setSearchPending] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const searchController = useRef<AbortController | undefined>(undefined);
  const searchSequence = useRef(0);
  const dialogTrigger = useRef<HTMLButtonElement | null>(null);
  const createButtonRef = useRef<HTMLButtonElement>(null);
  const wasDialogOpen = useRef(false);
  const navigate = useNavigate();

  useEffect(() => {
    const controller = new AbortController();
    api
      .listProjects(controller.signal)
      .then(setProjects)
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setLoadError(true);
        }
      });
    return () => controller.abort();
  }, [api]);

  useEffect(() => {
    if (wasDialogOpen.current && !dialogOpen) {
      (dialogTrigger.current ?? createButtonRef.current)?.focus();
    }
    wasDialogOpen.current = dialogOpen;
  }, [dialogOpen]);

  useEffect(() => () => searchController.current?.abort(), []);

  const changeQuery = (value: string) => {
    searchSequence.current += 1;
    searchController.current?.abort();
    setQuery(value);
    setRemoteProjects(undefined);
    setSearchError(false);
    setSearchPending(false);
  };

  const search = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = query.trim();
    if (!name || searchPending) return;
    const sequence = ++searchSequence.current;
    const controller = new AbortController();
    searchController.current?.abort();
    searchController.current = controller;
    setSearchPending(true);
    setSearchError(false);
    setRemoteProjects(undefined);
    try {
      const matches = await api.discoverRemoteProjects(name, "contains", controller.signal);
      if (sequence === searchSequence.current) setRemoteProjects(matches);
    } catch {
      if (sequence === searchSequence.current && !controller.signal.aborted) setSearchError(true);
    } finally {
      if (sequence === searchSequence.current) setSearchPending(false);
    }
  };

  const filteredProjects = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return (projects ?? []).filter((project) =>
      project.name.toLocaleLowerCase().includes(normalized) || project.slug.toLocaleLowerCase().includes(normalized),
    );
  }, [projects, query]);

  const createProject = async (name: string, remoteSlug?: string) => {
    const controller = new AbortController();
    const created = await api.createProject(name, controller.signal);
    if (remoteSlug) {
      const preview = await api.previewConnection(created.slug, remoteSlug);
      await api.confirmConnection(created.slug, preview);
    }
    const persisted = await api.getProject(created.slug, controller.signal);
    navigate(`/projects/${persisted.slug}`);
  };

  return (
    <AppShell>
      <section aria-labelledby="projects-heading">
        <div className="page-heading">
          <div>
            <p className="eyebrow">HCGameLoc</p>
            <h1 id="projects-heading">{t("Projects")}</h1>
          </div>
          <button
            className="primary-button"
            onClick={(event) => {dialogTrigger.current = event.currentTarget; setInitialProject(undefined); setDialogOpen(true);}}
            ref={createButtonRef}
            type="button"
          >
            {t("Create project")}
          </button>
        </div>

        <form className="search-field" onSubmit={search} role="search">
          <label htmlFor="project-search">{t("Project search")}</label>
          <div className="project-search-controls">
          <input
            id="project-search"
            onChange={(event) => changeQuery(event.target.value)}
            type="search"
            value={query}
          />
          <button type="submit" disabled={searchPending || !query.trim()}>{t("Find")}</button>
          </div>
          <small>{t("Find projects in the wizard and external Weblate by name or slug.")}</small>
        </form>

        {searchPending ? <AsyncNotice>{t("Searching Weblate…")}</AsyncNotice> : null}
        {searchError ? <AsyncNotice kind="error">{t("Weblate search failed. Try again.")}</AsyncNotice> : null}

        {loadError ? (
          <AsyncNotice kind="error">
            {t("Projects could not be loaded. Try again.")}
          </AsyncNotice>
        ) : null}
        {!projects && !loadError ? (
          <AsyncNotice>{t("Loading projects…")}</AsyncNotice>
        ) : null}
        {projects && filteredProjects.length === 0 ? (
          <AsyncNotice>
            {query ? t("No matching projects in the wizard.") : t("No projects yet.")}
          </AsyncNotice>
        ) : null}
        {remoteProjects ? <section aria-labelledby="remote-projects-heading">
          <h2 id="remote-projects-heading">{t("Existing Weblate projects")}</h2>
          {remoteProjects.length === 0 ? <AsyncNotice>{t("No matching projects in Weblate.")}</AsyncNotice> : (
            <ul className="project-grid">{remoteProjects.map((project) => {
              const connected = projects?.find((local) => local.advanced_url === `https://l10n.herocraft.com/projects/${project.slug}/`);
              return <li key={project.slug}><div className="project-card">
                <strong>{project.name} — {project.slug}</strong>
                {connected ? <Link to={`/projects/${connected.slug}`}>{t("Open project")}</Link> : (
                  <button type="button" onClick={(event) => {dialogTrigger.current = event.currentTarget; setInitialProject(project); setDialogOpen(true);}}>{t("Connect for reading")}</button>
                )}
              </div></li>;
            })}</ul>
          )}
        </section> : null}
        {filteredProjects.length > 0 ? (
          <ul className="project-grid">
            {filteredProjects.map((project) => (
              <li key={project.slug}>
                <Link
                  aria-label={project.name}
                  className="project-card"
                  to={`/projects/${project.slug}`}
                >
                  <strong>{project.name}</strong>
                  <span>{t("Open project")}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <CreateProjectDialog
        initialProject={initialProject}
        onFind={typeof api.discoverRemoteProjects === "function" ? (name) => api.discoverRemoteProjects(name) : undefined}
        onClose={() => setDialogOpen(false)}
        onCreate={createProject}
        open={dialogOpen}
      />
    </AppShell>
  );
}
