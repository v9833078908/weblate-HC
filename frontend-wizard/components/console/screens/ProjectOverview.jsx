"use client";

import {
  AlertTriangle,
  ArrowRight,
  Download as DownloadIcon,
  ShieldCheck,
  Upload as UploadIcon,
} from "lucide-react";
import * as React from "react";
import { AppShell } from "@/components/console/AppShell";
import {
  ConsoleCard,
  MetricTile,
  Money,
  StatusBadge,
  WeblateLink,
} from "@/components/console/primitives";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import * as api from "@/src/api/client";

function fmt(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const months = [
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сент",
    "окт",
    "нояб",
    "дек",
  ];
  return `${d.getDate()} ${months[d.getMonth()]}, ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function JudgeLaunchSheet({ slug, open, onOpenChange, navigate }) {
  const [estimate, setEstimate] = React.useState(null);
  const [confirm, setConfirm] = React.useState(false);
  const [starting, setStarting] = React.useState(false);

  React.useEffect(() => {
    if (open) {
      setEstimate(null);
      api
        .estimateRun(slug, { content: "whole", languages: "all" })
        .then(setEstimate);
    }
  }, [open, slug]);

  const start = async () => {
    setStarting(true);
    const run = await api.createJudgeRun(slug, {
      content: "whole",
      languages: "all",
    });
    setStarting(false);
    setConfirm(false);
    onOpenChange(false);
    navigate(`/projects/${slug}/runs/${run.id}`);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full sm:max-w-[520px]">
        <SheetHeader>
          <SheetTitle>Проверить качество</SheetTitle>
          <SheetDescription>
            Оценка судьёй помогает найти искажения смысла. Она платная и идёт в
            фоне.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-4">
          <div className="rounded border border-border p-4">
            <div className="text-body-sm text-muted-foreground">
              Область проверки
            </div>
            <div className="mt-1 text-body-md text-foreground">
              Весь проект · все языки
            </div>
          </div>

          {estimate ? (
            <div className="rounded border border-border bg-muted p-4">
              <div className="text-label-caps uppercase text-muted-foreground">
                Оценка
              </div>
              <div className="mt-2 text-body-md text-foreground">
                ≈ {estimate.strings.toLocaleString("ru-RU")} строк · ≈{" "}
                <Money value={estimate.cost_usd} /> · ≈ {estimate.hours_min}–
                {estimate.hours_max} часа
              </div>
            </div>
          ) : (
            <Skeleton className="h-20 rounded" />
          )}

          <p className="text-body-sm text-muted-foreground">
            Проверка идёт в фоне, письмо придёт по завершении.
          </p>
        </div>

        <SheetFooter className="mt-6">
          <Button
            variant="secondary"
            className="h-9 rounded-sm"
            onClick={() => onOpenChange(false)}
          >
            Отмена
          </Button>
          <Button
            className="h-9 rounded-sm"
            disabled={!estimate}
            onClick={() => setConfirm(true)}
          >
            Запустить проверку
          </Button>
        </SheetFooter>

        <AlertDialog open={confirm} onOpenChange={setConfirm}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Запустить платную проверку?</AlertDialogTitle>
              <AlertDialogDescription>
                Будет списано ≈ <Money value={estimate?.cost_usd} /> за оценку ≈{" "}
                {estimate?.strings?.toLocaleString("ru-RU")} строк. Проверка
                займёт ≈ {estimate?.hours_min}–{estimate?.hours_max} часа.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel className="rounded-sm">
                Отмена
              </AlertDialogCancel>
              <AlertDialogAction
                className="rounded-sm"
                disabled={starting}
                onClick={(e) => {
                  e.preventDefault();
                  start();
                }}
              >
                {starting ? "Запуск…" : "Запустить"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </SheetContent>
    </Sheet>
  );
}

export function ProjectOverviewScreen({ project, navigate }) {
  const slug = project.slug;
  const [runs, setRuns] = React.useState(null);
  const [judgeOpen, setJudgeOpen] = React.useState(false);

  React.useEffect(() => {
    api.getRuns(slug).then(setRuns);
  }, [slug]);

  const liveRun = runs?.find(
    (r) => r.status === "running" || r.status === "queued",
  );
  const failedRun = runs?.find((r) => r.status === "error");

  return (
    <AppShell
      project={project}
      active="overview"
      navigate={navigate}
      breadcrumb={[{ label: "Проекты", path: "/" }, { label: project.name }]}
    >
      <div className="mb-6 flex items-center justify-between gap-3">
        <h1 className="text-heading-page text-foreground">Обзор</h1>
        <Button
          variant="secondary"
          className="h-9 rounded-sm"
          onClick={() => setJudgeOpen(true)}
        >
          <ShieldCheck className="h-4 w-4" /> Проверить качество
        </Button>
      </div>

      {failedRun && (
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded border border-destructive bg-background p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle
              className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
              aria-hidden
            />
            <div>
              <div className="text-label-strong text-foreground">
                Прогон остановлен с ошибкой
              </div>
              <div className="text-body-sm text-muted-foreground">
                {failedRun.error}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              className="h-9 rounded-sm"
              onClick={() => navigate(`/projects/${slug}/runs/${failedRun.id}`)}
            >
              Подробнее
            </Button>
            <Button
              className="h-9 rounded-sm"
              onClick={() => navigate(`/projects/${slug}/runs/${failedRun.id}`)}
            >
              Перезапустить незавершённое
            </Button>
          </div>
        </div>
      )}

      {/* status band */}
      <div className="mb-6 grid grid-cols-1 gap-6 md:grid-cols-3">
        <MetricTile
          value={`${project.ready_count} / ${project.language_count}`}
          label="Готово к выгрузке (языки)"
          tone="success"
          action={
            <button
              type="button"
              onClick={() => navigate(`/projects/${slug}/download`)}
              className="inline-flex items-center gap-1 text-body-sm text-primary hover:underline"
            >
              Скачать результат <ArrowRight className="h-3.5 w-3.5" />
            </button>
          }
        />
        <MetricTile
          value={project.decisions_count}
          label={`Требуют решения · ${project.blocking_count} блокируют выгрузку`}
          tone="warning"
          action={
            <button
              type="button"
              onClick={() =>
                navigate(`/projects/${slug}/decisions?kind=blocking`)
              }
              className="inline-flex items-center gap-1 text-body-sm text-primary hover:underline"
            >
              Открыть требующие решения <ArrowRight className="h-3.5 w-3.5" />
            </button>
          }
        />
        <MetricTile
          value={<Money value={project.spend_month} />}
          label={`Потрачено в ${project.month_label}`}
        />
      </div>

      {liveRun && (
        <div className="mb-6">
          <ConsoleCard title="Активный прогон">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-3">
                  <span className="text-label-strong text-foreground">
                    {liveRun.title}
                  </span>
                  <StatusBadge status={liveRun.status} />
                </div>
                <div className="mt-1 text-body-sm text-muted-foreground">
                  Идёт {liveRun.elapsed}. Отправим письмо, когда закончится.
                </div>
              </div>
              <Button
                variant="secondary"
                className="h-9 rounded-sm"
                onClick={() => navigate(`/projects/${slug}/runs/${liveRun.id}`)}
              >
                Открыть прогон
              </Button>
            </div>
          </ConsoleCard>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* language table */}
        <div className="lg:col-span-2">
          <ConsoleCard title="Языки">
            <div className="overflow-hidden rounded-sm border border-border">
              <table className="w-full border-collapse text-body-sm">
                <thead>
                  <tr className="bg-muted text-start text-label-strong">
                    <th className="px-3 py-2 text-start font-semibold">Язык</th>
                    <th className="px-3 py-2 text-end font-semibold">Строк</th>
                    <th className="px-3 py-2 text-end font-semibold">Статус</th>
                  </tr>
                </thead>
                <tbody>
                  {project.languages.map((l) => (
                    <tr
                      key={l.code}
                      tabIndex={0}
                      onClick={() =>
                        navigate(`/projects/${slug}/decisions?lang=${l.code}`)
                      }
                      onKeyDown={(e) =>
                        e.key === "Enter" &&
                        navigate(`/projects/${slug}/decisions?lang=${l.code}`)
                      }
                      className="h-10 cursor-pointer border-t border-border hover:bg-accent"
                    >
                      <td className="px-3">
                        {l.name}{" "}
                        <span className="font-mono text-muted-foreground">
                          {l.code}
                        </span>
                      </td>
                      <td className="px-3 text-end tabular-nums text-muted-foreground">
                        {l.strings.toLocaleString("ru-RU")}
                      </td>
                      <td className="px-3 text-end">
                        {l.status === "ready" && <StatusBadge status="ready" />}
                        {l.status === "translating" && (
                          <StatusBadge status="translating" />
                        )}
                        {l.status === "decisions" && (
                          <span className="inline-flex items-center gap-1.5 rounded-sm bg-warning-surface px-2 py-0.5 text-label-caps uppercase text-warning">
                            <AlertTriangle
                              className="h-3.5 w-3.5"
                              aria-hidden
                            />{" "}
                            {l.decisions} на решение
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </ConsoleCard>
        </div>

        {/* runs card */}
        <div>
          <ConsoleCard title="Прогоны">
            {runs === null ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-10 rounded-sm" />
                ))}
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {runs.slice(0, 5).map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => navigate(`/projects/${slug}/runs/${r.id}`)}
                      className="flex w-full items-center justify-between gap-2 py-2.5 text-start hover:bg-muted"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-body-sm text-foreground">
                          #{r.id} · {r.kind === "judge" ? "Оценка" : "Перевод"}
                        </div>
                        <div className="text-body-sm text-muted-foreground">
                          {fmt(r.started)}
                        </div>
                      </div>
                      <StatusBadge status={r.status} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </ConsoleCard>
        </div>
      </div>

      {/* content card */}
      <div className="mt-6">
        <ConsoleCard title="Содержимое проекта">
          <ul className="divide-y divide-border">
            {project.contents.map((c) => (
              <li
                key={c.id}
                className="flex items-center justify-between gap-3 py-3"
              >
                <div>
                  <div className="text-body-md text-foreground">{c.label}</div>
                  <div className="text-body-sm text-muted-foreground">
                    {c.type === "loc-kit" &&
                      `${c.keys?.toLocaleString("ru-RU")} ключей`}
                    {c.type === "glossary" && `${c.terms} терминов`}
                    {c.type === "store" && "тексты для стора"}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    className="h-8 rounded-sm"
                    onClick={() =>
                      navigate(`/projects/${slug}/download?content=${c.id}`)
                    }
                  >
                    <DownloadIcon className="h-4 w-4" />
                    Скачать
                  </Button>
                  <Button
                    variant="ghost"
                    className="h-8 rounded-sm"
                    onClick={() => navigate(`/projects/${slug}/upload`)}
                  >
                    <UploadIcon className="h-4 w-4" />
                    Загрузить
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          <div className="mt-3">
            <WeblateLink
              url={`https://weblate.hcgameloc.internal/projects/${slug}/`}
              navigate={navigate}
            />
          </div>
        </ConsoleCard>
      </div>

      <JudgeLaunchSheet
        slug={slug}
        open={judgeOpen}
        onOpenChange={setJudgeOpen}
        navigate={navigate}
      />
    </AppShell>
  );
}
