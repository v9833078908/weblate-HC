"use client";

import {
  CheckCircle2,
  Circle,
  Loader2,
  Mail,
  Radio,
  RefreshCw,
  XCircle,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/console/AppShell";
import {
  ConsoleCard,
  Money,
  StatusBadge,
} from "@/components/console/primitives";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";

function StageIcon({ status }) {
  if (status === "done")
    return <CheckCircle2 className="h-5 w-5 text-success" aria-hidden />;
  if (status === "running")
    return (
      <Loader2 className="h-5 w-5 animate-spin text-info-strong" aria-hidden />
    );
  if (status === "error")
    return <XCircle className="h-5 w-5 text-destructive" aria-hidden />;
  return <Circle className="h-5 w-5 text-muted-foreground" aria-hidden />;
}

export function RunCardScreen({ project, runId, navigate }) {
  const slug = project.slug;
  const [run, setRun] = React.useState(null);
  const [resuming, setResuming] = React.useState(false);

  const load = React.useCallback(() => {
    api.getRun(slug, runId).then(setRun);
  }, [slug, runId]);
  React.useEffect(() => {
    load();
  }, [load]);

  // poll every 5s while the run is live (mock)
  React.useEffect(() => {
    if (!run || !["running", "queued", "stalled"].includes(run.status)) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [run, load]);

  const resume = async () => {
    setResuming(true);
    const r = await api.resumeRun(slug, runId);
    setResuming(false);
    setRun(r);
    toast("Незавершённое отправлено на повторную обработку");
  };

  if (!run) {
    return (
      <AppShell
        project={project}
        active="overview"
        navigate={navigate}
        breadcrumb={[
          { label: "Проекты", path: "/" },
          { label: project.name, path: `/projects/${slug}` },
          { label: `Прогон #${runId}` },
        ]}
      >
        <Skeleton className="mb-4 h-8 w-80 rounded" />
        <Skeleton className="h-64 rounded" />
      </AppShell>
    );
  }

  const total = (run.cost || []).reduce(
    (s, c) => s + Number.parseFloat(c.usd || 0),
    0,
  );
  const isLive = ["running", "queued"].includes(run.status);

  return (
    <AppShell
      project={project}
      active="overview"
      navigate={navigate}
      breadcrumb={[
        { label: "Проекты", path: "/" },
        { label: project.name, path: `/projects/${slug}` },
        { label: `Прогон #${run.id}` },
      ]}
    >
      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-heading-page text-foreground">{run.title}</h1>
            <StatusBadge status={run.status} />
          </div>
          <div className="mt-1 text-body-sm text-muted-foreground">
            Начат · идёт {run.elapsed}
            {run.finished ? " · завершён" : ""}
          </div>
          {isLive && (
            <div className="mt-2 inline-flex items-center gap-2 rounded-sm bg-info-surface px-2 py-1 text-body-sm text-info-strong">
              <Mail className="h-4 w-4" aria-hidden /> Отправим письмо, когда
              закончится
            </div>
          )}
        </div>
        {(run.status === "error" || run.status === "stalled") && (
          <Button
            className="h-9 rounded-sm"
            disabled={resuming}
            onClick={resume}
          >
            <RefreshCw className={cn("h-4 w-4", resuming && "animate-spin")} />{" "}
            Перезапустить незавершённое
          </Button>
        )}
      </div>

      {run.status === "error" && run.error && (
        <div className="mb-6 flex items-start gap-3 rounded border border-destructive bg-background p-4">
          <XCircle
            className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
            aria-hidden
          />
          <div>
            <div className="text-label-strong text-foreground">
              Прогон остановлен с ошибкой
            </div>
            <div className="text-body-sm text-muted-foreground">
              {run.error}
            </div>
          </div>
        </div>
      )}

      {run.status === "stalled" && (
        <div className="mb-6 flex items-start gap-3 rounded border border-input bg-background p-4">
          <Radio
            className="mt-0.5 h-5 w-5 shrink-0 animate-pulse text-muted-foreground"
            aria-hidden
          />
          <div>
            <div className="text-label-strong text-foreground">
              Нет обновлений
            </div>
            <div className="text-body-sm text-muted-foreground">
              Обработчик не отвечает 4 мин; незавершённые строки не потеряны.
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <ConsoleCard title="Этапы">
            <ol className="space-y-1">
              {run.stages.map((st, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: run stage list is static, never reordered
                <li key={i}>
                  <div className="flex items-center gap-3 py-2">
                    <StageIcon status={st.status} />
                    <span
                      className={cn(
                        "text-body-md",
                        st.status === "pending"
                          ? "text-muted-foreground"
                          : "text-foreground",
                      )}
                    >
                      {st.name}
                    </span>
                    {st.detail && (
                      <span className="ms-auto text-body-sm text-muted-foreground">
                        {st.detail}
                      </span>
                    )}
                  </div>
                  {st.sub && (
                    <ul className="ms-8 border-s border-border ps-4">
                      {st.sub.map((s, j) => (
                        <li
                          // biome-ignore lint/suspicious/noArrayIndexKey: sub-stage list is static, never reordered
                          key={j}
                          className="flex items-center gap-2 py-1 text-body-sm"
                        >
                          <StageIcon status={s.status} />
                          <span
                            className={
                              s.status === "pending"
                                ? "text-muted-foreground"
                                : "text-foreground"
                            }
                          >
                            {s.name}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          </ConsoleCard>

          {run.kind === "judge" && run.outcome && (
            <div className="mt-6 grid grid-cols-3 gap-6">
              <div className="rounded border border-border p-4">
                <div className="text-metric-lg tabular-nums text-success">
                  {run.outcome.passed}
                </div>
                <div className="mt-1 text-body-sm text-muted-foreground">
                  прошло
                </div>
              </div>
              <div className="rounded border border-border p-4">
                <div className="text-metric-lg tabular-nums text-info-strong">
                  {run.outcome.notes}
                </div>
                <div className="mt-1 text-body-sm text-muted-foreground">
                  замечания
                </div>
              </div>
              <div className="rounded border border-border p-4">
                <div className="text-metric-lg tabular-nums text-warning">
                  {run.outcome.decisions}
                </div>
                <div className="mt-1 text-body-sm text-muted-foreground">
                  требуют решения
                </div>
              </div>
            </div>
          )}
        </div>

        <div>
          <ConsoleCard title="Стоимость">
            {run.cost?.length ? (
              <table className="w-full text-body-sm">
                <thead>
                  <tr className="text-label-caps uppercase text-muted-foreground">
                    <th className="pb-2 text-start font-semibold">Язык</th>
                    <th className="pb-2 text-start font-semibold">Модель</th>
                    <th className="pb-2 text-end font-semibold">$</th>
                  </tr>
                </thead>
                <tbody>
                  {run.cost.map((c) => (
                    <tr key={c.language} className="border-t border-border">
                      <td className="py-1.5">{c.language}</td>
                      <td className="py-1.5 font-mono text-muted-foreground">
                        {c.model}
                      </td>
                      <td className="py-1.5 text-end tabular-nums">
                        <Money value={c.usd} />
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-border">
                    <td className="pt-2 text-label-strong" colSpan={2}>
                      Итого{isLive ? " (пока)" : ""}
                    </td>
                    <td className="pt-2 text-end text-label-strong tabular-nums">
                      <Money value={total.toFixed(2)} />
                    </td>
                  </tr>
                </tfoot>
              </table>
            ) : (
              <p className="text-body-sm text-muted-foreground">
                Нет данных о стоимости.
              </p>
            )}
          </ConsoleCard>
        </div>
      </div>
    </AppShell>
  );
}
