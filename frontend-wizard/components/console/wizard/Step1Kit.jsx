"use client";

import { Download, FileWarning } from "lucide-react";
import * as React from "react";
import { DropZone } from "@/components/console/upload-parts";
import { useWizard } from "@/components/console/wizard/WizardShell";
import * as api from "@/src/api/client";

const EXAMPLES = [
  { file: "pirate-ships.xlsx", label: "pirate-ships.xlsx (полный путь)" },
  {
    file: "utf16-engine-export.txt",
    label: "utf16-engine-export.txt (расширение лжёт)",
  },
  {
    file: "workbook-3-sheets.xlsx",
    label: "workbook-3-sheets.xlsx (несколько листов)",
  },
  {
    file: "positional-export.csv",
    label: "positional-export.csv (нет группировки)",
  },
  { file: "en-sparse.xlsx", label: "en-sparse.xlsx (en заполнен 41%)" },
];

const ROLE_RU = {
  key: "ключ",
  service: "служебная",
  character: "говорящий",
  explanation: "пояснение",
  language: "язык",
};

const SUBSTEPS = [
  "Кодировка и формат",
  "Колонки",
  "Языки",
  "Ключи и дубликаты",
  "Проверка импортом",
];

export function Step1Kit() {
  const { slug, upload, onParsed, set, s, patch, openRows } = useWizard();
  const [parsing, setParsing] = React.useState(false);

  const parse = async (file) => {
    set({ fileName: file });
    setParsing(true);
    const a = await api.createUploadAnalysis(slug, { file });
    setParsing(false);
    onParsed(a);
  };

  if (parsing) {
    return (
      <section>
        <h2 className="text-heading-card text-foreground">Анализируем…</h2>
        <div className="mt-4 rounded border border-border bg-muted p-6">
          <div className="mb-3 font-mono text-body-sm text-muted-foreground">
            {s.fileName}
          </div>
          <ul className="space-y-2">
            {SUBSTEPS.map((st, i) => (
              <li
                key={st}
                className="flex items-center gap-2 text-body-sm text-foreground"
              >
                <span
                  className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent"
                  style={{ animationDelay: `${i * 100}ms` }}
                />
                {st}
              </li>
            ))}
          </ul>
        </div>
      </section>
    );
  }

  if (!upload) {
    return (
      <section className="space-y-5">
        <div>
          <h2 className="text-heading-card text-foreground">
            Загрузите лок-кит
          </h2>
          <p className="mt-1 text-body-sm text-muted-foreground">
            Расширение не важно — формат определим по содержимому.
          </p>
        </div>
        <DropZone
          onPick={(name) => parse(name)}
          formats="XLSX · CSV · TSV · TXT"
        />
        <div>
          {/* biome-ignore lint/a11y/useValidAnchor: link-styled action, href="#" + preventDefault prevents navigation; converting to <button> would change middle-click/context-menu semantics */}
          <a
            href="#"
            onClick={(e) => e.preventDefault()}
            className="inline-flex items-center gap-1 text-body-sm text-primary hover:underline"
          >
            <Download className="h-3.5 w-3.5" />
            Скачать шаблон лок-кита
          </a>
          <p className="mt-1 text-body-sm text-muted-foreground">
            Character — имя говорящего и ничего больше. Explanation — всё, чего
            переводчик не увидит в самой строке.
          </p>
        </div>
        <div className="rounded-sm border border-border bg-muted p-3">
          <div className="mb-2 text-label-caps uppercase text-muted-foreground">
            Примеры файлов (для ревью состояний)
          </div>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((e) => (
              <button
                type="button"
                key={e.file}
                onClick={() => parse(e.file)}
                className="rounded-sm border border-border bg-card px-3 py-1.5 text-body-sm text-foreground hover:bg-accent"
              >
                {e.label}
              </button>
            ))}
          </div>
        </div>
      </section>
    );
  }

  // stop: choose sheet
  if (upload.stop?.kind === "sheet_choice") {
    return (
      <section className="space-y-4">
        <div className="flex items-start gap-3 rounded border border-warning bg-warning-surface p-4">
          <FileWarning
            className="mt-0.5 h-5 w-5 shrink-0 text-warning"
            aria-hidden
          />
          <div className="text-body-sm text-warning">
            В книге {upload.format.sheets.length} листа. Одна загрузка создаёт
            один компонент — выберите лист. Остальные листы можно сделать
            компонентами на шаге «Компоненты».
          </div>
        </div>
        <div className="space-y-2">
          {upload.format.sheets.map((sh) => (
            <button
              type="button"
              key={sh.name}
              onClick={() => patch({ sheet: sh.name })}
              className="flex w-full items-center justify-between rounded-sm border border-border bg-card px-4 py-3 text-start hover:border-primary"
            >
              <span className="font-mono text-body-md text-foreground">
                {sh.name}
              </span>
              <span className="text-body-sm text-muted-foreground">
                {sh.rows.toLocaleString("ru-RU")} строк
              </span>
            </button>
          ))}
        </div>
      </section>
    );
  }

  const langCols = upload.columns.filter((c) => c.role === "language");
  const q = upload.rows.quarantined_by_reason || {};
  const qTotal = Object.values(q).reduce((a, b) => a + b, 0);

  return (
    <section className="space-y-4">
      <h2 className="text-heading-card text-foreground">Что мы нашли</h2>
      {upload.note && (
        <div className="rounded-sm bg-info-surface p-3 text-body-sm text-info-strong">
          {upload.note}
        </div>
      )}

      <div className="rounded border border-border p-4">
        <div className="text-label-caps uppercase text-muted-foreground">
          Формат
        </div>
        <div className="mt-1 text-body-md text-foreground">
          {upload.format.kind}
          {upload.format.sheet ? `, лист «${upload.format.sheet}»` : ""}
          {upload.format.encoding && upload.format.encoding !== "UTF-8"
            ? `, ${upload.format.encoding}${upload.format.bom ? " с BOM" : ""}`
            : ""}
          , {upload.rows.total.toLocaleString("ru-RU")} строк
        </div>
      </div>

      <div className="rounded border border-border p-4">
        <div className="mb-2 text-label-caps uppercase text-muted-foreground">
          Колонки (в порядке файла)
        </div>
        <div className="flex flex-wrap gap-2">
          {upload.columns.map((c) => (
            <div
              key={c.header}
              className="rounded-sm border border-border bg-muted px-2.5 py-1.5"
            >
              <div className="font-mono text-body-sm text-foreground">
                {c.header}
              </div>
              <div className="text-label-caps uppercase text-muted-foreground">
                {ROLE_RU[c.role]}
                {c.role === "language" ? ` ${c.language || "?"}` : ""}
                {c.renamed_to && c.renamed_to !== c.header
                  ? ` → ${c.renamed_to}`
                  : ""}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded border border-border p-4">
        <div className="mb-2 text-label-caps uppercase text-muted-foreground">
          Языки
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-body-sm text-foreground">
          {langCols.map((c) => (
            <span key={c.header}>
              {c.header} {c.fill}%{c.ambiguous ? " (уточним вариант)" : ""}
              {c.low_fill ? " (уточним, язык ли это)" : ""}
            </span>
          ))}
        </div>
      </div>

      <div className="rounded border border-border p-4">
        <div className="flex items-center justify-between">
          <div className="text-label-caps uppercase text-muted-foreground">
            Строки
          </div>
          {qTotal > 0 && (
            <button
              type="button"
              onClick={() =>
                openRows({ set: "quarantine", title: "Строки в карантине" })
              }
              className="text-body-sm text-primary hover:underline"
            >
              Показать
            </button>
          )}
        </div>
        <div className="mt-1 text-body-md text-foreground">
          {upload.rows.total.toLocaleString("ru-RU")} всего ·{" "}
          {upload.rows.importable.toLocaleString("ru-RU")} готовы к импорту
          {qTotal > 0 ? ` · ${qTotal} в карантин` : ""}
        </div>
        {qTotal > 0 && (
          <div className="mt-1 text-body-sm text-muted-foreground">
            {q.empty_all ? `${q.empty_all} пустых во всех языках` : ""}
            {q.duplicate_exact || q.duplicate_conflict
              ? ` · ${(q.duplicate_exact || 0) + (q.duplicate_conflict || 0)} дубликатов ключа`
              : ""}
            {q.empty_key ? ` · ${q.empty_key} без ключа` : ""}
          </div>
        )}
      </div>

      {upload.header_map?.length > 0 && (
        <div className="rounded-sm bg-muted p-3 text-body-sm text-muted-foreground">
          Заголовки, которые переписали:{" "}
          {upload.header_map.map((m) => `${m.from} → ${m.to}`).join(", ")}
        </div>
      )}
    </section>
  );
}
