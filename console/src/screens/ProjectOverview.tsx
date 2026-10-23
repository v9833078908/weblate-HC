import { Link } from "react-router-dom";

import type { ProducerProjectDetail, ProducerApi } from "../api/client";
import { ProjectConnection } from "../components/ProjectConnection";
import { ProjectHistory } from "../components/ProjectHistory";
import { t } from "../i18n";

interface ProjectOverviewProps {
  project: ProducerProjectDetail;
  api?: ProducerApi;
}

export function ProjectOverview({ project, api }: ProjectOverviewProps) {
  return (
    <article aria-labelledby="project-heading" className="project-overview">
      <nav aria-label={t("Breadcrumb")} className="breadcrumbs">
        <Link to="/projects">{t("Projects")}</Link>
        <span aria-hidden="true"> / </span>
        <span>{project.name}</span>
      </nav>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{t("Project overview")}</p>
          <h1 id="project-heading">{project.name}</h1>
        </div>
        <div className="button-row">
          {project.contents.some((content) => content.exportable) ? (
            <Link
              className="secondary-link"
              to={`/projects/${project.slug}/download`}
            >
              {t("Download results")}
            </Link>
          ) : null}
          <Link
            className="primary-button"
            to={`/projects/${project.slug}/localize`}
          >
            {t("Make localization")}
          </Link>
        </div>
      </div>
      <div className="overview-grid">
        <section className="overview-card" aria-labelledby="contents-heading">
          <h2 id="contents-heading">{t("Content")}</h2>
          {project.contents.length ? (
            <ul className="content-list">
              {project.contents.map((content) => (
                <li key={content.id}>
                  <span>
                    <strong>{content.label}</strong>
                    <small>
                      {content.type === "store"
                        ? t("Store content")
                        : content.type === "glossary"
                          ? t("Glossary")
                          : t("Loc-kit")}
                    </small>
                  </span>
                  <a href={content.advanced_url}>{t("Open in Weblate")}</a>
                </li>
              ))}
            </ul>
          ) : (
            <p>{t("No localization content yet.")}</p>
          )}
        </section>
        <section className="overview-card" aria-labelledby="languages-heading">
          <h2 id="languages-heading">{t("Languages")}</h2>
          {project.languages.length ? (
            <ul className="language-list">
              {project.languages.map((language) => (
                <li key={language.code}>
                  <span>{language.name}</span>
                  <span>
                    {language.strings} · {t(language.status)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p>
              {t("Languages will appear after the first confirmed loc-kit.")}
            </p>
          )}
        </section>
      </div>
      <a className="secondary-link" href={project.advanced_url}>
        {t("Advanced Weblate")}
      </a>
      {api ? <ProjectConnection api={api} slug={project.slug} /> : null}
      {api ? <ProjectHistory api={api} slug={project.slug} /> : null}
    </article>
  );
}
