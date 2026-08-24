# Безопасное пополнение существующего глоссария из loc-kit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Продюсер добавляет свежую loc-kit таблицу в существующий glossary-компонент; Weblate добавляет только действительно новые термины и их descriptions/notes, никогда не меняет существующие термины и успешно обрабатывает частично заполненные языки.

**Architecture:** Входной конвейер фазы 1 остаётся прежним: временный черновик → детерминированный профиль или ручная коррекция → `validate_glossary_profile` → предпросмотр. Для обновления не применяется `Translation.handle_upload`: его режим `add` не умеет пропускать пустые target-значения и исторически теряет source/target explanations. Вместо этого отдельный application-service в `weblate/trans/loc_kit.py` работает только через штатные `Component.add_new_language`, `Translation.add_unit`, `Unit.update_explanation` и `Unit.update_extra_flags`; прямых записей в БД и собственного обхода истории нет.

Сервис сначала под блокировкой компонента строит строгий набор новых терминов. Идентичность термина — `(context, source)`. Совпадающая идентичность означает «старый термин»: его target, descriptions и flags не меняются. Тот же `source` в новом `context` — не «новый термин», а конфликт, который блокирует применение до решения человеком. Для каждого нового термина добавляются только непустые target-значения. Отсутствующая колонка или пустая ячейка не делают импорт ошибкой; они отражаются в итоговом отчёте как пропуски. Если в таблице есть непустые значения для языка, которого ещё нет в глоссарии, сервис создаёт этот язык при наличии `translation.add`; если право или политика компонента не позволяют создать язык, пропускается только этот язык.

**Tech Stack:** Django, `weblate/trans/views/create.py`, `weblate/trans/loc_kit.py`, `Translation.add_unit`, `Unit.update_explanation`, `Component.add_new_language`, `loc_kit_ingest`.

**Status (2026-08-15): реализован и проверен.** Append-only application
service, partial-language handling, collision protection, terminology sync,
and the update UI are covered by contract and focused tests.

**Dependency:** фаза 1 (`docs/product/plans/2026-08-10-loc-kit-glossary-deterministic-infer.md`) должна быть влита. План использует `_infer_draft_profile`, `_store_validated_profile`, auto-skip выбора листа и `validate_glossary_profile`.

## Product contract

- **Append-only по умолчанию и без альтернативного overwrite-режима.** Это не инструмент синхронизации таблицы с Weblate. Уже существующая запись не меняется даже когда её target пустой, а в таблице он заполнен.
- **Успех частичного импорта.** Пустой target в строке, отсутствующая колонка языка или невозможность создать один дополнительный язык не отменяют добавление пригодных данных для других языков. Итог показывает разбивку по языкам.
- **Новые языки.** Если колонка языка распознана, содержит хотя бы один пригодный target нового термина и её ещё нет в глоссарии, создать translation через `Component.add_new_language`. Для этого нужны `translation.add` и право добавлять glossary entries; ограниченный пользователь может выбрать только язык из того же queryset, что стандартная форма Weblate, а `translation.add_more` снимает это ограничение. Без права, допустимого языка или политики компонента пропускается только этот язык с объяснением, остальные продолжаются.
- **Descriptions/notes — часть нового термина.** Непустое source explanation сохраняется на source unit; непустое target explanation — на соответствующем target unit. Отсутствие колонки или ячейки explanation допустимо и не блокирует добавление термина. У существующих терминов explanations никогда не меняются этим потоком.
- **Омонимы не добавляются молча.** Если в текущем глоссарии уже есть тот же source с другим context, применение останавливается с ограниченным списком конфликтов. Это предотвращает два конкурирующих значения одного слова в подсказках.
- **Терминология распространяется.** После появления хотя бы одного нового source unit он получает флаг `terminology`, затем запускается обычная sync-задача. Она создаст структурные пустые пары в языках без перевода; это не импорт перевода и не перезапись старых данных.
- **Атомарность содержимого.** После предварительной валидации операции добавления unit и explanations выполняются в одной DB-транзакции под стандартным `Component.locked_for_update()` (`repository → DB row`). Если добавление term data падает, новые units не остаются частично записанными и черновик не consume-ится. Создание нового языкового файла — отдельная штатная VCS-операция, поэтому выполняется и фиксируется в отдельной фазе до content transaction. При любой последующей content-ошибке сервис компенсирующе удаляет только созданные этим вызовом пустые language translations через штатный `Translation.remove`; неудача компенсации не скрывается, логируется как error и оставляет draft для оператора.
- **Поддерживаемая форма частичного файла.** Update flow использует тот же schema v2 `record-map`, который пропускает `validate_glossary_profile`; `grammar.allow_empty_targets=True` разрешает пустые target cells. Профиль v1 `term-description-pairs` этим UI не принимается и не является отдельным режимом обновления.

## Non-goals

- Не обновлять существующие target, descriptions, flags, states или контекст из таблицы.
- Не очищать существующий перевод пустой ячейкой.
- Не объединять два листа в один import.
- Не заявлять в UI, что словарь «полный» по всем языкам: результат сообщает, какие языки и ячейки были пропущены.
- Не вводить повторно `handle_upload(..., method="translate")`, чекбокс overwrite или user-visible «sync changes from table» в этой задаче.

## Result terminology

Финальный экран и сообщение используют следующие независимые счётчики для каждого языка:

- `added` — создана новая target-пара нового термина;
- `existing` — термин уже существовал в source glossary, поэтому полностью пропущен;
- `blank` — target-ячейка в таблице пуста;
- `absent` — такой target language уже есть в глоссарии, но в таблице нет его колонки;
- `unavailable` — таблица содержит данные, но language translation не удалось создать (право, фильтр, alias или VCS-ошибка).

Набор строк результата — объединение target languages таблицы и уже существующих target translations глоссария. Для уже существующего source term приоритет имеет `existing` для каждой колонки языка таблицы: значение ячейки не читается и не очищается. Для нового source term `blank` означает пустую ячейку, `unavailable` — непустую ячейку, которую нельзя применить, а `added` — реально созданную target-пару. У языка глоссария без колонки в таблице единственный результат — `absent`.

Итог прямо поясняет, что `blank`/`absent` не импортируют перевод. После terminology sync Weblate может создать для такого языка пустую техническую пару нового source term, чтобы поддерживать структуру глоссария; это не перенос значения из таблицы и не изменение существующего term.

Черновик consume-ится, когда обработка закончилась без content-ошибки и хотя бы один language был применим. Импорт «0 добавлено, всё уже существовало» также является корректным идемпотентным завершением. Если нет ни одного применимого языка из-за пустых ячеек, отсутствия права или недоступных языков, черновик остаётся на preview-странице с пояснением, чтобы оператор мог исправить таблицу или получить право.

---

### Task 1: привязать update-черновик к целевому glossary-компоненту

**Files:**

- Modify: `weblate/trans/models/loc_kit.py`
- Create: `weblate/trans/migrations/0099_loc_kit_draft_target_component.py`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: написать падающие модельные тесты.**

Добавить `LocKitUpdateDraftModelTest`:

```python
class LocKitUpdateDraftModelTest(ViewTestCase):
    CREATE_GLOSSARIES: bool = True

    def test_draft_binds_to_existing_glossary(self) -> None:
        glossary = self.project.glossaries[0]
        draft = LocKitImportDraft.objects.create(
            owner=self.user,
            session_key="x" * 10,
            project=self.project,
            slug=glossary.slug,
            name=glossary.name,
            source_filename="Terms.csv",
            target_component=glossary,
        )
        self.assertEqual(draft.target_component, glossary)

    def test_deleting_target_glossary_deletes_its_draft(self) -> None:
        glossary = self.project.glossaries[0]
        LocKitImportDraft.objects.create(
            owner=self.user,
            session_key="x" * 10,
            project=self.project,
            slug=glossary.slug,
            name=glossary.name,
            source_filename="Terms.csv",
            target_component=glossary,
        )
        glossary.delete()
        self.assertFalse(LocKitImportDraft.objects.exists())
```

**Step 2: убедиться, что тест падает.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitUpdateDraftModelTest -n0`

Expected: FAIL — `target_component` ещё не является полем модели.

**Step 3: добавить nullable foreign key.**

После `category` добавить:

```python
    target_component = models.ForeignKey(
        "trans.Component",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="loc_kit_update_drafts",
    )
```

Creation draft оставляет поле `NULL`; update draft всегда задаёт существующий glossary.

**Step 4: создать и проверить миграцию.**

Run: `./rundev.sh manage makemigrations trans --name loc_kit_draft_target_component`

Expected: migration `0099_loc_kit_draft_target_component.py` с единственным `AddField`.

**Step 5: проверить тест.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitUpdateDraftModelTest -n0`

Expected: PASS.

**Step 6: commit.**

```bash
git add weblate/trans/models/loc_kit.py weblate/trans/migrations/0099_loc_kit_draft_target_component.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): bind update drafts to a glossary"
```

---

### Task 2: проверять доступ к update-черновику по праву загрузки

**Files:**

- Modify: `weblate/trans/views/create.py`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: написать падающие тесты доступа.**

Добавить `LocKitGlossaryUpdateGateTest`, который для target draft проверяет:

- отсутствие `upload.perform` даёт 404;
- locked glossary даёт 404;
- draft, повреждённо указывающий на не-glossary component, даёт 404;
- пользователь с `upload.perform`, но без `translation.add`, может открыть preview: это право потребуется только если конкретный язык нужно создать.
- пользователь с `upload.perform`, но без `glossary.add` / `unit.add`, может открыть preview, но при apply не получает обход прав: каждый язык будет `unavailable`, а новый unit не появится.

**Step 2: запустить тесты.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryUpdateGateTest -n0`

Expected: FAIL — текущий mixin знает только component-creation flow.

**Step 3: разделить gates в `LocKitDraftMixin.get_draft`.**

До проверки `get_creatable_projects` добавить ветку update draft:

```python
        if draft.target_component_id is not None:
            component = draft.target_component
            if (
                not component.is_glossary
                or component.locked
                or not self.request.user.has_perm("upload.perform", component)
            ):
                raise Http404
            return draft
```

Не требовать `translation.add` здесь: отсутствие этого права не лишает оператора возможности добавить данные в уже существующие языки.

**Step 4: проверить тесты и существующий flow создания.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k "UpdateGate or GlossaryUploadUI" -n0`

Expected: PASS.

**Step 5: commit.**

```bash
git add weblate/trans/views/create.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): gate glossary update drafts on upload access"
```

---

### Task 3: дать оператору вход из существующего глоссария

**Files:**

- Modify: `weblate/trans/forms.py`
- Modify: `weblate/trans/views/create.py`
- Modify: `weblate/urls.py`
- Modify: `weblate/templates/component.html`
- Create: `weblate/templates/trans/loc_kit_glossary_update.html`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: написать падающий UI-тест.**

Добавить тест, который POST-ит CSV в `loc-kit-glossary-update`, проверяет `draft.target_component`, одно-листовой auto-skip и редирект на preview. Отдельно проверить, что пользователь без upload permission получает 404 на GET и POST.

**Step 2: запустить тест.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k UpdateStart -n0`

Expected: FAIL — route и view ещё не существуют.

**Step 3: добавить форму.**

Рядом с `LocKitProfileCorrectionForm` добавить `LocKitGlossaryUpdateForm`:

```python
class LocKitGlossaryUpdateForm(forms.Form):
    table = forms.FileField(
        label=gettext_lazy("Loc-kit table (CSV, TSV, XLSX)"),
        help_text=gettext_lazy(
            "The table is mapped locally and previewed before anything is "
            "added to the glossary. Existing terms are never changed."
        ),
        validators=[
            validate_component_zip_upload_size,
            FileExtensionValidator(allowed_extensions=["csv", "tsv", "xlsx"]),
        ],
        widget=forms.FileInput(attrs={"accept": ".csv,.tsv,.xlsx"}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
```

**Step 4: добавить start view и URL.**

`LocKitGlossaryUpdateStartView` должен повторять чтение, storage и auto-sheet logic существующего glossary create flow, но брать component через `parse_path`, проверять `is_glossary`, `locked` и `upload.perform`, а затем сохранять `target_component=component` в draft.

Добавить маршрут:

```python
path(
    "loc-kit/glossary/update/<object_path:path>/",
    weblate.trans.views.create.LocKitGlossaryUpdateStartView.as_view(),
    name="loc-kit-glossary-update",
),
```

**Step 5: добавить template и ссылку.**

Template владеет единственным `<form method="post" enctype="multipart/form-data">` и применяет `{% crispy form %}`. Текст должен говорить, что до preview ничего не записано и существующие термины не изменятся.

В Files menu `component.html` показать ссылку только когда `object.is_glossary` и есть `upload.perform`:

```html
<a class="dropdown-item"
   href="{% url 'loc-kit-glossary-update' path=object.get_url_path %}">
  {% translate "Add new glossary terms from a loc-kit table" %}
</a>
```

**Step 6: проверить UI-тесты и отсутствие вложенных forms.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k "UpdateStart or stage_templates_never_nest_a_form" -n0`

Expected: PASS.

**Step 7: commit.**

```bash
git add weblate/trans/forms.py weblate/trans/views/create.py weblate/urls.py weblate/templates/component.html weblate/templates/trans/loc_kit_glossary_update.html weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): stage glossary append tables"
```

---

### Task 4: сохранить все валидированные термины для применения, не расширяя UI-preview

**Files:**

- Modify: `weblate/trans/loc_kit.py`
- Test: `weblate/trans/tests/test_loc_kit_profile_suggester.py`

`GlossaryPreview.terms` намеренно ограничен `PREVIEW_TERM_LIMIT`, поэтому применять его нельзя: большая таблица потеряла бы все строки после preview лимита. Добавить отдельное in-memory поле `all_terms`, содержащее полный tuple нормализованных `GlossaryTerm`; оно не сериализуется в `draft.preview_json` и не выдаётся UI.

**Step 1: написать падающий тест.**

Создать profile с `PREVIEW_TERM_LIMIT + 1` терминами. Проверить:

```python
preview = validate_glossary_profile(...)
self.assertLen(preview.terms, PREVIEW_TERM_LIMIT)
self.assertLen(preview.all_terms, PREVIEW_TERM_LIMIT + 1)
```

**Step 2: запустить тест.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_profile_suggester.py -k FullTerms -n0`

Expected: FAIL — `all_terms` отсутствует.

**Step 3: расширить dataclass и результат валидатора.**

В `GlossaryPreview` добавить после `terms`:

```python
    all_terms: tuple[GlossaryTerm, ...]
```

Импорт `GlossaryTerm` держать в `TYPE_CHECKING`; благодаря `from __future__ import annotations` runtime import не нужен. В `validate_glossary_profile` передать `tuple(glossary_terms)` в это поле. Оставить `terms` ограниченным, как сейчас.

**Step 4: проверить тест.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_profile_suggester.py -k FullTerms -n0`

Expected: PASS.

**Step 5: commit.**

```bash
git add weblate/trans/loc_kit.py weblate/trans/tests/test_loc_kit_profile_suggester.py
git commit -m "refactor(loc-kit): retain validated terms for glossary append"
```

---

### Task 5: реализовать append-only application service с частичными языками и notes

**Files:**

- Modify: `weblate/trans/loc_kit.py`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: написать падающие unit/contract tests.**

Создать тесты для публичной функции сервиса, а не для view:

1. existing source с пустым target не меняется;
2. translated и approved existing source не меняются;
3. новый source с English и Polish target создаёт обе пары;
4. blank Polish target не создаёт Polish translation и считает `blank`, но English добавляется;
5. отсутствующая польская колонка не блокирует English;
6. непустая Japanese колонка создаёт отсутствующую Japanese translation и добавляет term;
7. без `translation.add`, без `translation.add_more` для запрещённого языка, без `glossary.add` и без `unit.add` Japanese считается `unavailable`, а English всё равно добавляется, когда его права достаточны;
8. source explanation и target explanation сохраняются на новом term;
9. отсутствующие notes не блокируют новый term;
10. incoming notes не меняют notes уже существующего term;
11. тот же source с другим context в Weblate **и два новых ряда с тем же source в разных contexts** поднимают collision и ничего не добавляют;
12. существующий target language без колонки получает `absent`; строка с blank cell получает `blank`; existing term увеличивает `existing` для соответствующих языков;
13. искусственная ошибка при добавлении второго term откатывает все новые units и не возвращает success result;
14. lock timeout не consume-ит draft и даёт повторяемое сообщение; повторный POST успешного apply не может добавить term второй раз;
15. созданный Japanese language компенсирующе удаляется, если следующая content transaction падает; неуспех самой компенсации записывается в error log и заметно возвращается оператору.

Проверять meanings, а не только число units: source explanation должен быть на `target_unit.source_unit`, target explanation — на `target_unit.explanation`.

**Step 2: запустить новый класс.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryAppendServiceTest -n0`

Expected: FAIL — сервиса ещё нет.

**Step 3: добавить типы результата и исключение конфликта в `weblate/trans/loc_kit.py`.**

Добавить immutable result types, не зависящие от view:

```python
@dataclass(frozen=True)
class GlossaryLanguageAppendResult:
    added: int = 0
    existing: int = 0
    blank: int = 0
    absent: bool = False
    unavailable: str = ""


@dataclass(frozen=True)
class GlossaryAppendResult:
    languages: Mapping[str, GlossaryLanguageAppendResult]
    added_terms: int


class GlossaryAppendCollisionError(Exception):
    """A source is already known under another glossary context."""
```

Хранить в exception только capped list `(source, existing_context, incoming_context)` для безопасного UI-сообщения; не помещать полный файл в message.

**Step 4: реализовать preflight и подобрать разрешённые языки.**

Добавить `append_glossary_terms(request, component, preview) -> GlossaryAppendResult`. Функция должна:

```python
with component.locked_for_update() as locked_component:
    existing_keys = set(
        locked_component.source_translation.unit_set.values_list(
            "context", "source"
        )
    )
    existing_sources = {source for _context, source in existing_keys}
    incoming_sources = set()
    new_terms = []
    collisions = []
    for term in preview.all_terms:
        key = (term.context, term.values[preview.source_language])
        if key in existing_keys:
            # This logical term is old. Do not fill targets, notes or flags.
            continue
        if key[1] in existing_sources or key[1] in incoming_sources:
            collisions.append((key[1], ...))
            continue
        incoming_sources.add(key[1])
        new_terms.append(term)
    if collisions:
        raise GlossaryAppendCollisionError(...)
```

`new_terms` — глобальный source-level набор. Никогда не определять «новизну» отдельно в каждом target language: иначе добавление польского target к старому source фактически изменило бы старый термин, хотя UI называл бы это append-only. Обязательно в result учесть `existing` для каждого языка и `absent=True` для target translation глоссария, которой нет среди `preview.target_languages`.

**Step 5: разрешить языки независимо друг от друга и создать новые отдельной фазой.**

Сначала добавить в result все уже существующие target languages глоссария, которых нет в `preview.target_languages`: для них выставить `absent=True`. Затем для каждого `preview.target_languages` посчитать terms с `term.values[code].strip()`. Если таких нет, записать `blank` и не создавать language translation. Так итог отличает «в файле нет японской колонки» от «японская колонка есть, но в этой строке перевод пуст».

Для уже существующей translation обязательно проверять:

```python
request.user.has_perm("unit.add", translation)
```

Без `unit.add` язык получает `unavailable`; `Translation.add_unit` никогда не вызывается только потому, что есть `upload.perform`.

Для отсутствующего target language сначала проверить `translation.add` и `glossary.add` на project. Разрешённый `Language` выбирать точно как стандартная Weblate form:

```python
languages = component.get_all_available_languages()
if not request.user.has_perm("translation.add_more", component):
    languages = languages.filter_for_add(component.project)
language = languages.filter(code=code).first()
```

Если `language is None`, право отсутствует либо `component.can_add_new_language(request.user)` ложно, записать `unavailable` без VCS-записи. Не вызывать `add_new_language` для колонки, где нет непустых targets новых terms.

С созданием языка нельзя обещать одну DB-атомарность с content append: `add_new_language` создаёт файл и VCS commit. Поэтому выполнить его в отдельной первой фазе под `component.locked_for_update()` и сохранить `created_by_this_apply`. После создания повторно проверить `request.user.has_perm("unit.add", translation)`; defensive failure немедленно компенсировать `translation.remove(request.user)` и записать `unavailable`. Ошибки/timeout `add_new_language` локально превратить в `unavailable` конкретного языка, не начав content mutation других языков.

**Step 6: добавить новые terms штатными model methods и сохранить descriptions.**

Открыть вторую, content-фазу через `component.locked_for_update()`; использовать возвращённый fresh component и заново построить `existing_keys`/collision set, чтобы конкурентное изменение между фазами не могло создать дубликат. В этой фазе выполнить всё добавление в единственной DB transaction. Не захватывать вручную `component.lock` и не смешивать собственный порядок locks с `locked_for_update`; `WeblateLockTimeoutError` пробрасывается в view как retryable failure без consume draft.

Для каждого `new_term` выбрать все разрешённые target translations, где target непуст. Если такого языка нет, не создавать source unit: term останется пропущенным, а не source-only записью. Увеличить `blank` только для пустой ячейки нового term, `existing` — только для term, существовавшего до apply, а `added` — после реально созданной target пары.

Для первой разрешённой translation вызвать:

```python
target_unit = translation.add_unit(
    request,
    new_term.context,
    source,
    target,
    explanation=new_term.target_explanations.get(code, ""),
)
assert target_unit is not None
target_unit.source_unit.update_explanation(
    new_term.source_explanation, request.user
)
```

Для остальных непустых targets того же `new_term` вызвать тот же `add_unit` с соответствующим `target explanation`; source explanation второй раз не вызывать. Эти методы создают штатные `NEW_UNIT` и `EXPLANATION` changes, pending changes и VCS-совместимое состояние. Не использовать `QuerySet.update`, `bulk_create`, собственный SQL или `Translation.handle_upload`.

После записи всех непустых targets нового source:

```python
source_unit = target_unit.source_unit
flags = Flags(source_unit.extra_flags)
flags.merge("terminology")
source_unit.update_extra_flags(flags.format(), request.user)
```

Никогда не записывать explanation или flags, если source был в `existing_keys`: это ключевая защита старых terms.

**Step 7: завершить транзакцию и запланировать terminology sync.**

Если появились новые source terms, после успешного content transaction commit вызвать:

```python
transaction.on_commit(component.schedule_sync_terminology)
```

Не вызывать задачу, если импорт добавил 0 term data. При content exception попытаться штатно удалить каждый пустой language translation из `created_by_this_apply`; если компенсация тоже падает, отправить exception в `report_error`/проектный log и вернуть отдельную user-facing ошибку без ложного success. Проверить, что background sync создаёт необходимые пустые structural pairs и не меняет уже заполненные target units.

**Step 8: запустить тесты класса.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryAppendServiceTest -n0`

Expected: PASS.

**Step 9: commit.**

```bash
git add weblate/trans/loc_kit.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): append glossary terms without overwriting existing data"
```

---

### Task 6: применить append service из preview view и показать честный результат

**Files:**

- Modify: `weblate/trans/views/create.py`
- Modify: `weblate/templates/trans/loc_kit_glossary_preview.html`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: написать падающие integration tests preview/apply.**

Покрыть через HTTP:

- один новый term импортируется, draft удаляется и сообщение содержит added count;
- existing empty target остаётся пустым;
- partial English/Polish table возвращает success с `blank` count;
- table с Japanese создаёт японскую translation и сообщает это;
- отсутствующее право `translation.add` не отменяет English import и показывает unavailable Japanese;
- collision ведёт обратно на preview, draft и storage сохраняются, DB не меняется;
- notes на новом term существуют в UI/DB после apply;
- profile с notes показывает положительный текст о переносе descriptions, а не старое предупреждение об их потере.

**Step 2: запустить тесты.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k UpdateApply -n0`

Expected: FAIL — action `apply` отсутствует.

**Step 3: реализовать `action == "apply"`.**

В `LocKitGlossaryPreviewView.post` до ветки `confirm`:

1. получить `draft.target_component`, проверить `PREVIEW_READY`;
2. повторно прочитать sheet и вызвать `validate_glossary_profile` с `component_name=component.slug`;
3. проверить совпадение source language; mismatch остаётся блокирующей ошибкой;
4. вызвать `append_glossary_terms(request, component, preview)`;
5. `GlossaryAppendCollisionError` показать через messages и вернуть на этот preview без consume;
6. если нет применимых языков, показать result как ошибку/информацию и не consume draft;
7. при завершении записать общий и per-language outcome в messages, consume draft, удалить storage и redirect на component.

Не импортировать `BytesIO`, `NamedBytesIO`, `Path` или `Translation.handle_upload` в update apply path: TBX остаётся обязательным parse-back artefact validation, а application service получает нормализованные `preview.all_terms`.

**Step 4: изменить template.**

В update-ветке preview:

- заголовок кнопки — `Add new terms`;
- удалить checkbox `overwrite` и строки о `refreshed`;
- перед кнопкой показать contract: existing terms and their notes will not change; blank targets are skipped;
- если `preview.note_count`, показать positive alert: `Descriptions from this table will be added to new glossary terms. Existing terms and descriptions are not changed.`;
- после redirect message выводит table/list per-language counts added, existing, blank, absent, unavailable;
- сохранить единственный `<form>` и CSRF.

**Step 5: проверить integration tests и HTML.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k "UpdateApply or stage_templates_never_nest_a_form" -n0`

Expected: PASS.

**Step 6: commit.**

```bash
git add weblate/trans/views/create.py weblate/templates/trans/loc_kit_glossary_preview.html weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): apply append-only glossary updates"
```

---

### Task 7: проверить terminology sync и языковые границы end-to-end

**Files:**

- Modify: `weblate/trans/tests/test_loc_kit_ingest_contract.py`
- Modify: `weblate/glossary/tests.py` only if existing helpers need a narrow regression test

**Step 1: написать падающий regression test.**

В eager Celery test settings:

1. добавить новый English-only term через append service;
2. убедиться, что source unit помечен `terminology`;
3. добавить в проект новый обычный component language;
4. выполнить terminology sync;
5. убедиться, что глоссарий получил structural pair для нового term, не изменив существующий translated target;
6. для Japanese, созданного из input table, убедиться, что его target не пуст и explanation сохранён.

**Step 2: запустить тест.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k TerminologySync -n0`

Expected: FAIL до вызова `update_extra_flags(... terminology ...)` и scheduling.

**Step 3: завершить минимальную реализацию, если test выявил пропуск.**

Корректировать только Task 5 service: не менять Weblate-wide semantics `sync_terminology` и не добавлять новый Celery task.

**Step 4: проверить test.**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k TerminologySync -n0`

Expected: PASS.

**Step 5: commit.**

```bash
git add weblate/trans/tests/test_loc_kit_ingest_contract.py weblate/glossary/tests.py
git commit -m "test(loc-kit): cover glossary append terminology sync"
```

Do not stage `weblate/glossary/tests.py` if it was not changed.

---

### Task 8: обновить документацию и acceptance criteria

**Files:**

- Modify: `docs/changes.rst`
- Modify: `docs/guides/loc-kit-ingest.md`
- Modify: `AGENTS.md`

**Step 1: добавить changelog entry.**

В unreleased section одним предложением сообщить: existing glossary can receive append-only loc-kit terms; existing data stays intact; non-empty new language columns can create glossary languages.

**Step 2: обновить loc-kit spec.**

Добавить после glossary creation flow отдельную секцию «Append-only update of an existing glossary» с точным product contract:

- точка входа Files меню glossary;
- preview и повторная валидация;
- identity `(context, source)`;
- existing terms, their target и notes не меняются;
- blank cells and absent columns are partial-success skips;
- automatic creation of a missing target language with `translation.add`;
- per-language outcome; no false claim of global completeness;
- explanations of new terms persist when present;
- source mismatch и same-source/different-context conflict block apply;
- `terminology` sync keeps terms usable in later components.

**Step 3: обновить `AGENTS.md`.**

В описании `loc_kit_ingest` / Weblate-side glossary surface заменить утверждение о простом table workflow на append-only contract и notes preservation. Не менять unrelated project guidance.

**Step 4: перечитать изменённые тексты.**

Run: `git diff --check && git diff -- docs/changes.rst docs/guides/loc-kit-ingest.md AGENTS.md`

Expected: no whitespace errors; documentation does not describe overwrite or loss of explanations.

**Step 5: commit.**

```bash
git add docs/changes.rst docs/guides/loc-kit-ingest.md AGENTS.md
git commit -m "docs(loc-kit): describe append-only glossary updates"
```

---

### Task 9: полный прогон и ручной producer smoke

**Step 1: targeted tests.**

Run:

```bash
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -n0
./rundev.sh test weblate/trans/tests/test_loc_kit_profile_suggester.py -n0
```

Expected: PASS.

**Step 2: lint and format.**

Run:

```bash
uv run prek run --files weblate/trans/models/loc_kit.py weblate/trans/loc_kit.py weblate/trans/views/create.py weblate/trans/forms.py weblate/templates/trans/loc_kit_glossary_preview.html weblate/templates/trans/loc_kit_glossary_update.html weblate/templates/component.html weblate/urls.py weblate/trans/tests/test_loc_kit_ingest_contract.py weblate/trans/tests/test_loc_kit_profile_suggester.py
```

Expected: PASS.

**Step 3: manual smoke on the local dev instance.**

On `http://localhost:3001/`, as `admin`/`admin`, open an existing glossary and use Files → `Add new glossary terms from a loc-kit table`.

1. Upload a table with one brand-new English/Polish term, a blank Polish cell for another new term, and an already-existing term whose Weblate target is intentionally empty.
2. Confirm preview says existing data will not change and descriptions will be imported when present.
3. Apply it by clicking the button normally, not submitting the form programmatically.
4. Verify: new nonempty translations appear; the blank Polish target was skipped; existing empty term stayed empty; any description from the table is visible on the new term.
5. Upload a table with nonempty Japanese data when Japanese is absent from the glossary. Verify the Japanese glossary translation is created and has the imported term.
6. Upload a table with an already-known source in a different section/context. Verify apply is blocked and no term changes.
7. Create or open a later project component in a relevant language. Verify the terminology sync makes new glossary source terms available without changing existing choices.

**Step 4: final review and commit status.**

Run:

```bash
git status --short
git log --oneline -9
```

Expected: plan-owned implementation changes are committed; pre-existing unrelated files remain untouched and are neither staged nor included in commits.

**Step 5: push the implementation branch.**

Run: `git push`

Expected: remote accepts all commits.
