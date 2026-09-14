---
version: alpha
name: HCGameLoc Console
description: Визуальная основа HCGameLoc (форк Weblate) — извлечена из текущей темы Bootstrap 5 и подготовлена как контракт токенов для переноса интерфейса на shadcn/ui + Tailwind.
colors:
  background: "#ffffff"
  foreground: "#2a3744"
  card: "#ffffff"
  card-foreground: "#2a3744"
  muted: "#f5f5f5"
  muted-foreground: "#6b7280"
  accent: "#e9eaec"
  accent-foreground: "#2a3744"
  border: "#e9eaec"
  input: "#cccccc"
  ring: "#107a62"
  primary: "#107a62"
  primary-foreground: "#ffffff"
  primary-strong: "#144d3f"
  secondary: "#f5f5f5"
  secondary-foreground: "#2a3744"
  destructive: "#cc3d20"
  destructive-foreground: "#ffffff"
  success: "#158068"
  success-foreground: "#ffffff"
  warning: "#8a6d3b"
  warning-surface: "#fcf8e3"
  info: "#1378d0"
  info-strong: "#0f5fa6"
  info-surface: "#e0eaf1"
  topbar: "#2a3744"
  topbar-foreground: "#bfc3c7"
  topbar-foreground-hover: "#2eccaa"
  highlight-glossary: "#ffffcc"
  dark-background: "#1a1d1e"
  dark-foreground: "#d9d3cc"
  dark-card: "#1e2122"
  dark-muted: "#212324"
  dark-muted-foreground: "#d5d0c7"
  dark-border: "#63696c"
  dark-primary: "#29b396"
  dark-primary-foreground: "#0b1211"
  dark-ring: "#4eeac9"
  dark-topbar: "#25303b"
typography:
  display-lg:
    fontFamily: Source Sans 3
    fontSize: 56px
    fontWeight: "400"
    lineHeight: 64px
    letterSpacing: -0.46px
  metric-lg:
    fontFamily: Source Sans 3
    fontSize: 40px
    fontWeight: "600"
    lineHeight: 50px
    letterSpacing: -0.67px
  heading-page:
    fontFamily: Source Sans 3
    fontSize: 24px
    fontWeight: "600"
    lineHeight: 31px
    letterSpacing: -0.4px
  heading-card:
    fontFamily: Source Sans 3
    fontSize: 18px
    fontWeight: "600"
    lineHeight: 24px
  body-md:
    fontFamily: Source Sans 3
    fontSize: 16px
    fontWeight: "400"
    lineHeight: 24px
  body-sm:
    fontFamily: Source Sans 3
    fontSize: 14px
    fontWeight: "400"
    lineHeight: 18px
  label-strong:
    fontFamily: Source Sans 3
    fontSize: 14px
    fontWeight: "600"
    lineHeight: 18px
  label-caps:
    fontFamily: Source Sans 3
    fontSize: 12px
    fontWeight: "600"
    lineHeight: 16px
    letterSpacing: 0.08em
  code-md:
    fontFamily: Source Code Pro
    fontSize: 14px
    fontWeight: "400"
    lineHeight: 22px
rounded:
  sm: 4px
  DEFAULT: 10px
  lg: 14px
  xl: 20px
  full: 9999px
spacing:
  unit: 4px
  control-x: 12px
  control-y: 8px
  card-padding: 16px
  card-gap: 24px
  section-gap: 40px
components:
  topbar:
    backgroundColor: "{colors.topbar}"
    textColor: "{colors.topbar-foreground}"
    typography: "{typography.body-md}"
    height: 48px
    padding: 0 16px
  topbar-link-hover:
    textColor: "{colors.topbar-foreground-hover}"
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.label-strong}"
    rounded: "{rounded.sm}"
    height: 36px
    padding: 0 16px
  button-primary-hover:
    backgroundColor: "{colors.primary-strong}"
    textColor: "{colors.primary-foreground}"
  button-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.secondary-foreground}"
    typography: "{typography.label-strong}"
    rounded: "{rounded.sm}"
    height: 36px
    padding: 0 16px
  button-destructive:
    backgroundColor: "{colors.destructive}"
    textColor: "{colors.destructive-foreground}"
    typography: "{typography.label-strong}"
    rounded: "{rounded.sm}"
    height: 36px
    padding: 0 16px
  input-field:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    height: 36px
    padding: 0 12px
  input-field-focus:
    backgroundColor: "{colors.background}"
    textColor: "{colors.ring}"
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.card-foreground}"
    rounded: "{rounded.DEFAULT}"
    padding: "{spacing.card-padding}"
  card-header:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-foreground}"
    typography: "{typography.heading-card}"
    padding: 9px 16px
  table-row-hover:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.foreground}"
  metric-label:
    textColor: "{colors.muted-foreground}"
    typography: "{typography.body-sm}"
  editor-source-cell:
    backgroundColor: "{colors.muted}"
    textColor: "{colors.foreground}"
    typography: "{typography.code-md}"
    rounded: "{rounded.sm}"
    padding: 12px
  badge-state-ok:
    backgroundColor: "{colors.success}"
    textColor: "{colors.success-foreground}"
    typography: "{typography.label-caps}"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  alert-warning:
    backgroundColor: "{colors.warning-surface}"
    textColor: "{colors.warning}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.DEFAULT}"
    padding: 12px 16px
  alert-info:
    backgroundColor: "{colors.info-surface}"
    textColor: "{colors.info-strong}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.DEFAULT}"
    padding: 12px 16px
  badge-state-info:
    backgroundColor: "{colors.info}"
    textColor: "{colors.primary-foreground}"
    typography: "{typography.label-caps}"
    rounded: "{rounded.sm}"
    padding: 2px 8px
  term-highlight:
    backgroundColor: "{colors.highlight-glossary}"
    textColor: "{colors.foreground}"
    rounded: "{rounded.sm}"
    padding: 0 2px
  border-hairline:
    backgroundColor: "{colors.border}"
    height: 1px
  input-outline:
    backgroundColor: "{colors.input}"
    height: 1px
  dark-button-primary:
    backgroundColor: "{colors.dark-primary}"
    textColor: "{colors.dark-primary-foreground}"
    typography: "{typography.label-strong}"
    rounded: "{rounded.sm}"
    height: 36px
    padding: 0 16px
  dark-page:
    backgroundColor: "{colors.dark-background}"
    textColor: "{colors.dark-foreground}"
    typography: "{typography.body-md}"
  dark-card:
    backgroundColor: "{colors.dark-card}"
    textColor: "{colors.dark-foreground}"
    rounded: "{rounded.DEFAULT}"
    padding: "{spacing.card-padding}"
  dark-editor-source-cell:
    backgroundColor: "{colors.dark-muted}"
    textColor: "{colors.dark-muted-foreground}"
    typography: "{typography.code-md}"
    rounded: "{rounded.sm}"
    padding: 12px
  dark-topbar:
    backgroundColor: "{colors.dark-topbar}"
    textColor: "{colors.dark-foreground}"
    typography: "{typography.body-md}"
    height: 48px
    padding: 0 16px
  dark-input-field-focus:
    backgroundColor: "{colors.dark-background}"
    textColor: "{colors.dark-ring}"
  dark-border-hairline:
    backgroundColor: "{colors.dark-border}"
    height: 1px
---

## Overview

HCGameLoc — это рабочий инструмент («operate»-режим), а не витрина: переводчик
и редактор проводят в нём часы подряд в плотных таблицах строк, редакторе
перевода и отчётах о проверках. Личность бренда живёт в одной детали —
приглушённом «чернильном» тёмно-синем шапки (`#2a3744`) и изумрудном акценте
(`#107a62`); всё остальное обязано уходить на второй план и не спорить с
содержимым строки перевода.

Источник истины для этого документа — текущая тема:
`weblate/static/styles/variables.css` (токены `--wl-*` и переопределения
`--bs-*`), `weblate/static/styles/main.css` (типографика и компоненты),
`weblate/static/style-dark.css` (тёмная тема) и оболочка
`weblate/templates/base.html` (Bootstrap 5, `navbar.bg-dark`, `container-fluid`).
Токены в шапке файла — это нормативные значения; проза объясняет, как их
применять при переносе на shadcn/ui + Tailwind.

Целевая эстетика после переноса: «тихий продуктовый SaaS» — плоские
поверхности, волосяные границы вместо теней, один акцентный цвет, плотная
вертикальная сетка. Перенос — это **refinement, а не redesign**: палитра,
шрифты и радиусы сохраняются, меняются носитель (Bootstrap → Tailwind),
компонентная база (jQuery-плагины → shadcn/ui) и дисциплина применения.

## Colors

Палитра одноакцентная. Изумрудный `primary` отвечает только за интерактивность
(ссылки, основное действие, фокус и прогресс); статусные цвета — только за
состояние строки или проверки. Никакого декоративного использования цвета.

- **`primary` (#107a62)** — ссылки, основная кнопка, индикатор прогресса,
  кольцо фокуса. Контраст с белым 5.27:1 — проходит WCAG AA для текста.
- **`primary-strong` (#144d3f)** — hover/active и «сильные» ссылки. 9.71:1.
- **`topbar` (#2a3744)** — тёмная шапка приложения и одновременно
  `foreground` для всего текста на светлом фоне (12.15:1). Это единственный
  «тёмный» элемент светлой темы; оставьте её тёмной и в shadcn-оболочке, иначе
  форк теряет узнаваемость.
- **`muted` (#f5f5f5) / `accent` (#e9eaec)** — две ступени серого: первая для
  нередактируемых ячеек исходной строки, вторая для шапок карточек и hover
  строк таблицы. Больше оттенков серого вводить нельзя.
- **`destructive` (#cc3d20)** 4.93:1 и **`success` (#158068)** 4.86:1 на белом —
  на границе AA: применимы к тексту от 14px и к заливке значков, но не к
  тонкому 12px-тексту.
- **`info` (#1378d0)** — только заливка (бейдж, иконка): как текст на
  `info-surface` (#e0eaf1) он даёт 3.72:1 и AA не проходит. Текст
  информационного блока — `info-strong` (#0f5fa6), 5.36:1.
- **`warning` (#8a6d3b) на `warning-surface` (#fcf8e3)** — 4.54:1. Пара
  неразрывна: коричневый текст warning нельзя класть на белое поле карточки.
- **`highlight-glossary` (#ffffcc)** — подсветка термина глоссария прямо в
  тексте строки. Это семантика продукта, а не украшение.

### Тёмная тема

Тёмная тема — не инверсия, а отдельный набор значений (сейчас
`style-dark.css`, после переноса — `.dark { … }` в Tailwind). Она задана
теми же токенами с префиксом `dark-` и такими же парами компонентов
(`dark-button-primary`, `dark-card`, …), поэтому линтер контрастов
проверяет её наравне со светлой:

| Токен | Светлая | Тёмная |
|:--|:--|:--|
| `background` | `#ffffff` | `#1a1d1e` |
| `card` | `#ffffff` | `#1e2122` |
| `muted` | `#f5f5f5` | `#212324` |
| `foreground` | `#2a3744` | `#d9d3cc` |
| `muted-foreground` | `#6b7280` | `#d5d0c7` |
| `border` | `#e9eaec` | `#63696c` (было `#484d50`) |
| `primary` | `#107a62` | `#29b396` |
| `primary-foreground` | `#ffffff` | `#0b1211` |
| `ring` | `#107a62` | `#4eeac9` |
| `topbar` | `#2a3744` | `#25303b` |

Два значения текущей тёмной темы исправлены здесь намеренно.
`--wl-color-primary` `#1c8f75` даёт с белым текстом 4.01:1 (AA не проходит),
поэтому `dark-button-primary` — это `#29b396` с почти чёрным текстом
`#0b1211` (7.2:1), и белый текст на изумрудной кнопке в тёмной теме
запрещён. Граница `#484d50` на `#1a1d1e` даёт 1.98:1 при требуемых 3:1 для
нетекстовых элементов, поэтому `dark-border` поднят до `#63696c` (3.04:1);
прежнее значение допустимо только как разделитель внутри одной поверхности.

## Typography

Два семейства, уже загружаемые локально (`weblate_fonts/source-sans-3.css`,
`weblate_fonts/source-code-pro.css`), остаются: **Source Sans 3** для
интерфейса и **Source Code Pro** для кода, плейсхолдеров, diff и разметки
движка. Никаких веб-шрифтов со стороны — инстанс работает без внешних CDN.

- Базовый размер — 16px/24px (`body-md`). Плотные таблицы и метаданные —
  `body-sm` 14px; ниже 12px текст не опускается.
- Шкала намеренно короткая: `display-lg` (56px) живёт только на публичной
  странице вовлечения, `metric-lg` (40px) — в карточках метрик,
  `heading-page` (24px) — `h1` страницы, `heading-card` (18px) — заголовок
  карточки. Промежуточных размеров не вводить.
- `label-strong` (14/600) — подписи полей формы. Текущая тема ставит
  `label { font-weight: 700 }` глобально; при переносе это правило заменяется
  явным классом, иначе жирным становится любая подпись, включая
  вспомогательные.
- `label-caps` (12/600, `0.08em`, uppercase) — бейджи, имена проверок,
  колоночные заголовки. Только для коротких меток; на фразу из трёх слов
  капс не ставится.
- Строка перевода и исходник всегда рендерятся `code-md`: моноширинный
  шрифт — часть корректности, он делает видимыми `$`, `%KEY%`, `{0}` и
  парные пробелы.

## Layout

Оболочка: фиксированная тёмная шапка (48px) + `container-fluid` во всю
ширину. Полноширинная раскладка сохраняется: таблицы строк и редактор
перевода упираются в ширину экрана, поэтому центрированный контейнер
фиксированной ширины при переносе не вводится.

- Сетка 4px (`spacing.unit`); реальные шаги — 8, 12, 16, 24, 40.
- `card-gap` 24px между карточками, `section-gap` 40px между смысловыми
  блоками страницы (`.container-gapped`).
- Плотность — приоритет: строка таблицы `padding-y` 8px, контрол высотой
  36px. Комфортные 44px-контролы shadcn по умолчанию уменьшают экран на
  несколько строк и не применяются.
- Точки останова остаются бутстраповскими (`sm 576`, `md 768`, `lg 992`,
  `xl 1200`, `2xl 1400`). Текущие утилиты `zero-width-*`, скрывающие колонки
  таблицы по ширине, переносятся как отзывчивые классы видимости
  (`hidden xl:table-cell`), а не как отдельные CSS-правила.
- RTL — обязателен (`dir="rtl"` на `<html>`, отдельный вендорный CSS).
  В Tailwind это означает логические свойства (`ps-*`, `pe-*`, `text-start`)
  вместо `left/right` без исключений.

## Elevation & Depth

Глубина почти не используется, и это сознательно: рабочая плоскость плоская.

- Уровень 0 — страница (`background`), разделение только границей 1px
  `border`.
- Уровень 1 — карточка: граница + радиус 10px, **без тени**.
- Уровень 2 — всплывающие слои (dropdown, popover, поиск-превью, toast):
  `0 2px 10px rgba(0,0,0,0.1)`, радиус 10px. Это единственная разрешённая
  тень интерфейса.
- Уровень 3 — модальное окно: тот же оттенок тени с большим радиусом
  размытия и подложка `rgba(0,0,0,0.5)`.
- Поле ввода несёт внутреннюю тень `inset 0 1px 1px rgba(0,0,0,0.075)` —
  наследие Bootstrap; при переносе она убирается, глубину поля задаёт
  только граница `input`.

## Shapes

- `rounded.sm` 4px — контролы: кнопки, поля, бейджи, вкладки-пилюли,
  прогресс-бар.
- `rounded.DEFAULT` 10px — карточки, аккордеон, панели, всплывающие слои.
- `rounded.lg` 14px / `rounded.xl` 20px — только крупные промо-поверхности
  публичных страниц.
- `rounded.full` — аватары и счётчики-пилюли.
- Текущие «капсульные» кнопки (`.btn-info` с `border-radius: 20px`,
  `height: 50px`) — реликт маркетинговой страницы, попавший в приложение.
  В целевом интерфейсе все кнопки приложения используют `rounded.sm`;
  капсула остаётся только на странице вовлечения.

## Components

### Кнопки

Иерархия из трёх уровней и не больше одной `primary` на экранную область:
`primary` (изумруд) — подтверждающее действие; `secondary` (серая заливка) —
всё остальное; `ghost` (только текст) — действия внутри строки таблицы.
`destructive` — только необратимые операции, всегда с подтверждением.
Текущая тема противоречит сама себе: `.btn-primary` залит чернильным
`#2a3744`, а `.btn-warning` переопределён в зелёный `#158068` — при переносе
цвет кнопки определяется её ролью, а не историческим классом Bootstrap.

### Таблицы строк

Основной экран работы. Заголовок колонки — `label-caps` на `card-header`;
строка — `body-sm`; hover — `accent`; выбранная строка — левая граница 2px
`primary`. Состояние строки (`needs editing`, `translated`, `approved`)
передаётся бейджем и иконкой, никогда одной лишь заливкой фона.

### Редактор перевода

Исходная строка — `editor-source-cell` (серый фон, моноширинный шрифт,
только чтение), цель — поле того же размера на `background`. Провалившиеся
проверки перечисляются под полем списком: имя проверки — `label-caps`,
пояснение — `body-sm`, действие («игнорировать») — ghost-кнопка справа.
Подсветка глоссария (`term-highlight`) и подсветка плейсхолдеров рендерятся
поверх текста и не меняют его метрики.

### Формы

Метка сверху (`label-strong`), поле 36px, подсказка `body-sm`
`muted-foreground`, ошибка `body-sm` `destructive` с иконкой. Ошибка всегда
привязана к полю через `aria-describedby`; красной рамки без текста не
бывает.

### Карта переноса Bootstrap → shadcn/ui

| Сейчас | Цель | Примечание |
|:--|:--|:--|
| `.card` + `.card-header` | `Card` / `CardHeader` | шапка — `accent`, без тени |
| `.nav-pills` | `Tabs` | активная вкладка `primary-strong` |
| `.list-group.check` | `Accordion` / `Alert` | одна проверка = одна запись |
| `.dropdown-menu` | `DropdownMenu` | тень уровня 2 |
| Tom Select | `Command` + `Popover` | серверный поиск остаётся |
| `.modal` | `Dialog` / `AlertDialog` | `AlertDialog` для необратимого |
| `.toast-container` | `Sonner`/`Toaster` | позиция и `aria-live` сохраняются |
| `.progress` 8px | `Progress` | высота 8px, радиус 4px |
| `.badge` | `Badge` | `label-caps`, радиус 4px |
| `.tooltip` (jQuery) | `Tooltip` | доступен с клавиатуры |

### Tailwind v4

Токены переносятся один к одному в `@theme` (`--color-*`, `--radius-*`,
`--text-*`), тёмная тема — переопределением тех же переменных в `.dark`.
Серверные шаблоны Django продолжают отдавать разметку, поэтому классы
пишутся в шаблонах, а не генерируются в рантайме: динамических строк вида
`bg-${color}-500` быть не должно — Tailwind их не соберёт.

## Do's and Don'ts

**Do**

- Держать один акцент: изумруд = интерактивность, остальные цвета = статус.
- Проверять пару «фон/текст» на 4.5:1 (текст) и 3:1 (границы, иконки)
  до коммита; в тёмной теме это правило нарушают именно границы.
- Дублировать любое цветовое состояние формой: иконкой, бейджем, текстом.
  Проверки, состояния строк и diff читают и при дальтонизме.
- Использовать логические свойства и переводить каждую видимую строку
  (`{% translate %}`); длина немецкой или русской метки в 1.5 раза больше
  английской — макет обязан это выдерживать.
- Оставлять видимый фокус: кольцо 2px `ring` с отступом 2px на каждом
  интерактивном элементе, включая строки таблицы.

**Don't**

- Не использовать `#2eccaa` как кольцо фокуса на светлом фоне: 2.03:1 против
  требуемых 3:1. Текущее значение `--wl-focus-color` — дефект, а не образец.
- Не добавлять теней к карточкам и таблицам: плоскость — часть плотности.
- Не вводить новые оттенки серого, радиусы или размеры шрифта вне шкалы.
- Не заменять моноширинный шрифт в исходной/целевой строке пропорциональным.
- Не превращать рабочие экраны в маркетинговые: градиенты, стекло,
  крупные иллюстрации и капсульные кнопки остаются на публичных страницах.
- Не полагаться на hover: всё, что доступно мышью, доступно с клавиатуры.
