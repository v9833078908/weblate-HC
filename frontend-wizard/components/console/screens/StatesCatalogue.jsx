"use client";

import {
  CheckCircle2,
  FolderPlus,
  Radio,
  ShieldOff,
  XCircle,
} from "lucide-react";
import { AppShell } from "@/components/console/AppShell";
import {
  EmptyState,
  KindBadge,
  StatusBadge,
  WeblateLink,
} from "@/components/console/primitives";
import { DropZone } from "@/components/console/upload-parts";
import { GlossaryWorkspace } from "@/components/console/wizard/GlossaryWorkspace";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

function Tile({ title, children, wide }) {
  return (
    <div className={wide ? "lg:col-span-2" : ""}>
      <div className="rounded border border-border bg-card">
        <div className="border-b border-border bg-accent px-4 py-2 text-label-caps uppercase text-muted-foreground">
          {title}
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

function Group({ title, children }) {
  return (
    <div className="mt-8">
      <h2 className="mb-3 text-heading-card text-foreground">{title}</h2>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">{children}</div>
    </div>
  );
}

const WarnBox = ({ children }) => (
  <div className="rounded border border-warning bg-warning-surface p-3 text-body-sm text-warning">
    {children}
  </div>
);
const ErrBox = ({ title, text, action }) => (
  <div className="flex items-start justify-between gap-3 rounded border border-destructive bg-background p-4">
    <div className="flex items-start gap-3">
      <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
      <div>
        <div className="text-label-strong text-foreground">{title}</div>
        <div className="text-body-sm text-muted-foreground">{text}</div>
      </div>
    </div>
    {action}
  </div>
);

// 19 universal-wizard states. Numbers match the product spec.
// 17 & 18 are run/result states rendered inline; the rest open in the wizard.
const UNI = [
  [
    1,
    "kit_ok",
    1,
    "Лок-кит: успешное распознавание",
    "Валидный XLSX → тип «Лок-кит», строки, языки, готовность и карантин; «Посмотреть анализ» открывает боковую панель.",
  ],
  [
    2,
    "steam_txt",
    1,
    "Steam: три TXT-файла",
    "steam_short/about/legal → Steam, русские названия полей, символы, BBCode, лимиты Steamworks.",
  ],
  [
    3,
    "gplay_zip",
    1,
    "Google Play: архив",
    "metadata.zip со структурой ru-RU → поля title/short/full, лимиты автоматически.",
  ],
  [
    4,
    "appstore_folder",
    1,
    "App Store: папка",
    "Папка с пятью каноническими TXT → 5 полей App Store, символы и лимиты.",
  ],
  [
    5,
    "custom_unknown",
    1,
    "Другая площадка: неизвестные TXT",
    "Неизвестные имена → уточнение: название поля, лимит (или «нет»), источник, разметка.",
  ],
  [
    6,
    "ambiguous_one",
    1,
    "Один неоднозначный description.txt",
    "Таблица / поле стора / другое; при «поле стора» — площадка и поле в той же карточке.",
  ],
  [
    7,
    "two_stores",
    1,
    "Steam + Google Play в одной загрузке",
    "Нашли два набора → создать два отдельных набора / выбрать вручную / другой архив.",
  ],
  [
    8,
    "existing_translations",
    2,
    "В архиве уже есть fr и de",
    "«в файлах уже есть перевод» + сводка: сохраним 2, переведём остальные; не перезаписываем.",
  ],
  [
    9,
    "source_incomplete",
    2,
    "Исходный комплект неполный (en)",
    "Английский 2 из 3 полей → карточка недоступна, «Далее» заблокировано, подставки нет.",
  ],
  [
    10,
    "ambiguous_locales",
    2,
    "Неоднозначные региональные локали",
    "pt / es / zh → выбор варианта (pt-BR/pt-PT, es-ES/es-419, zh-Hans/zh-Hant).",
  ],
  [
    11,
    "over_limit_source",
    1,
    "Исходный текст превышает лимит стора",
    "title.txt 38/30 → превышение выделено, «Заменить файл», без автообрезки.",
  ],
  [
    12,
    "broken_bbcode",
    1,
    "Сломанная Steam BBCode-разметка",
    "Незакрытый [b] в «Об игре» → фрагмент с выделенным тегом, ручная замена.",
  ],
  [
    13,
    "empty_txt",
    1,
    "Пустой TXT-файл",
    "Пустое поле нельзя перевести; для необязательного — удалить или заменить.",
  ],
  [
    14,
    "unknown_in_known",
    1,
    "Неизвестный файл в известном наборе",
    "promo_extra.txt → поле из схемы / дополнительное поле / не импортировать.",
  ],
  [
    15,
    "duplicate_field",
    1,
    "Два файла назначены одному полю",
    "Две карточки сравнения (символы, превью, дата, разметка) → выбрать один.",
  ],
  [
    16,
    "unreadable_zip",
    1,
    "Архив не читается или небезопасен",
    "Повреждён / пароль / небезопасный путь → импорт не выполнен, частичного нет.",
  ],
  [17, "store_run_done", "run", "Перевод стор-текстов завершён", ""],
  [
    18,
    "target_over_limit",
    "run",
    "Target превышает лимит → выгружается пустым",
    "",
  ],
  [
    19,
    "glossary_store",
    3,
    "Извлечение терминов из стор-текстов",
    "Карточка «Предложить термины из загруженных файлов» + этапы под площадку.",
  ],
];

function StoreRunDone() {
  return (
    <div className="space-y-3">
      <div>
        <div className="text-heading-card text-foreground">
          Локализация Steam готова
        </div>
        <div className="text-body-sm text-muted-foreground">
          3 поля · 8 языков · 24 перевода · Проверки пройдены
        </div>
      </div>
      <ol className="space-y-1 text-body-sm">
        {[
          "Подготовка файлов",
          "Языки",
          "Глоссарий",
          "Перевод",
          "Проверка лимитов и разметки",
          "Сборка ZIP",
        ].map((s) => (
          <li key={s} className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4 text-success" />
            {s}
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap items-center gap-2">
        <Button className="h-9 rounded-sm">Скачать ZIP</Button>
        <Button variant="secondary" className="h-9 rounded-sm">
          Поля по языкам
        </Button>
        <WeblateLink url="#" />
      </div>
    </div>
  );
}

function TargetOverLimit() {
  return (
    <div className="space-y-3">
      <div className="flex gap-3">
        <div className="flex-1 rounded border border-border p-3">
          <div className="text-metric-lg tabular-nums text-foreground">
            7 / 8
          </div>
          <div className="text-body-sm text-muted-foreground">
            языков готовы
          </div>
        </div>
        <div className="flex-1 rounded border border-warning bg-warning-surface p-3">
          <div className="text-metric-lg tabular-nums text-warning">1</div>
          <div className="text-body-sm text-warning">поле требует решения</div>
        </div>
      </div>
      <div className="rounded border border-border p-3">
        <div className="flex items-center gap-2">
          <KindBadge kind="blocking" />
          <span className="text-body-sm text-foreground">
            Французский · Краткое описание
          </span>
        </div>
        <div className="mt-1 text-body-sm tabular-nums text-destructive">
          96 из 80 символов
        </div>
        <div className="text-body-sm text-muted-foreground">
          Перевод превышает лимит Google Play на 16 символов. Поле выгружается
          пустым — ключ сохранён.
        </div>
        <div className="mt-2 flex items-center gap-2">
          <Button className="h-8 rounded-sm">Исправить с помощью AI</Button>
          <WeblateLink url="#" />
        </div>
      </div>
      <p className="text-body-sm text-muted-foreground">
        «Принять как есть» отсутствует. ZIP и сводка сообщают, что одно значение
        выгружено пустым; остальные языки доступны.
      </p>
    </div>
  );
}

export function StatesCatalogueScreen({ navigate }) {
  return (
    <AppShell
      navigate={navigate}
      breadcrumb={[{ label: "Проекты", path: "/" }, { label: "/dev/states" }]}
    >
      <h1 className="mb-1 text-heading-page text-foreground">
        Каталог состояний
      </h1>
      <p className="text-body-sm text-muted-foreground">
        Все пустые, загрузочные и ошибочные состояния рядом для ревью.
      </p>

      <Group title="Статусы и типы решений">
        <Tile title="Статусы прогонов и языков">
          <div className="flex flex-wrap gap-2">
            <StatusBadge status="ready" />
            <StatusBadge status="completed" />
            <StatusBadge status="translating" />
            <StatusBadge status="running" />
            <StatusBadge status="queued" />
            <StatusBadge status="decisions" />
            <StatusBadge status="stalled" />
            <StatusBadge status="error" />
          </div>
        </Tile>
        <Tile title="Типы решений">
          <div className="flex flex-wrap gap-2">
            <KindBadge kind="blocking" />
            <KindBadge kind="judge_critical" />
            <KindBadge kind="judge_note" />
          </div>
        </Tile>
      </Group>

      <Group title="Пусто · загрузка · ошибки">
        <Tile title="Пусто: поиск проектов">
          <EmptyState
            icon={FolderPlus}
            title="Ничего не найдено"
            description="По вашему запросу проектов нет."
          />
        </Tile>
        <Tile title="Пусто: всё решено">
          <EmptyState
            icon={CheckCircle2}
            title="Всё решено"
            description="9 из 9 языков готовы к выгрузке."
          />
        </Tile>
        <Tile title="Загрузка: скелетон таблицы">
          <div className="space-y-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-10 rounded-sm" />
            ))}
          </div>
        </Tile>
        <Tile title="Загрузка: разбор файла">
          <DropZone
            onPick={() => {}}
            parsing
            fileName="pirate-ships-loc-kit.xlsx"
          />
        </Tile>
        <Tile title="Ошибка: прогон остановлен">
          <ErrBox
            title="Прогон остановлен с ошибкой"
            text="Модель вернула ошибку аутентификации (401). Незавершённые строки не потеряны."
            action={
              <Button className="h-9 rounded-sm">
                Перезапустить незавершённое
              </Button>
            }
          />
        </Tile>
        <Tile title="Нет обновлений">
          <div className="flex items-start gap-3 rounded border border-input bg-background p-4">
            <Radio className="mt-0.5 h-5 w-5 shrink-0 animate-pulse text-muted-foreground" />
            <div>
              <div className="text-label-strong text-foreground">
                Нет обновлений
              </div>
              <div className="text-body-sm text-muted-foreground">
                Обработчик не отвечает 4 мин; незавершённые строки не потеряны.
              </div>
            </div>
          </div>
        </Tile>
        <Tile title="Судья не настроен">
          <div className="flex items-start gap-3 rounded border border-border bg-muted p-4">
            <ShieldOff className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
            <div>
              <div className="text-label-strong text-foreground">
                Проверка качества недоступна
              </div>
              <div className="text-body-sm text-muted-foreground">
                Администратор AI-инструментов ещё не настроил судью.
              </div>
            </div>
          </div>
        </Tile>
        <Tile title="Ошибка выгрузки">
          <ErrBox
            title="Не удалось собрать файл"
            text="Попробуйте ещё раз через несколько минут."
          />
        </Tile>
      </Group>

      <Group title="Мастер · состояния по шагам">
        <Tile title="Шаг 1 · несколько листов">
          <div className="space-y-2">
            <WarnBox>
              В книге 3 листа. Одна загрузка создаёт один компонент — выберите
              лист.
            </WarnBox>
            <div className="flex items-center justify-between rounded-sm border border-border px-3 py-2 text-body-sm">
              <span className="font-mono">Strings</span>
              <span className="text-muted-foreground">3 120 строк</span>
            </div>
          </div>
        </Tile>
        <Tile title="Шаг 1 · файл не читается">
          <ErrBox
            title="Не удалось прочитать файл"
            text="Пришлите экспорт в CSV/TSV/XLSX или опишите формат. Частичный разбор не делаем."
          />
        </Tile>
        <Tile title="Шаг 1 · нет колонки ключа">
          <WarnBox>Не нашли колонку ключа. Укажите её.</WarnBox>
        </Tile>
        <Tile title="Шаг 1 · расширение лжёт">
          <div className="rounded-sm bg-info-surface p-3 text-body-sm text-info-strong">
            Расширение .txt, но по содержимому это TSV в UTF-16LE с BOM.
            Прочитали корректно.
          </div>
        </Tile>
        <Tile title="Шаг 2 · исходный язык не совпадает">
          <WarnBox>
            Вы выбрали английский как исходный, но колонка en заполнена на 41%
            (1 584 из 3 864 строк) — как оригинал она не годится.
          </WarnBox>
          <div className="mt-2 flex gap-2">
            <Button className="h-8 rounded-sm">Выбрать русский</Button>
            <Button variant="secondary" className="h-8 rounded-sm">
              Загрузить другой кит
            </Button>
          </div>
        </Tile>
        <Tile title="Шаг 4 · проверка импортом: не готово">
          <ErrBox
            title="Проверка импортом: не готово"
            text="строка 141: ожидается исправленный кит от разработчиков"
          />
        </Tile>
        <Tile title="Шаг 5 · нет признака группировки">
          <WarnBox>
            В ките нет признака группировки: ключи не образуют семейств, листов
            и колонки группы нет. Загрузите список от движка или оставьте один
            компонент.
          </WarnBox>
        </Tile>
        <Tile title="Шаг 6 · карточка без блоков">
          <div className="rounded border border-border p-3 text-body-sm text-muted-foreground">
            В карточке нет блоков «бриф» и «голос и стиль» — ответьте на вопросы
            ниже.
          </div>
        </Tile>
      </Group>

      <Group title="Извлечение терминов · раскадровка">
        <Tile title="1 · До извлечения (живой)" wide>
          <GlossaryWorkspace slug="pirate-ships" uploadId="demo" mode="page" />
        </Tile>
        <Tile title="2 · Извлечение идёт">
          <GlossaryWorkspace
            slug="pirate-ships"
            uploadId="demo"
            mode="page"
            forceState="running"
          />
        </Tile>
        <Tile title="3 · Модель не настроена">
          <GlossaryWorkspace
            slug="pirate-ships"
            uploadId="demo"
            mode="page"
            forceState="model_off"
          />
        </Tile>
        <Tile title="4 · Ошибка извлечения">
          <GlossaryWorkspace
            slug="pirate-ships"
            uploadId="demo"
            mode="page"
            forceState="failed"
          />
        </Tile>
        <Tile title="5 · Термины не найдены">
          <GlossaryWorkspace
            slug="pirate-ships"
            uploadId="demo"
            mode="page"
            forceState="empty"
          />
        </Tile>
        <Tile title="6 · Предложения (добавить/спорные/контекст)" wide>
          <GlossaryWorkspace
            slug="pirate-ships"
            uploadId="demo"
            mode="page"
            forceState="ready"
          />
        </Tile>
        <Tile title="7 · Частичная публикация" wide>
          <GlossaryWorkspace
            slug="pirate-ships"
            uploadId="demo"
            mode="page"
            forceState="partial"
          />
        </Tile>
      </Group>

      <Group title="Универсальный мастер · 19 состояний">
        {UNI.map(([num, key, stage, title, desc]) => (
          <Tile
            key={key}
            title={`${num}. ${title}`}
            wide={stage === "run" || num === 5 || num === 15}
          >
            {key === "store_run_done" ? (
              <StoreRunDone />
            ) : key === "target_over_limit" ? (
              <TargetOverLimit />
            ) : (
              <div className="space-y-3">
                <p className="text-body-sm text-muted-foreground">{desc}</p>
                <Button
                  variant="secondary"
                  className="h-9 rounded-sm"
                  onClick={() =>
                    navigate(
                      `/projects/novyy-proekt/localize?scenario=${key}&stage=${stage}`,
                    )
                  }
                >
                  Открыть в мастере →
                </Button>
              </div>
            )}
          </Tile>
        ))}
      </Group>

      <div className="mt-8 flex flex-wrap gap-3">
        <Button
          variant="secondary"
          className="h-9 rounded-sm"
          onClick={() => navigate("/emails/completion")}
        >
          Письмо: завершение
        </Button>
        <Button
          variant="secondary"
          className="h-9 rounded-sm"
          onClick={() => navigate("/emails/failure")}
        >
          Письмо: ошибка
        </Button>
      </div>
    </AppShell>
  );
}
