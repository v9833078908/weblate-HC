"use client";

import { Info, Search } from "lucide-react";
import * as React from "react";
import { useWizard } from "@/components/console/wizard/WizardShell";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";

function Q({ n, title, children }) {
  return (
    <div className="rounded border border-border p-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-muted text-label-caps text-muted-foreground">
          {n}
        </span>
        <h3 className="text-label-strong text-foreground">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function Radio({ name, checked, onChange, children }) {
  return (
    <label className="flex items-center gap-2 py-1 text-body-sm text-foreground">
      <input type="radio" name={name} checked={checked} onChange={onChange} />
      {children}
    </label>
  );
}

export function Step6Profile() {
  const { s, set } = useWizard();
  const [ev, setEv] = React.useState(null);
  const [q, setQ] = React.useState("");
  const [titles, setTitles] = React.useState([]);
  const [bdhc, setBdhc] = React.useState(null);
  const p = s.profile;
  const setP = (patch) =>
    set((prev) => ({ profile: { ...prev.profile, ...patch } }));

  React.useEffect(() => {
    api.getTextEvidence("u").then(setEv);
  }, []);
  React.useEffect(() => {
    api.getBdhcTitles(q).then(setTitles);
  }, [q]);

  const cjkNeeded = (s.languages || []).some(
    (l) => l.include && ["ja", "ko"].includes(l.resolved_as || l.code),
  );

  const selectBdhc = async (t) => {
    const prof = await api.getBdhcProfile(t.id);
    setBdhc({ ...t, ...prof });
    setP({ bdhc_title_id: t.id, skipped_q1: false });
  };

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-heading-card text-foreground">Профиль проекта</h2>
        <p className="mt-1 text-body-sm text-muted-foreground">
          Ответы задают тон перевода и служат опорой для судьи. Промпты
          формируются автоматически.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Q n={1} title="Игра">
            <div className="relative">
              <Search
                className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Карточка игры в БДХК"
                className="h-9 ps-9"
              />
            </div>
            <ul className="mt-2 divide-y divide-border rounded-sm border border-border">
              {titles.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => selectBdhc(t)}
                    className={cn(
                      "flex w-full items-center justify-between p-2.5 text-start hover:bg-muted",
                      bdhc?.id === t.id && "bg-accent",
                    )}
                  >
                    <span className="text-body-sm text-foreground">
                      {t.title}
                    </span>
                    <span className="text-body-sm text-muted-foreground">
                      {t.has_brief ? "бриф" : "нет брифа"}
                      {t.has_voice ? " · стиль" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {bdhc?.brief && bdhc.voice ? (
              <div className="mt-3 flex items-start gap-2 rounded border border-info-surface bg-info-surface p-3 text-info-strong">
                <Info className="mt-0.5 h-4 w-4 shrink-0" />
                <span className="text-body-sm">
                  Профиль проекта импортирован из карточки игры в БДХК: жанр,
                  сеттинг, тон, обращение к игроку.
                </span>
              </div>
            ) : bdhc ? (
              <div className="mt-3 space-y-2">
                <p className="text-body-sm text-muted-foreground">
                  В карточке нет блоков «бриф» и «голос и стиль» — ответьте на
                  вопросы ниже.
                </p>
                {["Жанр", "Сеттинг", "Роль игрока", "Аудитория / возраст"].map(
                  (f) => (
                    <div key={f}>
                      <Label className="text-body-sm">{f}</Label>
                      <Input
                        className="mt-1 h-9"
                        onChange={(e) => setP({ [f]: e.target.value })}
                      />
                    </div>
                  ),
                )}
                <div>
                  <Label className="text-body-sm">
                    Ссылка на страницу игры в сторе (или название и краткое
                    описание)
                  </Label>
                  <Input
                    className="mt-1 h-9"
                    value={p.store_url}
                    onChange={(e) => setP({ store_url: e.target.value })}
                  />
                </div>
              </div>
            ) : null}
          </Q>

          <Q n={2} title="Обращение к игроку в интерфейсе">
            <Radio
              name="rui"
              checked={p.register_ui === "ты"}
              onChange={() => setP({ register_ui: "ты" })}
            >
              На «ты»
            </Radio>
            <Radio
              name="rui"
              checked={p.register_ui === "вы"}
              onChange={() => setP({ register_ui: "вы" })}
            >
              На «вы»
            </Radio>
            <Radio
              name="rui"
              checked={p.register_ui === "formal"}
              onChange={() => setP({ register_ui: "formal" })}
            >
              Формально
            </Radio>
            {ev && (
              <div className="mt-2 space-y-1 text-body-sm text-muted-foreground">
                {ev.samples.ui.map((x) => (
                  <div key={x.key} className="font-mono">
                    {x.text}
                  </div>
                ))}
              </div>
            )}
          </Q>

          <Q n={3} title="Обращение в диалогах">
            <Radio
              name="rd"
              checked={p.register_dialogue === "by_speaker"}
              onChange={() => setP({ register_dialogue: "by_speaker" })}
            >
              Как в оригинале (по говорящему)
            </Radio>
            <Radio
              name="rd"
              checked={p.register_dialogue === "ty"}
              onChange={() => setP({ register_dialogue: "ty" })}
            >
              Всегда на «ты»
            </Radio>
            <Radio
              name="rd"
              checked={p.register_dialogue === "vy"}
              onChange={() => setP({ register_dialogue: "vy" })}
            >
              Всегда на «вы»
            </Radio>
            {ev && (
              <div className="mt-2 space-y-1 text-body-sm text-muted-foreground">
                {ev.samples.dialogue.map((x) => (
                  <div key={x.key}>
                    <span className="text-foreground">{x.character}:</span>{" "}
                    {x.text}
                  </div>
                ))}
              </div>
            )}
          </Q>

          <Q n={4} title="Мат и грубость сохраняем в силе источника?">
            <Radio
              name="prof"
              checked={p.profanity === "keep"}
              onChange={() => setP({ profanity: "keep" })}
            >
              Сохранять в силе источника — не смягчать и не добавлять
            </Radio>
            <Radio
              name="prof"
              checked={p.profanity === "soften"}
              onChange={() =>
                setP({
                  profanity: "soften",
                  profanity_level: p.profanity_level || "light",
                })
              }
            >
              Смягчать
            </Radio>
            {p.profanity === "soften" && (
              <div className="ms-6 mt-1 flex gap-2">
                <button
                  type="button"
                  onClick={() => setP({ profanity_level: "light" })}
                  className={cn(
                    "rounded-sm border px-2 py-1 text-body-sm",
                    p.profanity_level === "light"
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border",
                  )}
                >
                  до лёгкой грубости
                </button>
                <button
                  type="button"
                  onClick={() => setP({ profanity_level: "none" })}
                  className={cn(
                    "rounded-sm border px-2 py-1 text-body-sm",
                    p.profanity_level === "none"
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border",
                  )}
                >
                  без мата
                </button>
              </div>
            )}
            {ev && (
              <div className="mt-2 text-body-sm text-muted-foreground">
                Строк с грубой лексикой: {ev.profanity_rows}. В существующем
                переводе смягчено: «{ev.samples.softened[0].src}» → «
                {ev.samples.softened[0].tgt}».
              </div>
            )}
          </Q>

          {cjkNeeded && (
            <Q n={5} title="Вежливость для японского и корейского">
              <Radio
                name="cjk"
                checked={p.cjk_politeness === "as_shipped"}
                onChange={() => setP({ cjk_politeness: "as_shipped" })}
              >
                Как в существующих переводах (измерим)
              </Radio>
              <Radio
                name="cjk"
                checked={p.cjk_politeness === "neutral"}
                onChange={() => setP({ cjk_politeness: "neutral" })}
              >
                Нейтрально-вежливая речь везде
              </Radio>
              <Radio
                name="cjk"
                checked={p.cjk_politeness === "mixed"}
                onChange={() => setP({ cjk_politeness: "mixed" })}
              >
                Интерфейс нейтрально, диалоги по говорящему
              </Radio>
              {ev && (
                <div className="mt-2 text-body-sm text-muted-foreground">
                  Сейчас в ките: ja — {ev.shipped_register.ja}; ko —{" "}
                  {ev.shipped_register.ko}.
                </div>
              )}
            </Q>
          )}

          <div className="rounded border border-border p-4 text-body-sm text-muted-foreground">
            Запрещённые слова и обязательные формы —{" "}
            <button
              type="button"
              className="text-primary hover:underline"
              onClick={() => {}}
            >
              Задать в глоссарии →
            </button>
          </div>
        </div>

        <aside className="lg:sticky lg:top-16 lg:self-start">
          <div className="rounded border border-border bg-muted p-4">
            <div className="mb-2 text-label-caps uppercase text-muted-foreground">
              Что мы измерили в ките
            </div>
            {ev ? (
              <div className="space-y-1 text-body-sm text-foreground">
                <div>
                  {ev.rows.toLocaleString("ru-RU")} строк ·{" "}
                  {Math.round(ev.short_label_share * 100)}% короткие подписи
                </div>
                <div>{ev.dialogue_rows} реплик диалогов</div>
                <div>
                  разметка:{" "}
                  {Object.entries(ev.markup)
                    .map(([k, v]) => `${k} ${v}`)
                    .join(", ")}
                </div>
                <div>
                  плейсхолдеры:{" "}
                  {Object.entries(ev.placeholders)
                    .map(([k, v]) => `${k} ${v}`)
                    .join(", ")}
                </div>
                <div>
                  разделитель $: {ev.separators.dollar} · буквальный \n:{" "}
                  {ev.separators.newline}
                </div>
                <div>
                  строк с .!? на конце:{" "}
                  {Math.round(ev.terminal_punct_share * 100)}%
                </div>
                <div>
                  пояснения заполнены:{" "}
                  {Math.round(ev.explanation_coverage * 100)}%
                </div>
                <p className="pt-2 text-muted-foreground">
                  Профиль составляется только из этих измерений и ваших ответов
                  — ничего не выдумываем.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-4 rounded" />
                ))}
              </div>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}
