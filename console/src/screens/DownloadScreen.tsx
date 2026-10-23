import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import type {
  ProducerApi,
  ProducerApiError,
  ProducerExportSummary,
  ProducerProjectDetail,
} from "../api/client";
import { AppShell } from "../components/AppShell";
import { AsyncNotice } from "../components/AsyncNotice";
import { t } from "../i18n";

interface DownloadScreenProps {
  api: ProducerApi;
}

type ExportFormat = "xlsx" | "csv" | "json";

function saveDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function DownloadScreen({ api }: DownloadScreenProps) {
  const { slug } = useParams<{ slug: string }>();
  const [project, setProject] = useState<ProducerProjectDetail>();
  const [selected, setSelected] = useState<string[]>([]);
  const [summary, setSummary] = useState<ProducerExportSummary>();
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const summaryGeneration = useRef(0);

  const refresh = useCallback(
    async (content: string[], signal?: AbortSignal) => {
      const generation = ++summaryGeneration.current;
      if (!slug || !content.length) {
        setSummary(undefined);
        return;
      }
      const loaded = await api.getExportSummary(slug, content, signal);
      if (generation === summaryGeneration.current) setSummary(loaded);
    },
    [api, slug],
  );

  useEffect(() => {
    if (!slug) {
      setError(t("The project address is invalid."));
      return;
    }
    const controller = new AbortController();
    void api
      .getProject(slug, controller.signal)
      .then(async (loaded) => {
        const content = loaded.contents
          .filter((item) => item.exportable)
          .map((item) => item.id);
        setProject(loaded);
        setSelected(content);
        await refresh(content, controller.signal);
        setError(undefined);
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(t("Export information could not be loaded."));
        }
      });
    return () => {
      summaryGeneration.current += 1;
      controller.abort();
    };
  }, [api, refresh, slug]);

  const toggle = async (id: string, checked: boolean) => {
    const next = checked
      ? [...selected, id]
      : selected.filter((item) => item !== id);
    setSelected(next);
    setBusy(true);
    try {
      await refresh(next);
      setError(undefined);
    } catch {
      setError(t("Export information could not be refreshed."));
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    if (!slug || !summary || !selected.length) return;
    setBusy(true);
    try {
      const result = await api.downloadExport(
        slug,
        selected,
        format,
        summary.revision,
      );
      saveDownload(result.blob, result.filename);
      setError(undefined);
    } catch (reason: unknown) {
      const code =
        reason && typeof reason === "object" && "code" in reason
          ? (reason as ProducerApiError).code
          : undefined;
      if (code === "stale-revision") {
        setError(
          t(
            "The export changed. Review the refreshed totals and download again.",
          ),
        );
        try {
          await refresh(selected);
        } catch {
          setError(t("Export information could not be refreshed."));
        }
      } else {
        setError(t("The export could not be downloaded."));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <nav aria-label={t("Breadcrumb")} className="breadcrumbs">
        <Link to="/projects">{t("Projects")}</Link>
        <span aria-hidden="true"> / </span>
        <Link to={`/projects/${slug}`}>{project?.name ?? slug}</Link>
        <span aria-hidden="true"> / </span>
        <span>{t("Download results")}</span>
      </nav>
      <article aria-labelledby="download-heading" className="download-page">
        <div className="page-heading">
          <div>
            <p className="eyebrow">{t("Source-first export")}</p>
            <h1 id="download-heading">{t("Download localization results")}</h1>
          </div>
        </div>
        {error ? <AsyncNotice kind="error">{error}</AsyncNotice> : null}
        {!project && !error ? (
          <AsyncNotice>{t("Loading export information…")}</AsyncNotice>
        ) : null}
        {project ? (
          <div className="download-grid">
            <section
              className="overview-card"
              aria-labelledby="download-content-heading"
            >
              <h2 id="download-content-heading">{t("Content")}</h2>
              {project.contents
                .filter((item) => item.exportable)
                .map((item) => (
                  <label className="content-choice" key={item.id}>
                    <input
                      checked={selected.includes(item.id)}
                      disabled={busy}
                      type="checkbox"
                      onChange={(event) =>
                        void toggle(item.id, event.currentTarget.checked)
                      }
                    />
                    <span>{item.label}</span>
                  </label>
                ))}
            </section>
            <section
              className="overview-card"
              aria-labelledby="download-summary-heading"
            >
              <h2 id="download-summary-heading">{t("Export summary")}</h2>
              {summary ? (
                <>
                  <dl className="gate-metrics">
                    <div>
                      <dt>{t("Keys")}</dt>
                      <dd>{summary.keys}</dd>
                    </div>
                    <div>
                      <dt>{t("Empty targets")}</dt>
                      <dd>{summary.empty}</dd>
                    </div>
                    <div>
                      <dt>{t("Removed keys")}</dt>
                      <dd>{summary.removed}</dd>
                    </div>
                  </dl>
                  <table className="export-language-table">
                    <thead>
                      <tr>
                        <th>{t("Language")}</th>
                        <th>{t("Translated")}</th>
                        <th>{t("Empty")}</th>
                        <th>{t("Blocked")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(summary.per_language).map(
                        ([language, counts]) => (
                          <tr key={language}>
                            <th scope="row">{language}</th>
                            <td>{counts.translated}</td>
                            <td>{counts.empty}</td>
                            <td>{counts.blocked}</td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                  <label className="field-label">
                    <span>{t("Format")}</span>
                    <select
                      value={format}
                      onChange={(event) =>
                        setFormat(event.currentTarget.value as ExportFormat)
                      }
                    >
                      {summary.formats.map((item) => (
                        <option key={item} value={item}>
                          {item.toUpperCase()}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    className="primary-button"
                    disabled={busy || !selected.length}
                    onClick={download}
                    type="button"
                  >
                    {busy ? t("Preparing download…") : t("Download")}
                  </button>
                </>
              ) : (
                <p>
                  {selected.length
                    ? t("Refreshing exact export totals…")
                    : t("Choose at least one loc-kit component.")}
                </p>
              )}
            </section>
          </div>
        ) : null}
      </article>
    </AppShell>
  );
}
