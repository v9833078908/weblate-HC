"use client";

import { ArrowRight, Store } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/console/AppShell";
import { DropZone, LocKitPreview } from "@/components/console/upload-parts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";

const STORES = [
  { id: "steam", label: "Steam" },
  { id: "google_play", label: "Google Play" },
  { id: "app_store", label: "App Store" },
  { id: "custom", label: "Произвольный" },
];

function StoreField({ field, value, onChange, markup }) {
  const over = field.limit > 0 && value.length > field.limit;
  const warn = field.limit > 0 && !over && value.length >= field.limit * 0.9;
  const big = field.limit === 0 || field.limit > 200;
  const Comp = big ? Textarea : Input;
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className="text-label-strong">{field.label}</Label>
        {field.limit > 0 && (
          <span
            className={cn(
              "text-body-sm tabular-nums",
              over
                ? "text-destructive"
                : warn
                  ? "text-warning"
                  : "text-muted-foreground",
            )}
          >
            {value.length} / {field.limit}
          </span>
        )}
      </div>
      <Comp
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={big ? 4 : undefined}
        className={cn(
          !big && "h-9",
          over && "border-destructive focus-visible:ring-destructive",
        )}
        aria-invalid={over}
      />
      {over && (
        <p className="text-body-sm text-destructive">
          Превышен лимит — {value.length - field.limit} симв. сверх нормы.
          Уберите лишнее перед отправкой.
        </p>
      )}
      {markup && (
        <p className="text-body-sm text-muted-foreground">
          Разрешена BBCode-разметка.
        </p>
      )}
    </div>
  );
}

export function UploadScreen({ project, navigate }) {
  const slug = project.slug;

  // loc-kit tab
  const [parsing, setParsing] = React.useState(false);
  const [fileName, setFileName] = React.useState("");
  const [preview, setPreview] = React.useState(null);
  const [submitting, setSubmitting] = React.useState(false);

  const pick = async (name) => {
    setFileName(name);
    setParsing(true);
    const p = await api.createUpload(slug, { kind: "loc-kit" });
    setPreview(p);
    setParsing(false);
  };
  const submitKit = async () => {
    setSubmitting(true);
    const run = await api.confirmUpload(slug, preview.id);
    setSubmitting(false);
    navigate(`/projects/${slug}/runs/${run.id}`);
  };

  // store tab
  const [store, setStore] = React.useState("steam");
  const [meta, setMeta] = React.useState(null);
  const [values, setValues] = React.useState({});
  React.useEffect(() => {
    setMeta(null);
    setValues({});
    api.getStoreFields(store).then(setMeta);
  }, [store]);
  const anyOver = meta
    ? meta.fields.some(
        (f) => f.limit > 0 && (values[f.id] || "").length > f.limit,
      )
    : false;
  const submitStore = async () => {
    await api.createUpload(slug, { kind: "store", store, fields: values });
    const run = await api.confirmUpload(slug, "store-upload");
    toast("Тексты стора отправлены на перевод");
    navigate(`/projects/${slug}/runs/${run.id}`);
  };

  return (
    <AppShell
      project={project}
      active="upload"
      navigate={navigate}
      breadcrumb={[
        { label: "Проекты", path: "/" },
        { label: project.name, path: `/projects/${slug}` },
        { label: "Загрузить" },
      ]}
    >
      <h1 className="mb-6 text-heading-page text-foreground">Загрузить</h1>

      <Tabs defaultValue="kit">
        <TabsList>
          <TabsTrigger value="kit">Лок-кит</TabsTrigger>
          <TabsTrigger value="store">Тексты для стора</TabsTrigger>
        </TabsList>

        <TabsContent value="kit" className="mt-5 space-y-5">
          {preview ? (
            <>
              <LocKitPreview preview={preview} showDiff />
              <div className="flex items-center gap-3 border-t border-border pt-4">
                <Button
                  className="h-9 rounded-sm"
                  disabled={submitting}
                  onClick={submitKit}
                >
                  {submitting ? (
                    "Отправка…"
                  ) : (
                    <>
                      Загрузить и перевести <ArrowRight className="h-4 w-4" />
                    </>
                  )}
                </Button>
                <Button
                  variant="ghost"
                  className="h-9 rounded-sm"
                  onClick={() => {
                    setPreview(null);
                    setFileName("");
                  }}
                >
                  Другой файл
                </Button>
              </div>
            </>
          ) : (
            <DropZone onPick={pick} parsing={parsing} fileName={fileName} />
          )}
        </TabsContent>

        <TabsContent value="store" className="mt-5 space-y-5">
          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1">
              <Label className="text-label-strong">Магазин</Label>
              <Select value={store} onValueChange={setStore}>
                <SelectTrigger className="h-9 w-64">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STORES.map((s) => (
                    <SelectItem key={s.id} value={s.id}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="pb-2 text-body-sm text-muted-foreground">
              Исходный язык:{" "}
              <span className="font-mono text-foreground">
                {project.source_language || "ru"}
              </span>{" "}
              (фиксирован)
            </div>
          </div>

          {meta ? (
            <div className="space-y-4">
              <div className="rounded-sm bg-muted px-3 py-2 text-body-sm text-muted-foreground">
                Лимиты символов — {meta.limits_source}.
              </div>
              {meta.fields.map((f) => (
                <StoreField
                  key={f.id}
                  field={f}
                  markup={f.markup}
                  value={values[f.id] || ""}
                  onChange={(v) => setValues((s) => ({ ...s, [f.id]: v }))}
                />
              ))}
              <div className="flex items-center gap-3 border-t border-border pt-4">
                <Button
                  className="h-9 rounded-sm"
                  disabled={anyOver}
                  onClick={submitStore}
                >
                  <Store className="h-4 w-4" />
                  Перевести
                </Button>
                {anyOver && (
                  <span className="text-body-sm text-destructive">
                    Есть поля сверх лимита — исправьте перед отправкой.
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-16 rounded" />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </AppShell>
  );
}
