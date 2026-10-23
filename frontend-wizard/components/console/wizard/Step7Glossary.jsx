"use client";

import { Check, Info, X } from "lucide-react";
import * as React from "react";
import { DropZone, langName } from "@/components/console/upload-parts";
import { GlossaryWorkspace } from "@/components/console/wizard/GlossaryWorkspace";
import { useWizard } from "@/components/console/wizard/WizardShell";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";

const RULE_RU = {
  read_only: "Не переводить — оставить как в оригинале",
  exact: "Строго в этой форме, без склонения",
  forbidden: "Не использовать этот вариант",
};

const CATS = [
  { id: "item", label: "Предметы и ресурсы" },
  { id: "character", label: "Персонажи" },
  { id: "place", label: "Места и локации" },
  {
    id: "concept",
    label: "Повторяющиеся игровые понятия (валюты, режимы, фракции)",
  },
  { id: "button", label: "Названия кнопок и меню" },
  { id: "phrase", label: "Целые реплики" },
];

export function Step7Glossary() {
  const { slug, s, set } = useWizard();
  const [exceptions, setExceptions] = React.useState(null);
  const g = s.glossary;
  const setG = (patch) =>
    set((p) => ({
      glossary: {
        ...p.glossary,
        ...(typeof patch === "function" ? patch(p.glossary) : patch),
      },
    }));

  React.useEffect(() => {
    api.previewExceptions(slug, {}).then(setExceptions);
  }, [slug]);

  const targets = (s.languages || [])
    .filter((l) => l.include)
    .map((l) => l.resolved_as || l.code)
    .filter((c) => c !== s.source_language);
  const wsTargets = ["en", "de", "fr", "ja"].filter((c) => targets.includes(c));
  const wsTargetsFinal = wsTargets.length
    ? wsTargets
    : ["en", "de", "fr", "ja"];

  const approve = (id) =>
    setG((gg) => ({
      exceptionsApproved: { ...gg.exceptionsApproved, [id]: true },
      exceptionsRejected: { ...gg.exceptionsRejected, [id]: false },
    }));
  const rejectExc = (id) =>
    setG((gg) => ({
      exceptionsRejected: { ...gg.exceptionsRejected, [id]: true },
      exceptionsApproved: { ...gg.exceptionsApproved, [id]: false },
    }));

  const selectedCount = Object.values(g.selected).filter(Boolean).length;
  const approvedCount = Object.values(g.exceptionsApproved).filter(
    Boolean,
  ).length;
  const rejectedCount = Object.values(g.exceptionsRejected).filter(
    Boolean,
  ).length;

  return (
    <section className="space-y-6">
      <div className="flex items-start gap-2 rounded border border-info-surface bg-info-surface p-4 text-info-strong">
        <Info className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
        <span className="text-body-sm">
          Глоссарий заметно повышает качество перевода — рекомендуем заполнить.
          Термины уезжают в модель вместе с каждым запросом.
        </span>
      </div>

      {/* 7a materials */}
      <div className="rounded border border-border p-4">
        <div className="mb-1 text-heading-card text-foreground">
          Готовые материалы
        </div>
        <p className="mb-3 text-body-sm text-muted-foreground">
          Есть ли готовые правила или список терминов? Есть ли слова, которые
          нужно сохранять без перевода, писать строго в одной форме или не
          использовать?
        </p>
        <DropZone
          onPick={() => {}}
          label="Таблица терминов (CSV/XLSX: исходный язык, переводы, пояснение)"
          formats="CSV · XLSX"
        />
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-3">
          <div>
            <div className="mb-1 text-label-strong">Сохранять без перевода</div>
            <Textarea
              rows={3}
              value={g.materials.read_only}
              onChange={(e) =>
                setG((gg) => ({
                  materials: { ...gg.materials, read_only: e.target.value },
                }))
              }
              placeholder="один термин в строке"
            />
          </div>
          <div>
            <div className="mb-1 text-label-strong">Строго в одной форме</div>
            <Textarea
              rows={3}
              value={g.materials.exact}
              onChange={(e) =>
                setG((gg) => ({
                  materials: { ...gg.materials, exact: e.target.value },
                }))
              }
            />
          </div>
          <div>
            <div className="mb-1 text-label-strong">
              Не использовать (замена через →)
            </div>
            <Textarea
              rows={3}
              value={g.materials.forbidden}
              onChange={(e) =>
                setG((gg) => ({
                  materials: { ...gg.materials, forbidden: e.target.value },
                }))
              }
              placeholder="чёрт → проклятье"
            />
          </div>
        </div>
        <p className="mt-2 text-body-sm text-muted-foreground">
          Флаги из вашего файла мы покажем как предложения, а не применим
          автоматически.
        </p>
      </div>

      {/* 7b categories */}
      <div className="rounded border border-border p-4">
        <div className="mb-1 text-heading-card text-foreground">
          Что считаем термином?
        </div>
        <p className="mb-3 text-body-sm text-muted-foreground">
          Включаем названия предметов, персонажей и мест, а также повторяющиеся
          игровые понятия? Обычные кнопки и целые реплики не включаем.
        </p>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {CATS.map((c) => (
            <label
              key={c.id}
              htmlFor={`glossary-cat-${c.id}`}
              className="flex items-center gap-2 text-body-sm text-foreground"
            >
              <Checkbox
                id={`glossary-cat-${c.id}`}
                checked={!!g.categories[c.id]}
                onCheckedChange={(v) =>
                  setG((gg) => ({
                    categories: { ...gg.categories, [c.id]: !!v },
                  }))
                }
              />{" "}
              {c.label}
            </label>
          ))}
        </div>
        <p className="mt-2 text-body-sm text-muted-foreground">
          Количество терминов увидите после извлечения — заранее не обещаем.
        </p>
      </div>

      {/* 7c workspace */}
      <GlossaryWorkspace
        slug={slug}
        uploadId={s.uploadId}
        mode="wizard"
        targetLanguages={wsTargetsFinal}
        categories={g.categories}
        onStageChange={(sel) => setG({ selected: sel })}
      />

      {/* 7d exceptions */}
      <div className="rounded border border-border">
        <div className="border-b border-border bg-accent px-4 py-2.5">
          <h3 className="text-heading-card">Особые правила</h3>
        </div>
        <div className="p-4">
          <p className="mb-3 text-body-sm text-muted-foreground">
            Ничего не применяется, пока вы не одобрите каждую строку.
          </p>
          {exceptions === null ? (
            <p className="text-body-sm text-muted-foreground">Загрузка…</p>
          ) : (
            <ul className="space-y-2">
              {exceptions.map((e) => {
                const approved = g.exceptionsApproved[e.id];
                const rejectedE = g.exceptionsRejected[e.id];
                const cantApprove = e.rule === "forbidden" && !e.replacement;
                return (
                  <li
                    key={e.id}
                    className={cn(
                      "rounded border p-3",
                      approved
                        ? "border-success"
                        : rejectedE
                          ? "border-border opacity-60"
                          : "border-border",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-body-md text-foreground">
                            {e.term}
                          </span>
                          <span className="rounded-sm bg-muted px-1.5 py-0.5 text-label-caps uppercase text-muted-foreground">
                            {e.source}
                          </span>
                        </div>
                        <div className="mt-1 text-body-sm text-foreground">
                          {RULE_RU[e.rule]}
                          {e.rule === "forbidden" && e.replacement
                            ? `; замена: ${e.replacement}`
                            : ""}
                        </div>
                        <div className="mt-0.5 text-body-sm text-muted-foreground">
                          Языки: {e.languages.map(langName).join(", ")} · зачем:{" "}
                          {e.why} · запретит: {e.prohibits}
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <Button
                          variant={approved ? "default" : "secondary"}
                          className="h-8 rounded-sm"
                          disabled={cantApprove}
                          onClick={() => approve(e.id)}
                        >
                          <Check className="h-4 w-4" />
                          Одобрить
                        </Button>
                        <Button
                          variant="ghost"
                          className="h-8 rounded-sm"
                          onClick={() => rejectExc(e.id)}
                        >
                          <X className="h-4 w-4" />
                          Отклонить
                        </Button>
                      </div>
                    </div>
                    {cantApprove && (
                      <div className="mt-1 text-body-sm text-destructive">
                        Правило «не использовать» без замены нельзя одобрить.
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-3 space-y-1 text-body-sm text-muted-foreground">
            <div>
              Правило «строго в этой форме» действует только на языки, для
              которых в таблице есть перевод.
            </div>
            <div>
              Правило для одного языка из нескольких мы применим уже в проекте
              после запуска.
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-sm bg-muted p-3 text-body-sm text-foreground">
        Глоссарий: {selectedCount} терминов выбрано · {targets.length} языков ·
        особых правил: {approvedCount} одобрено, {rejectedCount} отклонено ·
        будет создан как компонент «Глоссарий». Термины только добавляются;
        существующие не перезаписываются.
      </div>
    </section>
  );
}
