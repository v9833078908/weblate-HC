"use client";

import { AlertTriangle, Lock } from "lucide-react";
import { useWizard } from "@/components/console/wizard/WizardShell";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const CARDS = [
  {
    code: "ru",
    title: "Русский (ru)",
    give: "Быстрее и удобнее в ежедневной работе: авторы строк, продюсер и QA читают оригинал на своём языке; пояснения, глоссарий, сообщения проверок и промпты подсказок живут на нём же; между оригиналом и переводами нет лишнего звена, которое нужно поддерживать",
    pay: "На части целевых языков доступные подрядчики и модели могут работать с английского лучше, чем с русского, — качество на них может выйти ниже",
  },
  {
    code: "en",
    title: "Английский (en)",
    give: "Потенциально выше качество на части языков, если доступные там подрядчики и модели работают с английского лучше, чем с русского",
    pay: "Английский придётся писать и вычитывать как настоящий оригинал. Если в ките он получен машинным переводом с русского, каждый перевод унаследует невычитанный пивот, а русскоязычная команда будет вычитывать язык, на котором не пишет",
  },
];

export function Step2Source() {
  const { upload, s, set, patch } = useWizard();
  const fill = (code) => upload?.columns.find((c) => c.language === code)?.fill;
  const hasExplanation = upload?.columns.some((c) => c.role === "explanation");

  const choose = async (code) => {
    set({ source_language: code });
    await patch({ source_language: code });
  };

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-heading-card text-foreground">
          Исходным языком делаем русский или английский?
        </h2>
        <p className="mt-1 text-body-sm text-muted-foreground">
          Русский быстрее и удобнее в ежедневной работе; английский потенциально
          даёт выше качество на части языков. Выбор после создания компонента не
          меняется.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {CARDS.map((c) => {
          const on = s.source_language === c.code;
          const f = fill(c.code);
          return (
            <button
              type="button"
              key={c.code}
              onClick={() => choose(c.code)}
              className={cn(
                "flex flex-col rounded border p-4 text-start transition-colors",
                on
                  ? "border-primary ring-2 ring-primary ring-offset-2"
                  : "border-border hover:border-primary",
              )}
            >
              <div className="text-heading-card text-foreground">{c.title}</div>
              <div className="mt-3 text-label-caps uppercase text-muted-foreground">
                Что даёт
              </div>
              <p className="mt-1 text-body-sm text-foreground">{c.give}</p>
              <div className="mt-3 text-label-caps uppercase text-muted-foreground">
                Чем платим
              </div>
              <p className="mt-1 text-body-sm text-muted-foreground">{c.pay}</p>
              {f != null && (
                <div className="mt-3 rounded-sm bg-muted px-2 py-1 text-body-sm text-foreground">
                  В ките: колонка {c.code} заполнена на {f}%
                </div>
              )}
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-1.5 text-body-sm text-muted-foreground">
        <Lock className="h-3.5 w-3.5" aria-hidden /> После запуска не меняется.
      </div>

      {upload?.source_conflict && (
        <div className="flex flex-col gap-3 rounded border border-warning bg-warning-surface p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle
              className="mt-0.5 h-5 w-5 shrink-0 text-warning"
              aria-hidden
            />
            <div className="text-body-sm text-warning">
              Вы выбрали английский как исходный, но колонка en заполнена на{" "}
              {upload.source_conflict.fill}% (
              {upload.source_conflict.rows.toLocaleString("ru-RU")} из{" "}
              {upload.rows.total.toLocaleString("ru-RU")} строк) — как оригинал
              она не годится.
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button className="h-9 rounded-sm" onClick={() => choose("ru")}>
              Выбрать русский
            </Button>
            <Button
              variant="secondary"
              className="h-9 rounded-sm"
              onClick={() =>
                window.location.assign(`/projects/${upload ? "" : ""}`)
              }
              disabled
            >
              Загрузить другой кит
            </Button>
          </div>
        </div>
      )}

      {hasExplanation && (
        <div className="border-t border-border pt-5">
          <div className="mb-2 text-label-strong text-foreground">
            Язык пояснений (колонка Explanation)
          </div>
          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-2 text-body-sm">
              <input
                type="radio"
                name="expl"
                checked={s.explanation_language === "source"}
                onChange={() => set({ explanation_language: "source" })}
              />{" "}
              Как исходный язык
            </label>
            <label className="flex items-center gap-2 text-body-sm">
              <input
                type="radio"
                name="expl"
                checked={s.explanation_language === "other"}
                onChange={() => set({ explanation_language: "other" })}
              />{" "}
              Другой
            </label>
          </div>
        </div>
      )}
    </section>
  );
}
