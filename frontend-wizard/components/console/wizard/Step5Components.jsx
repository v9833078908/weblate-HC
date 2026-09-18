"use client";

import { CheckCircle2, Plus } from "lucide-react";
import * as React from "react";
import { useWizard } from "@/components/console/wizard/WizardShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";

const AMBIGUITY = [
  {
    family: "mission_descr_complete_tutorial",
    rows: 14,
    options: ["Обучение", "Миссии"],
  },
];

export function Step5Components() {
  const { s, set, upload } = useWizard();
  const [signals, setSignals] = React.useState(null);

  React.useEffect(() => {
    if (s.splitMode === "split" && !signals)
      api.getSplitSignals(upload.id).then(setSignals);
  }, [s.splitMode, signals, upload]);

  // seed rule from key families
  // biome-ignore lint/correctness/useExhaustiveDependencies: seed-once effect; deliberately not re-run on set identity (existing eslint-disable).
  React.useEffect(() => {
    if (
      s.splitMode === "split" &&
      signals?.key_families?.length &&
      s.split.rule.length === 0
    ) {
      const rule = [
        { name: "Диалоги", anchors: ["dialog_text_", "dialog_character_"] },
        { name: "Обучение", anchors: ["tutorial_"] },
        { name: "Миссии", anchors: ["mission_"] },
      ];
      set((p) => ({ split: { ...p.split, rule, residue: "UI (остаток)" } }));
    }
  }, [signals, s.splitMode]); // eslint-disable-line

  const choose = (mode) => set({ splitMode: mode });

  if (s.splitMode !== "split") {
    return (
      <section className="space-y-5">
        <div>
          <h2 className="text-heading-card text-foreground">
            Один компонент или разделить лок-кит на несколько?
          </h2>
        </div>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <button
            type="button"
            onClick={() => choose("single")}
            className={cn(
              "rounded border p-4 text-start",
              s.splitMode === "single"
                ? "border-primary ring-2 ring-primary ring-offset-2"
                : "border-border hover:border-primary",
            )}
          >
            <div className="text-heading-card text-foreground">
              Один компонент (по умолчанию)
            </div>
            <p className="mt-1 text-body-sm text-muted-foreground">
              Весь лок-кит остаётся единым набором строк.
            </p>
          </button>
          <button
            type="button"
            onClick={() => choose("split")}
            className="rounded border border-border p-4 text-start hover:border-primary"
          >
            <div className="text-heading-card text-foreground">
              Разделить — например UI, Диалоги, Обучение
            </div>
            <p className="mt-1 text-body-sm text-muted-foreground">
              Разные части получают отдельные компоненты для удобной работы.
            </p>
          </button>
        </div>
        <p className="text-body-sm text-muted-foreground">
          Все компоненты получают одинаковые колонки и один исходный язык.
        </p>
      </section>
    );
  }

  if (!signals)
    return (
      <div className="space-y-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24 rounded" />
        ))}
      </div>
    );

  const noSignal =
    !signals.key_families?.length &&
    !signals.group_columns?.length &&
    !signals.sheets?.length;
  if (noSignal) {
    return (
      <section className="space-y-4">
        <button
          type="button"
          onClick={() => choose("single")}
          className="text-body-sm text-primary hover:underline"
        >
          ← Оставить один компонент
        </button>
        <div className="rounded border border-warning bg-warning-surface p-4 text-body-sm text-warning">
          В ките нет признака группировки: ключи не образуют семейств, листов и
          колонки группы нет. Загрузите список от движка или оставьте один
          компонент.
        </div>
      </section>
    );
  }

  const total = upload.rows.total;
  const qTotal = Object.values(upload.rows.quarantined_by_reason || {}).reduce(
    (a, b) => a + b,
    0,
  );
  const famRows = Object.fromEntries(
    (signals.key_families || []).map((f) => [f.anchor, f.rows]),
  );
  const rule = s.split.rule;
  const compRows = (r) =>
    r.anchors.reduce((sum, a) => sum + (famRows[a] || 0), 0) +
    AMBIGUITY.filter((am) => s.split.ambiguity[am.family] === r.name).reduce(
      (x, am) => x + am.rows,
      0,
    );
  const matched = rule.reduce((sum, r) => sum + compRows(r), 0);
  const residueRows = Math.max(0, total - qTotal - matched);

  const setAmb = (family, dest) =>
    set((p) => ({
      split: {
        ...p.split,
        ambiguity: { ...p.split.ambiguity, [family]: dest },
      },
    }));
  const setResidue = (v) => set((p) => ({ split: { ...p.split, residue: v } }));
  const setName = (i, name) =>
    set((p) => {
      const r = [...p.split.rule];
      r[i] = { ...r[i], name };
      return { split: { ...p.split, rule: r } };
    });

  const allComponents = [
    ...rule.map((r) => ({ name: r.name, rows: compRows(r) })),
    { name: s.split.residue || "UI (остаток)", rows: residueRows },
  ];

  return (
    <section className="space-y-6">
      <button
        type="button"
        onClick={() => choose("single")}
        className="text-body-sm text-primary hover:underline"
      >
        ← Оставить один компонент
      </button>

      <div className="rounded border border-border p-4">
        <div className="mb-2 text-label-caps uppercase text-muted-foreground">
          Признак группировки
        </div>
        <div className="flex flex-col gap-2 text-body-sm">
          {signals.sheets?.length > 0 && (
            <label className="flex items-center gap-2">
              <input type="radio" name="sig" defaultChecked /> Листы книги:{" "}
              {signals.sheets.map((s2) => `${s2.name} (${s2.rows})`).join(", ")}
            </label>
          )}
          {signals.group_columns?.map((gc) => (
            <label key={gc.header} className="flex items-center gap-2">
              <input type="radio" name="sig" /> Колонка {gc.header}:{" "}
              {gc.values.map((v) => `${v.value} (${v.rows})`).join(", ")}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <input type="radio" name="sig" defaultChecked /> Семейства ключей по
            началу:{" "}
            {signals.key_families
              .map((f) => `${f.anchor} ${f.rows}`)
              .join(" · ")}
          </label>
        </div>
        {signals.substring_trap && (
          <p className="mt-2 text-body-sm text-muted-foreground">
            «{signals.substring_trap.anchor}» внутри ключа поймал бы ещё{" "}
            {signals.substring_trap.extra_rows} строк интерфейса (
            {signals.substring_trap.samples.join(", ")}) — используем только
            начало ключа.
          </p>
        )}
        <p className="mt-2 text-body-sm text-muted-foreground">
          Мы никогда не маршрутизируем по смыслу текста — только по началу
          ключа.
        </p>
      </div>

      <div className="rounded border border-border p-4">
        <div className="mb-3 text-label-caps uppercase text-muted-foreground">
          Правило
        </div>
        <div className="space-y-3">
          {rule.map((r, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: index is the rule's identity (setName(i,...)); list never reordered
            <div key={i} className="flex flex-wrap items-center gap-3">
              <Input
                value={r.name}
                onChange={(e) => setName(i, e.target.value)}
                className="h-9 w-40"
              />
              <span className="text-muted-foreground">←</span>
              {r.anchors.map((a) => (
                <span
                  key={a}
                  className="rounded-sm bg-muted px-2 py-1 font-mono text-body-sm"
                >
                  {a}
                </span>
              ))}
              <span className="text-body-sm text-muted-foreground">
                {compRows(r)} строк
              </span>
            </div>
          ))}
        </div>
        <Button variant="ghost" className="mt-3 h-8 rounded-sm" disabled>
          <Plus className="h-4 w-4" />
          Добавить компонент
        </Button>
      </div>

      <div className="rounded border border-border p-4">
        <div className="mb-2 text-label-strong text-foreground">
          В какой компонент попадают ключи без правила?
        </div>
        <Select
          value={s.split.residue || "UI (остаток)"}
          onValueChange={setResidue}
        >
          <SelectTrigger className="h-9 w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="UI (остаток)">UI (остаток)</SelectItem>
            {rule.map((r) => (
              <SelectItem key={r.name} value={r.name}>
                {r.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {AMBIGUITY.map((am) => (
        <div
          key={am.family}
          className="rounded border border-warning bg-warning-surface p-4"
        >
          <div className="text-label-strong text-warning">
            Спорное семейство
          </div>
          <div className="mt-1 text-body-sm text-foreground">
            <span className="font-mono">{am.family}</span> ({am.rows} строк) —
            куда отнести?
          </div>
          <div className="mt-3 flex gap-2">
            {am.options.map((o) => (
              <button
                type="button"
                key={o}
                onClick={() => setAmb(am.family, o)}
                className={cn(
                  "rounded-sm border px-3 py-1.5 text-body-sm",
                  s.split.ambiguity[am.family] === o
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-card hover:bg-muted",
                )}
              >
                {o}
              </button>
            ))}
          </div>
        </div>
      ))}

      <div className="rounded border border-border p-4">
        <div className="mb-2 text-label-caps uppercase text-muted-foreground">
          Итог
        </div>
        <div className="font-mono text-body-md text-foreground">
          {total.toLocaleString("ru-RU")} ={" "}
          {allComponents.map((c) => `${c.rows} (${c.name})`).join(" + ")} +{" "}
          {qTotal} (карантин)
        </div>
        <ul className="mt-3 space-y-1">
          {allComponents.map((c) => (
            <li key={c.name} className="flex items-center gap-2 text-body-sm">
              <CheckCircle2 className="h-4 w-4 text-success" />
              {c.name}: готово · {c.rows} · 0 пропущено
            </li>
          ))}
        </ul>
      </div>

      <p className="text-body-sm text-muted-foreground">
        Все компоненты получают одинаковые колонки и один исходный язык.
      </p>
    </section>
  );
}
