import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import type {
  ProducerApi,
  ProducerApiError,
  ProducerProjectDetail,
} from "../api/client";
import { AppShell } from "../components/AppShell";
import { AsyncNotice } from "../components/AsyncNotice";
import { t } from "../i18n";
import { ProjectOverview } from "./ProjectOverview";

interface EmptyProjectProps {
  api: ProducerApi;
}

function projectError(error: unknown): string {
  const status =
    error && typeof error === "object" && "status" in error
      ? (error as ProducerApiError).status
      : undefined;
  if (status === 403 || status === 404) {
    return t("This project is unavailable or you do not have access.");
  }
  return t("The project could not be loaded. Try again.");
}

export function EmptyProject({ api }: EmptyProjectProps) {
  const { slug } = useParams<{ slug: string }>();
  const [project, setProject] = useState<ProducerProjectDetail>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!slug) {
      setError(t("The project address is invalid."));
      return;
    }
    const controller = new AbortController();
    api
      .getProject(slug, controller.signal)
      .then(setProject)
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(projectError(reason));
        }
      });
    return () => controller.abort();
  }, [api, slug]);

  return (
    <AppShell>
      {error ? <AsyncNotice kind="error">{error}</AsyncNotice> : null}
      {!project && !error ? (
        <AsyncNotice>{t("Loading project…")}</AsyncNotice>
      ) : null}
      {project?.contents?.length ? (
        <ProjectOverview project={project} api={api} />
      ) : project ? (
        <article className="empty-project" aria-labelledby="project-heading">
          <nav aria-label={t("Breadcrumb")}>
            <Link to="/projects">{t("Projects")}</Link>
            <span aria-hidden="true"> / </span>
            <span>{project.name}</span>
          </nav>
          <p className="eyebrow">{t("Project created")}</p>
          <h1 id="project-heading">{project.name}</h1>
          <p className="lead">
            {t(
              "The empty project is ready. Upload a loc-kit to prepare the first localization.",
            )}
          </p>
          <div className="button-row">
            <Link className="primary-button" to={`/projects/${project.slug}/localize`}>
              {t("Make localization")}
            </Link>
            <a className="secondary-link" href={project.advanced_url}>
              {t("Advanced Weblate")}
            </a>
          </div>
        </article>
      ) : null}
    </AppShell>
  );
}
