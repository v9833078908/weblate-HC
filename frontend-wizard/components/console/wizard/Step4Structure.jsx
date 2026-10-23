"use client";

import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import * as React from "react";
import { useWizard } from "@/components/console/wizard/WizardShell";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

const ROLE_OPTIONS = [
  { v: "key", l: "Ключ" },
  { v: "character", l: "Говорящий (Character)" },
  { v: "explanation", l: "Пояснение (Explanation)" },
  { v: "note", l: "Комментарий для переводчика" },
  { v: "service", l: "Служебная колонка" },
];
const SERVICE_OPTIONS = [
  { v: "keep", l: "Оставить значения" },
  { v: "empty", l: "Оставить пустой" },
  { v: "drop", l: "Не импортировать" },
];

export function Step4Structure() {
  const { upload, patch, s, openRows } = useWizard();

  // refresh gate on entering this step with current answers
  // biome-ignore lint/correctness/useExhaustiveDependencies: mount-once gate refresh; s/patch captured intentionally (existing eslint-disable).
  React.useEffect(() => {
    const _langs = (s.languages || [])
      .filter((l) => l.include)
      .map((l) => l.resolved_as || l.code);
    patch({
      languages: s.languages || [],
      duplicates_policy: s.duplicates_policy,
    });
    // eslint-disable-next-line
  }, []);

  if (!upload) return null;
  const nonLang = upload.columns.filter((c) => c.role !== "language");
  const q = upload.rows.quarantined_by_reason || {};

  const setRole = (header, role) => {
    const cols = upload.columns.map((c) =>
      c.header === header ? { ...c, role } : c,
    );
    patch({ columns: cols });
  };
  const setService = (header, policy) => {
    const cols = upload.columns.map((c) =>
      c.header === header ? { ...c, service_policy: policy } : c,
    );
    patch({ columns: cols });
  };
  const setDup = (policy) => patch({ duplicates_policy: policy });

  const gate = upload.gate;

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-heading-card text-foreground">Структура кита</h2>
        <p className="mt-1 text-body-sm text-muted-foreground">
          Роли неязыковых колонок. Character и Explanation — две разные колонки,
          они не объединяются.
        </p>
      </div>

      <div className="overflow-hidden rounded border border-border">
        <table className="w-full text-body-sm">
          <thead>
            <tr className="bg-muted text-label-strong">
              <th className="px-3 py-2 text-start font-semibold">Колонка</th>
              <th className="px-3 py-2 text-start font-semibold">Роль</th>
              <th className="px-3 py-2 text-start font-semibold">
                Что произойдёт
              </th>
            </tr>
          </thead>
          <tbody>
            {nonLang.map((c) => {
              const flagName = ["flags", "weblate-flags", "флаги"].includes(
                c.header.toLowerCase(),
              );
              return (
                <tr key={c.header} className="border-t border-border align-top">
                  <td className="px-3 py-2 font-mono text-foreground">
                    {c.header}
                    {c.renamed_to && c.renamed_to !== c.header && (
                      <div className="text-body-sm text-muted-foreground">
                        → {c.renamed_to}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    <Select
                      value={c.role}
                      onValueChange={(v) => setRole(c.header, v)}
                    >
                      <SelectTrigger className="h-9 w-56">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ROLE_OPTIONS.map((o) => (
                          <SelectItem key={o.v} value={o.v}>
                            {o.l}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {c.role === "service" && (
                      <Select
                        value={c.service_policy || "keep"}
                        onValueChange={(v) => setService(c.header, v)}
                      >
                        <SelectTrigger className="mt-2 h-9 w-56">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {SERVICE_OPTIONS.map((o) => (
                            <SelectItem key={o.v} value={o.v}>
                              {o.l}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {c.role === "key" && "Уникальный идентификатор строки"}
                    {c.role === "character" &&
                      "Имя говорящего уедет в отдельное поле"}
                    {c.role === "explanation" &&
                      "Контекст для переводчика и судьи"}
                    {c.role === "note" && "Developer note в Weblate"}
                    {c.role === "service" &&
                      (c.service_policy === "drop"
                        ? "Не импортируется"
                        : c.service_policy === "empty"
                          ? "Импортируется пустой"
                          : "Значения сохраняются")}
                    {c.language_shaped && (
                      <div className="mt-1 rounded-sm bg-muted px-2 py-0.5 text-body-sm">
                        Похоже на язык — переименовали в «{c.renamed_to}»
                      </div>
                    )}
                    {flagName && (
                      <div className="mt-1 flex items-start gap-1.5 rounded-sm bg-warning-surface px-2 py-1 text-warning">
                        <AlertTriangle className="mt-0.5 h-3.5 w-3.5" />
                        Колонка с таким именем не импортируется как флаги —
                        переименуйте её или оставьте служебной.
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* identity audit */}
      <div className="rounded border border-border p-4">
        <div className="mb-3 text-label-caps uppercase text-muted-foreground">
          Аудит ключей и строк
        </div>
        <ul className="space-y-2 text-body-sm">
          {q.duplicate_exact ? (
            <li className="flex items-center justify-between">
              <span>
                Точные дубликаты: {q.duplicate_exact} — оставим первую копию,
                остальные в карантин
              </span>
              <button
                type="button"
                onClick={() =>
                  openRows({ set: "duplicates", title: "Дубликаты ключа" })
                }
                className="text-primary hover:underline"
              >
                Показать
              </button>
            </li>
          ) : null}
          {q.duplicate_conflict ? (
            <li className="flex items-center justify-between">
              <span>
                Один ключ, разные тексты: {q.duplicate_conflict} — в карантин до
                уточнения от разработчиков
              </span>
              <button
                type="button"
                onClick={() =>
                  openRows({
                    set: "duplicates",
                    title: "Ключ с разными текстами",
                  })
                }
                className="text-primary hover:underline"
              >
                Показать
              </button>
            </li>
          ) : null}
          {q.empty_key ? (
            <li className="flex items-center justify-between">
              <span>Пустой ключ: {q.empty_key} — в карантин</span>
              <button
                type="button"
                onClick={() =>
                  openRows({ set: "quarantine", title: "Пустой ключ" })
                }
                className="text-primary hover:underline"
              >
                Показать
              </button>
            </li>
          ) : null}
          {q.empty_all ? (
            <li className="flex items-center justify-between">
              <span>
                Пусто во всех языках: {q.empty_all} — в карантин (такая строка
                иначе отклоняет весь файл)
              </span>
              <button
                type="button"
                onClick={() =>
                  openRows({ set: "empty_all", title: "Пусто во всех языках" })
                }
                className="text-primary hover:underline"
              >
                Показать
              </button>
            </li>
          ) : null}
          <li className="text-muted-foreground">
            Пустой перевод при заполненном оригинале:{" "}
            {upload.rows.empty_targets?.toLocaleString("ru-RU")} — это
            нормально, переведём
          </li>
        </ul>
      </div>

      <div className="rounded border border-border p-4">
        <div className="mb-3 text-label-strong text-foreground">
          Как поступить с дубликатами и битыми строками?
        </div>
        <div className="flex flex-col gap-2">
          <label className="flex items-center gap-2 text-body-sm">
            <input
              type="radio"
              name="dup"
              checked={s.duplicates_policy === "quarantine"}
              onChange={() => setDup("quarantine")}
            />{" "}
            В карантин, не угадывать (рекомендуем)
          </label>
          <label className="flex items-center gap-2 text-body-sm">
            <input
              type="radio"
              name="dup"
              checked={s.duplicates_policy === "stop"}
              onChange={() => setDup("stop")}
            />{" "}
            Остановиться — пришлю исправленный кит
          </label>
        </div>
        <p className="mt-2 text-body-sm text-muted-foreground">
          Мы никогда не переименовываем ключи ради уникальности и не склеиваем
          строки по похожести.
        </p>
      </div>

      {/* gate */}
      {gate && (
        <div
          className={cn(
            "flex items-start gap-3 rounded border p-4",
            gate.ready
              ? "border-success bg-background"
              : "border-destructive bg-background",
          )}
        >
          {gate.ready ? (
            <CheckCircle2
              className="mt-0.5 h-5 w-5 shrink-0 text-success"
              aria-hidden
            />
          ) : (
            <XCircle
              className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
              aria-hidden
            />
          )}
          <div>
            <div className="text-label-strong text-foreground">
              Проверка импортом: {gate.ready ? "готово" : "не готово"}
            </div>
            {gate.ready ? (
              <div className="text-body-sm text-muted-foreground">
                {gate.imported.toLocaleString("ru-RU")} строк · 0 пропущено ·
                исходный {gate.source} · языки {gate.languages.join(", ")} ·
                пояснения: {gate.explanations} строк
              </div>
            ) : (
              <ul className="text-body-sm text-destructive">
                {gate.errors.map((e, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: static gate error list; row numbers may repeat
                  <li key={i}>
                    строка {e.row}: {e.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      {gate?.questions?.length > 0 && (
        <div className="rounded border border-border bg-muted p-4">
          <div className="mb-2 text-label-caps uppercase text-muted-foreground">
            Вопросы для шага «Глоссарий»
          </div>
          <ul className="list-disc space-y-1 ps-5 text-body-sm text-foreground">
            {gate.questions.map((q2, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: static question list; terms may repeat
              <li key={i}>
                «{q2.term}» одинаков во всех языках — это название, которое не
                переводится?
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
