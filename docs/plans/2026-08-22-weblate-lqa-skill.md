# План: Создание скилла weblate-lqa для оценки качества локализации

**Дата:** 2026-08-22
**Цель:** Создать переиспользуемый скилл `weblate-lqa` для Claude Code, Oh My Pi / Codex и OpenCode, реализующий гибридный аудит качества игровой локализации на базе стандарта **MQM-Core Game Profile** и диагностику детерминированных проверок Weblate.

---

## 1. Состав и структура скилла

Скилл создается в `.claude/skills/weblate-lqa/` и зеркалируется в `.omp/skills/weblate-lqa/` и `~/.claude/skills/weblate-lqa/`:

```text
weblate-lqa/
├── SKILL.md                              # Главные инструкции, триггеры и рабочий процесс
├── references/
│   ├── mqm-game-profile.md               # Стандарт типологии ошибок MQM-Core, веса (0/1/5/25), скоринг
│   └── weblate-checks-guide.md           # Каталог проверок Weblate, причины срабатываний и матрица флагов
└── scripts/
    └── audit_component.py                # Безопасный CLI-скрипт выгрузки и скоринга через API/файлы
```

---

## 2. Ключевые возможности скилла

1. **Диагностика детерминированных проверок Weblate (Layer 0):**
   - Выгрузка горящих проверок компонента (`same`, `multiple_capital`, `reused`, `game-markup`, `game-token`, `game-number`, `game-length`, `cyrillic-leak`).
   - Разделение на **True Positives** (реальные ошибки модели/переводчика) и **False Positives** (совпадение форм ед./мн. ч., бренды, омонимы, регистр хоткеев).
   - Формирование точных предписаний по флагам (`ignore-same`, `ignore-multiple-capital`, `ignore-reused`) и исправлениям строк.

2. **Оценка качества по MQM-Core Game Profile (Layer 1):**
   - Типология: `Accuracy` (mistranslation, omission, addition, context hallucination), `Terminology/Lore` (acronym leaks, glossary violation), `Fluency` (grammar, register/tone), `Game/Engine` (placeholders, keybindings, overflow).
   - Взвешивание тяжести: Neutral (0), Minor (1 pt), Major (5 pt), Critical (25 pt / auto-reject).
   - Расчет итогового MQM Score:
     $$\text{MQM Score} = 100 - \left(\frac{\sum \text{Penalty Points}}{\text{Total Words}} \times 100\right)$$
   - Качественные пороги (Quality Gates): Grade A ($\ge 95$), Grade B ($85-94$), Grade C ($70-84$), Fail ($<70$ или наличие неисправленных Critical).

3. **Формирование аналитического отчета (Scorecard):**
   - Сводная таблица метрик (слова, ошибки, штрафы, грейд, статус допуска к релизу).
   - Таблица триажа дефектов с точными контекстами, исходниками, таргетами и предписаниями.
   - Оценка базовой модели перевода (сильные стороны, зоны риска) и рекомендации для LLM-судейства (Two-Seat Judge).

---

## 3. Шаги реализации

1. **Шаг 1: Создание спецификаций в `references/`**
   - `references/mqm-game-profile.md`: полная таксономия, правила скоринга, примеры ошибок для игр.
   - `references/weblate-checks-guide.md`: маппинг `check_id` $\to$ логика проверки $\to$ рекомендуемые флаги.

2. **Шаг 2: Создание скрипта `scripts/audit_component.py`**
   - Чтение токена из безопасного окружения (`deploy/.env.local` или `WEBLATE_API_TOKEN` в env, без вывода секрета в логи).
   - Автоматическая выгрузка всех строк, сбор статистики и группировка `failing_checks` по типам.
   - Базовый эвристический аудит (утечки акронимов, непарные скобки, плейсхолдеры, омонимы).
   - Экспорт в Markdown-отчет и JSON-структуру для дальнейшей обработки.

3. **Шаг 3: Написание `SKILL.md`**
   - Метаданные frontmatter с широкими триггерами (русский и английский): `weblate-lqa`, `lqa audit`, `оценка качества перевода`, `проверь качество локализации`, `скоркарт компонента`, `mqm audit`.
   - Пошаговый алгоритм выполнения аудита (API-выгрузка $\to$ Layer 0 $\to$ Layer 1 $\to$ Scorecard $\to$ Remediation Plan).

4. **Шаг 4: Развертывание и зеркалирование**
   - Создание в `.claude/skills/weblate-lqa/`
   - Копирование в `.omp/skills/weblate-lqa/` и `~/.claude/skills/weblate-lqa/`

5. **Шаг 5: Верификация**
   - Прогон `scripts/audit_component.py` на реальном компоненте `victory-banner/common` (`de`).
   - Проверка генерации отчета и валидации структуры скилла.

---

## 4. Что намеренно вне скоупа (Out of Scope)
- Изменение боевых данных на продакшене без отдельного явного запроса.
- Модификация ядра Weblate в этом плане (скилл использует существующие API и проверяющие модули).

---

Жду утверждения плана для начала реализации.
