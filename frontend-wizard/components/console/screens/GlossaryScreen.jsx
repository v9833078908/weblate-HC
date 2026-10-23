"use client";

import { BookText, Plus } from "lucide-react";
import * as React from "react";
import { AppShell } from "@/components/console/AppShell";
import { EmptyState } from "@/components/console/primitives";
import { DropZone, langName } from "@/components/console/upload-parts";
import { GlossaryWorkspace } from "@/components/console/wizard/GlossaryWorkspace";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";

const RULE_STYLE = {
  обязательный: "bg-info-surface text-info-strong",
  "не переводить": "bg-muted text-muted-foreground",
  запрещён: "border border-destructive text-destructive",
  обычный: "bg-muted text-muted-foreground",
};
const COLS = ["en", "de", "fr", "ja"];

export function GlossaryScreen({ project, navigate }) {
  const slug = project.slug;
  const [terms, setTerms] = React.useState(null);
  const [adding, setAdding] = React.useState(false);
  const [newTerm, setNewTerm] = React.useState("");

  React.useEffect(() => {
    api.getGlossary(slug).then(setTerms);
  }, [slug]);

  return (
    <AppShell
      project={project}
      active="glossary"
      navigate={navigate}
      breadcrumb={[
        { label: "Проекты", path: "/" },
        { label: project.name, path: `/projects/${slug}` },
        { label: "Глоссарий" },
      ]}
    >
      <div className="mb-6 flex items-center justify-between gap-3">
        <h1 className="text-heading-page text-foreground">Глоссарий</h1>
        <Button className="h-9 rounded-sm" onClick={() => setAdding((v) => !v)}>
          <Plus className="h-4 w-4" />
          Добавить термин
        </Button>
      </div>

      {terms === null ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-10 rounded-sm" />
          ))}
        </div>
      ) : terms.length === 0 ? (
        <EmptyState
          icon={BookText}
          title="Глоссарий пуст"
          description="Глоссарий заметно повышает качество перевода — добавьте термины или извлеките их из лок-кита."
        />
      ) : (
        <div className="overflow-hidden rounded border border-border">
          <table className="w-full text-body-sm">
            <thead>
              <tr className="bg-muted text-label-strong">
                <th className="px-3 py-2 text-start font-semibold">Источник</th>
                {COLS.map((c) => (
                  <th key={c} className="px-3 py-2 text-start font-semibold">
                    {langName(c)}
                  </th>
                ))}
                <th className="px-3 py-2 text-start font-semibold">
                  Пояснение
                </th>
                <th className="px-3 py-2 text-start font-semibold">Правило</th>
              </tr>
            </thead>
            <tbody>
              {adding && (
                <tr className="border-t border-border bg-accent/40">
                  <td className="px-3 py-2">
                    <Input
                      value={newTerm}
                      onChange={(e) => setNewTerm(e.target.value)}
                      className="h-8"
                      placeholder="новый термин"
                    />
                  </td>
                  <td colSpan={COLS.length + 2} className="px-3 py-2">
                    <Button
                      className="h-8 rounded-sm"
                      disabled={!newTerm}
                      onClick={() => {
                        setTerms((t) => [
                          {
                            source: newTerm,
                            targets: {},
                            note: "",
                            rule: "обычный",
                          },
                          ...t,
                        ]);
                        setNewTerm("");
                        setAdding(false);
                      }}
                    >
                      Сохранить
                    </Button>
                  </td>
                </tr>
              )}
              {terms.map((t, i) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: glossary rows have no stable unique id (source may repeat)
                <tr key={i} className="border-t border-border">
                  <td className="px-3 py-2 font-mono text-foreground">
                    {t.source}
                  </td>
                  {COLS.map((c) => (
                    <td key={c} className="px-3 py-2 text-foreground">
                      {t.targets?.[c] || (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  ))}
                  <td className="px-3 py-2 text-muted-foreground">{t.note}</td>
                  <td className="px-3 py-2">
                    <span
                      className={cn(
                        "rounded-sm px-2 py-0.5 text-label-caps uppercase",
                        RULE_STYLE[t.rule] || "bg-muted text-muted-foreground",
                      )}
                    >
                      {t.rule}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-6">
        <DropZone
          onPick={() => {}}
          label="Добавить таблицу терминов (только новые термины добавляются, существующие не меняются)"
          formats="CSV · XLSX"
        />
      </div>

      <div className="mt-8">
        <h2 className="mb-3 text-heading-card text-foreground">
          Кандидаты из лок-кита
        </h2>
        <GlossaryWorkspace
          slug={slug}
          uploadId="loc-kit"
          mode="page"
          targetLanguages={COLS}
          categories={{
            item: true,
            character: true,
            place: true,
            concept: true,
          }}
        />
      </div>
    </AppShell>
  );
}
