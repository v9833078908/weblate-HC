"use client";

import { Check, Copy, Download } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/console/AppShell";
import { ConsoleCard } from "@/components/console/primitives";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import * as api from "@/src/api/client";

const FORMATS = [
  { id: "xlsx", label: "XLSX" },
  { id: "csv", label: "CSV" },
  { id: "json", label: "JSON" },
  { id: "as-uploaded", label: "Как загружено (.xlsx)" },
];

function CopyField({ text }) {
  const [done, setDone] = React.useState(false);
  return (
    <Button
      variant="ghost"
      className="h-8 shrink-0 rounded-sm"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      }}
    >
      {done ? (
        <>
          <Check className="h-4 w-4" />
          Скопировано
        </>
      ) : (
        <>
          <Copy className="h-4 w-4" />
          Скопировать
        </>
      )}
    </Button>
  );
}

export function DownloadScreen({ project, navigate }) {
  const slug = project.slug;
  const params = new URLSearchParams(
    typeof window !== "undefined" ? window.location.search : "",
  );
  const [scope, setScope] = React.useState(params.get("content") || "whole");
  const [format, setFormat] = React.useState("xlsx");
  const [storeView, setStoreView] = React.useState("zip");
  const [summary, setSummary] = React.useState(null);

  React.useEffect(() => {
    api.getExportSummary(slug, scope).then(setSummary);
  }, [slug, scope]);

  const scopeItem = project.contents.find((c) => c.id === scope);
  const isStore = scopeItem?.type === "store";

  const download = async () => {
    const res = await api.exportFile(slug, scope, format);
    toast(`Файл готов: ${res.filename}`, { duration: 6000 });
  };

  return (
    <AppShell
      project={project}
      active="download"
      navigate={navigate}
      breadcrumb={[
        { label: "Проекты", path: "/" },
        { label: project.name, path: `/projects/${slug}` },
        { label: "Скачать" },
      ]}
    >
      <h1 className="mb-6 text-heading-page text-foreground">
        Скачать результат
      </h1>

      <div className="max-w-2xl space-y-6">
        <ConsoleCard title="Что скачать">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-label-strong">Область</Label>
              <Select value={scope} onValueChange={setScope}>
                <SelectTrigger className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="whole">Весь проект</SelectItem>
                  {project.contents.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {!isStore && (
              <div className="space-y-1">
                <Label className="text-label-strong">Формат</Label>
                <Select value={format} onValueChange={setFormat}>
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {FORMATS.map((f) => (
                      <SelectItem key={f.id} value={f.id}>
                        {f.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
            {isStore && (
              <div className="space-y-1">
                <Label className="text-label-strong">Вид</Label>
                <Select value={storeView} onValueChange={setStoreView}>
                  <SelectTrigger className="h-9">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="zip">ZIP-архив</SelectItem>
                    <SelectItem value="fields">Поля по языкам</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        </ConsoleCard>

        {isStore && storeView === "fields" ? (
          <ConsoleCard title="Поля по языкам">
            {/* TODO(api): store field text per language is represented from the export blob; no separate route. */}
            <Accordion type="single" collapsible className="w-full">
              {project.languages.map((l) => (
                <AccordionItem key={l.code} value={l.code}>
                  <AccordionTrigger className="text-body-md">
                    {l.name}{" "}
                    <span className="font-mono text-muted-foreground">
                      {l.code}
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    <ul className="space-y-3">
                      {["Название", "Краткое описание", "Полное описание"].map(
                        (f) => {
                          const text = `${f} — готовый перевод (${l.code})`;
                          return (
                            <li
                              key={f}
                              className="flex items-start justify-between gap-3 rounded-sm border border-border p-3"
                            >
                              <div>
                                <div className="text-label-caps uppercase text-muted-foreground">
                                  {f}
                                </div>
                                <div className="mt-1 text-body-sm text-foreground">
                                  {text}
                                </div>
                              </div>
                              <CopyField text={text} />
                            </li>
                          );
                        },
                      )}
                    </ul>
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </ConsoleCard>
        ) : (
          <div className="rounded border border-border p-4">
            {summary ? (
              <p className="text-body-md text-foreground">
                {summary.keys.toLocaleString("ru-RU")} ключей ·{" "}
                {summary.empty > 0 ? (
                  <button
                    type="button"
                    onClick={() =>
                      navigate(`/projects/${slug}/decisions?kind=blocking`)
                    }
                    className="text-warning underline-offset-4 hover:underline"
                  >
                    {summary.empty} выгружены пустыми → Требуют решения
                  </button>
                ) : (
                  "все ключи заполнены"
                )}
              </p>
            ) : (
              <p className="text-body-sm text-muted-foreground">
                Считаем итог…
              </p>
            )}
            {summary && summary.empty > 0 && (
              <p className="mt-1 text-body-sm text-muted-foreground">
                Строки с блокирующими проверками выгружаются пустыми, ключ
                остаётся в файле.
              </p>
            )}
          </div>
        )}

        <div>
          <Button className="h-9 rounded-sm" onClick={download}>
            <Download className="h-4 w-4" />
            Скачать результат
          </Button>
        </div>
      </div>
    </AppShell>
  );
}
