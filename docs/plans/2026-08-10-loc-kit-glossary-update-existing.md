# План: обновление существующего глоссария из loc-kit таблицы

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Свежий экспорт таблицы терминов вливается в уже работающий glossary-компонент: новые термины добавляются, изменённые обновляются, правки переводчиков по умолчанию не затираются.

**Architecture:** Переиспользуется весь конвейер фазы 1 (draft → детерминированный вывод профиля → `validate_glossary_profile` → превью). Расходится только финальный шаг: вместо создания компонента отрендеренные TBX подаются в штатный `Translation.handle_upload` по каждому языку — двумя проходами, `translate` (обновить существующие) затем `add` (создать недостающие). Никакого своего пути записи в БД: история, атрибуция и права — штатные.

**Tech Stack:** Django, `weblate/trans/views/create.py`, `Translation.handle_upload`, `loc_kit_ingest`.

**Зависимость:** фаза 1 (`docs/plans/2026-08-10-loc-kit-glossary-deterministic-infer.md`) должна быть влита: план опирается на `_infer_draft_profile`, `_store_validated_profile(..., extra_warnings=)` и auto-skip выбора листа.

**Ключевые решения (не пересматривать по ходу):**
- **Консервативный дефолт.** `conflicts=""` — уже переведённый термин не трогается (`translation.py:1749-1754`). Затирание включается явным чекбоксом и только при праве `upload.overwrite`.
- **Без собственного pre-flight дифа.** Отброшено сознательно: любой самописный подсчёт «новых/изменённых» разойдётся с реальным результатом, потому что Weblate матчит юниты по `(context, source)` внутри `handle_add_upload` (`translation.py:2017-2048`). Показываем фактические счётчики после применения — они авторитетны и бесплатны.
- **Два прохода на язык, порядок `translate` → `add`.** Обратный порядок даёт бессмысленные счётчики: только что созданные термины стали бы «пропущенными» на втором проходе.
- **Explanations через upload не переносятся** — задокументированное ограничение Weblate (`test_translation_upload_does_not_carry_explanations`, строки 331-345). Детерминированный вывод фазы 1 их и не производит; в UI показать предупреждение, если профиль их содержит.
- Имена файлов TBX — Weblate-коды языков (`writer.py:185`, `f"{target_code}.tbx"`), маппинг `Path(name).stem` → `translation_set.get(language__code=...)` корректен, включая `zh_Hans`.

**Общие правила исполнения:** контейнерные тесты `./rundev.sh test <path> -n0`; после правок `loc_kit_ingest/*.py` — `cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/`; линтеры и полный сьют — один раз в конце.

---

### Task 1: `target_component` в модели черновика

**Files:**
- Modify: `weblate/trans/models/loc_kit.py:53-64`
- Create: `weblate/trans/migrations/0099_loc_kit_draft_target_component.py`
- Test: `weblate/trans/tests/test_loc_kit_ingest_contract.py`

**Step 1: падающий тест** — в конец файла:

```python
class LocKitUpdateDraftModelTest(ViewTestCase):
    CREATE_GLOSSARIES: bool = True

    def test_draft_binds_to_a_target_component(self) -> None:
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

    def test_deleting_the_glossary_deletes_its_drafts(self) -> None:
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

**Step 2: убедиться, что падает**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitUpdateDraftModelTest -n0`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'target_component'`

**Step 3: поле модели** — после `category` (`loc_kit.py:56-62`):

```python
    # Set only for an update draft: the existing glossary this table refreshes.
    # A creation draft leaves it NULL and keeps using project/category/slug.
    target_component = models.ForeignKey(
        "trans.Component",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="loc_kit_update_drafts",
    )
```

**Step 4: миграция**

Run: `./rundev.sh manage makemigrations trans --name loc_kit_draft_target_component`
Проверить, что файл получил номер `0099` и содержит только `AddField`.

**Step 5: тесты и коммит**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitUpdateDraftModelTest -n0` → PASS

```bash
git add weblate/trans/models/loc_kit.py weblate/trans/migrations/0099_loc_kit_draft_target_component.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): bind an import draft to an existing glossary"
```

---

### Task 2: гейт доступа для update-черновика

Черновик обновления не должен пускать по праву «создавать компоненты»: нужен доступ на загрузку в целевой глоссарий.

**Files:**
- Modify: `weblate/trans/views/create.py` — `LocKitDraftMixin.get_draft`

**Step 1: падающие тесты** — новый класс:

```python
class LocKitGlossaryUpdateGateTest(ViewTestCase):
    CREATE_GLOSSARIES: bool = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary = self.project.glossaries[0]

    def _draft(self, **kwargs):
        return LocKitImportDraft.objects.create(
            owner=self.user,
            session_key=self.client.session.session_key or "",
            project=self.project,
            slug=self.glossary.slug,
            name=self.glossary.name,
            source_filename="Terms.csv",
            target_component=self.glossary,
            **kwargs,
        )

    def test_user_without_upload_permission_gets_404(self) -> None:
        draft = self._draft()
        self.user.is_superuser = False
        self.user.save()
        self.project.all_users("Administration").delete()
        response = self.client.get(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token})
        )
        self.assertEqual(response.status_code, 404)

    def test_locked_component_gets_404(self) -> None:
        draft = self._draft()
        self.glossary.locked = True
        self.glossary.save(update_fields=["locked"])
        response = self.client.get(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token})
        )
        self.assertEqual(response.status_code, 404)

    def test_non_glossary_target_gets_404(self) -> None:
        draft = self._draft()
        draft.target_component = self.component
        draft.save(update_fields=["target_component"])
        response = self.client.get(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token})
        )
        self.assertEqual(response.status_code, 404)
```

Замечание для исполнителя: `self.user` в `ViewTestCase` по умолчанию администратор проекта; в первом тесте права снимаются явно. Если снятие прав через `all_users("Administration")` не сработает в этой версии — снять через `self.project.remove_user(self.user)` и проверить, что 404 действительно из-за прав, а не из-за сессии.

**Step 2: убедиться, что падают**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py::LocKitGlossaryUpdateGateTest -n0`
Expected: FAIL (сейчас гейт смотрит только на `get_creatable_projects`, у администратора проекта оно проходит).

**Step 3: реализация** — в `LocKitDraftMixin.get_draft`, перед текущей проверкой creatable-projects:

```python
        if draft.target_component_id is not None:
            component = draft.target_component
            # An update draft is exactly as powerful as the ordinary upload
            # form on that glossary: same permission, same lock.
            if (
                not component.is_glossary
                or component.locked
                or not self.request.user.has_perm("upload.perform", component)
            ):
                raise Http404
            return draft
```

**Step 4: PASS + весь класс глоссарного UI не сломан**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k "UpdateGate or GlossaryUploadUI" -n0`

**Step 5: commit**

```bash
git add weblate/trans/views/create.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): gate update drafts on the glossary upload permission"
```

---

### Task 3: точка входа — загрузка таблицы в существующий глоссарий

**Files:**
- Modify: `weblate/trans/forms.py` (рядом с `LocKitProfileCorrectionForm`, ~строка 3021)
- Modify: `weblate/trans/views/create.py` (новый view после `LocKitDraftMixin`)
- Create: `weblate/templates/trans/loc_kit_glossary_update.html`
- Modify: `weblate/urls.py` (рядом с остальными loc-kit маршрутами, ~строка 352)
- Modify: `weblate/templates/component.html:91-95` (пункт меню Files)

**Step 1: форма**

```python
class LocKitGlossaryUpdateForm(forms.Form):
    """Upload a table to refresh an existing glossary."""

    table = forms.FileField(
        label=gettext_lazy("Loc-kit table (CSV, TSV, XLSX)"),
        help_text=gettext_lazy(
            "The table is mapped locally and previewed before anything is "
            "written to the glossary."
        ),
        validators=[
            validate_component_zip_upload_size,
            FileExtensionValidator(allowed_extensions=["csv", "tsv", "xlsx"]),
        ],
        widget=forms.FileInput(attrs={"accept": ".csv,.tsv,.xlsx"}),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # The template owns the <form> element; see LocKitSheetSelectForm.
        self.helper = FormHelper(self)
        self.helper.form_tag = False
```

**Step 2: view** (в `create.py`):

```python
@method_decorator(login_required, name="dispatch")
class LocKitGlossaryUpdateStartView(TemplateView):
    """Take a table for an existing glossary and stage it as a draft."""

    template_name = "trans/loc_kit_glossary_update.html"

    def get_component(self) -> Component:
        component = parse_path(self.request, self.kwargs["path"], (Component,))
        if (
            not component.is_glossary
            or component.locked
            or not self.request.user.has_perm("upload.perform", component)
        ):
            raise Http404
        return component

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["object"] = self.get_component()
        context["form"] = kwargs.get("form") or LocKitGlossaryUpdateForm()
        return context

    @transaction.atomic
    def post(self, request: AuthenticatedHttpRequest, **kwargs):
        component = self.get_component()
        form = LocKitGlossaryUpdateForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, **kwargs))

        # ruff: ignore[import-outside-top-level]
        from loc_kit_ingest.reader import ReaderError, read_sheets

        uploaded = form.cleaned_data["table"]
        filename = os.path.basename(getattr(uploaded, "name", "") or "")
        uploaded.seek(0)
        payload = uploaded.read()
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / filename
            local.write_bytes(payload)
            try:
                sheets = read_sheets(local)
            except ReaderError as error:
                form.add_error(
                    "table", gettext("Could not read the table: %s") % error
                )
                return self.render_to_response(
                    self.get_context_data(form=form, **kwargs)
                )
        if not sheets:
            form.add_error("table", gettext("The table holds no worksheets."))
            return self.render_to_response(self.get_context_data(form=form, **kwargs))

        if not request.session.session_key:
            request.session.create()
        draft = LocKitImportDraft(
            owner=request.user,
            session_key=request.session.session_key or "",
            project=component.project,
            category=component.category,
            slug=component.slug,
            name=component.name,
            source_filename=filename,
            target_component=component,
        )
        draft.uploaded.save(filename, ContentFile(payload), save=False)
        try:
            draft.save()
        except Exception:
            draft.delete_storage()
            raise

        if len(sheets) == 1:
            name, rows = next(iter(sheets.items()))
            draft.sheet = name
            draft.state = LocKitImportDraft.State.SHEET_SELECTED
            draft.save(update_fields=["sheet", "state"])
            error = _infer_draft_profile(draft, rows)
            if error is None:
                return redirect("loc-kit-glossary-preview", token=draft.token)
            messages.info(request, error)
        return redirect("loc-kit-sheet-select", token=draft.token)
```

Импорты: `parse_path` уже используется в проекте (`weblate/utils/views.py`); проверить, что он импортирован в `create.py`, иначе добавить.

**Step 3: шаблон** `weblate/templates/trans/loc_kit_glossary_update.html` — по образцу `loc_kit_sheet_select.html`: `{% extends "base.html" %}`, панель, собственный `<form method="post" enctype="multipart/form-data">`, `{% crispy form %}`, submit «Continue», и абзац о том, что до превью в глоссарий ничего не пишется.

**Step 4: URL**

```python
    path(
        "loc-kit/glossary/update/<object_path:path>/",
        weblate.trans.views.create.LocKitGlossaryUpdateStartView.as_view(),
        name="loc-kit-glossary-update",
    ),
```

**Step 5: пункт меню** — в `component.html` внутри блока Files, после «Upload translation»:

```html
        {% if object.is_glossary and user_can_upload_translation %}
          <li>
            <a class="dropdown-item"
               href="{% url 'loc-kit-glossary-update' path=object.get_url_path %}">{% translate "Update glossary from a loc-kit table" %}</a>
          </li>
        {% endif %}
```

Проверить, что `user_can_upload_translation` в этом шаблоне уже определён (`{% perm 'upload.perform' object as user_can_upload_translation %}`); если нет — добавить в начало блока.

**Step 6: тест**

```python
    def test_table_upload_lands_on_a_preview(self) -> None:
        source = self.component.source_language.code
        target = self.glossary_translation.language.code
        csv = f"{source},{target}\nSage,Mudrc\n"
        response = self.client.post(
            reverse(
                "loc-kit-glossary-update",
                kwargs={"path": self.glossary.get_url_path()},
            ),
            {"table": SimpleUploadedFile("Terms.csv", csv.encode())},
        )
        draft = LocKitImportDraft.objects.get()
        self.assertEqual(draft.target_component, self.glossary)
        self.assertRedirects(
            response,
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
        )
        self.assertEqual(draft.state, LocKitImportDraft.State.PREVIEW_READY)
```

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k UpdateUI -n0`

**Step 7: commit**

```bash
git add weblate/trans/forms.py weblate/trans/views/create.py weblate/templates/trans/loc_kit_glossary_update.html weblate/templates/component.html weblate/urls.py weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): upload a table into an existing glossary"
```

---

### Task 4: применение — два прохода `handle_upload`

**Files:**
- Modify: `weblate/trans/views/create.py` — `LocKitGlossaryPreviewView.post`
- Modify: `weblate/templates/trans/loc_kit_glossary_preview.html`

**Step 1: падающие тесты** (в классе update-UI):

```python
    def _apply(self, draft, *, overwrite: bool = False):
        data = {"action": "apply"}
        if overwrite:
            data["overwrite"] = "1"
        return self.client.post(
            reverse("loc-kit-glossary-preview", kwargs={"token": draft.token}),
            data,
            follow=True,
        )

    def test_apply_adds_missing_terms(self) -> None:
        self._start_update("Sage,Mudrc\n")
        draft = LocKitImportDraft.objects.get()
        self._apply(draft)
        unit = self.glossary_translation.unit_set.get(source="Sage")
        self.assertEqual(unit.target, "Mudrc")

    def test_apply_keeps_a_translator_edit_by_default(self) -> None:
        self._start_update("Sage,Mudrc\n")
        self._apply(LocKitImportDraft.objects.get())
        unit = self.glossary_translation.unit_set.get(source="Sage")
        unit.translate(self.user, "Mudrec", STATE_TRANSLATED)

        self._start_update("Sage,Mudrc\n")
        self._apply(LocKitImportDraft.objects.get())
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Mudrec")

    def test_overwrite_replaces_a_translator_edit(self) -> None:
        self._start_update("Sage,Mudrc\n")
        self._apply(LocKitImportDraft.objects.get())
        unit = self.glossary_translation.unit_set.get(source="Sage")
        unit.translate(self.user, "Mudrec", STATE_TRANSLATED)

        self._start_update("Sage,Mudrc\n")
        self._apply(LocKitImportDraft.objects.get(), overwrite=True)
        unit.refresh_from_db()
        self.assertEqual(unit.target, "Mudrc")

    def test_apply_consumes_the_draft(self) -> None:
        self._start_update("Sage,Mudrc\n")
        draft = LocKitImportDraft.objects.get()
        self._apply(draft)
        self.assertFalse(LocKitImportDraft.objects.exists())
```

`_start_update(body)` — хелпер, добавляющий строку заголовка из кодов языков теста и постящий файл (см. Task 3).

**Step 2: убедиться, что падают** (сейчас `action="apply"` даёт 404).

**Step 3: реализация** — в `LocKitGlossaryPreviewView.post`, перед веткой `confirm`:

```python
        if action == "apply":
            component = draft.target_component
            if component is None or draft.state != (
                LocKitImportDraft.State.PREVIEW_READY
            ):
                raise Http404
            sheets = self.read_draft_sheets(draft)
            if draft.sheet not in sheets:
                raise Http404
            # Re-validate against the same worksheet: the preview may be an
            # hour old and the gate is cheap compared to a wrong import.
            try:
                preview = validate_glossary_profile(
                    profile_document=json.loads(draft.profile_json),
                    rows=sheets[draft.sheet],
                    sheet_name=draft.sheet,
                    component_name=component.slug,
                )
            except GlossaryProfileError as error:
                messages.error(request, str(error))
                return redirect("loc-kit-glossary-preview", token=draft.token)

            if preview.source_language != component.source_language.code:
                messages.error(
                    request,
                    gettext(
                        "The table's source language is %(table)s, but this "
                        "glossary uses %(glossary)s. A glossary only applies "
                        "to components sharing its source language."
                    )
                    % {
                        "table": preview.source_language,
                        "glossary": component.source_language.code,
                    },
                )
                return redirect("loc-kit-glossary-preview", token=draft.token)

            # Never silently overwrite a translator: the default leaves any
            # translated term alone, exactly like the ordinary upload form.
            conflicts: str = ""
            if request.POST.get("overwrite") and request.user.has_perm(
                "upload.overwrite", component
            ):
                conflicts = "replace-translated"

            translations = {
                translation.language.code: translation
                for translation in component.translation_set.all()
            }
            created = updated = 0
            for name, payload in preview.files.items():
                code = Path(name).stem
                translation = translations.get(code)
                if translation is None:
                    messages.warning(
                        request,
                        gettext(
                            "Language %s is not present in this glossary; "
                            "its terms were not imported."
                        )
                        % code,
                    )
                    continue
                # Two passes: refresh what exists, then create what does not.
                # The reverse order would report every new term as skipped.
                _nf, _skipped, accepted, _total = translation.handle_upload(
                    request, BytesIO(payload), conflicts, method="translate"
                )
                updated += accepted
                _nf, _skipped, accepted, _total = translation.handle_upload(
                    request, BytesIO(payload), "", method="add"
                )
                created += accepted

            messages.success(
                request,
                gettext("Glossary updated: %(created)d added, %(updated)d refreshed.")
                % {"created": created, "updated": updated},
            )
            draft.state = LocKitImportDraft.State.CONSUMED
            draft.save(update_fields=["state"])
            draft.delete_storage()
            draft.delete()
            return redirect(component)
```

Импорты: `from io import BytesIO`, `from pathlib import Path` (Path уже есть).

**Step 4: шаблон превью** — заменить блок кнопки создания на ветку:

```html
      {% if draft.target_component_id %}
        <form action="{% url 'loc-kit-glossary-preview' token=draft.token %}"
              method="post">
          {% csrf_token %}
          <input type="hidden" name="action" value="apply" />
          {% if user_can_overwrite %}
            <div class="form-check">
              <input class="form-check-input" type="checkbox" name="overwrite" id="id_overwrite" />
              <label class="form-check-label" for="id_overwrite">
                {% translate "Replace terms already translated in Weblate" %}
              </label>
            </div>
          {% endif %}
          <input type="submit"
                 value="{% translate "Update glossary" %}"
                 class="btn btn-primary"
                 {% if not preview %}disabled{% endif %} />
        </form>
      {% else %}
        ... существующая форма создания ...
      {% endif %}
```

`user_can_overwrite` положить в контекст `LocKitGlossaryPreviewView.get_context_data`:
`context["user_can_overwrite"] = draft.target_component_id is not None and self.request.user.has_perm("upload.overwrite", draft.target_component)`.

**Step 5: тесты**

Run: `./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -k Update -n0` → PASS

Дополнительно убедиться, что тест вложенности форм покрывает и update-режим: расширить `test_stage_templates_never_nest_a_form` третьей страницей — превью update-черновика.

**Step 6: commit**

```bash
git add weblate/trans/views/create.py weblate/templates/trans/loc_kit_glossary_preview.html weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): apply a table to an existing glossary"
```

---

### Task 5: предупреждение про описания

Если профиль содержит notes (LLM-путь), пользователь должен знать, что через upload они не доедут.

**Files:**
- Modify: `weblate/templates/trans/loc_kit_glossary_preview.html`

**Step 1: тест**

```python
    def test_update_preview_warns_about_explanations(self) -> None:
        """Notes exist in the profile but the upload path drops them."""
        # профиль с notes загружается вручную через action=upload-profile
        ...
        self.assertContains(page, "explanations")
```

**Step 2: реализация** — в update-ветке шаблона:

```html
        {% if preview.note_count %}
          <div class="alert alert-warning" role="alert">
            {% blocktranslate %}This table carries explanations. Updating an existing glossary imports terms only; explanations reach the database through repository synchronisation.{% endblocktranslate %}
          </div>
        {% endif %}
```

**Step 3: commit**

```bash
git add weblate/templates/trans/loc_kit_glossary_preview.html weblate/trans/tests/test_loc_kit_ingest_contract.py
git commit -m "feat(loc-kit): warn that a glossary update drops explanations"
```

---

### Task 6: документация

**Files:**
- Modify: `docs/changes.rst` (верхняя нерелизнутая секция)
- Modify: `docs/specs/loc-kit-ingest.md`
- Modify: `AGENTS.md`

Зафиксировать: точку входа (меню Files глоссария), два прохода `translate`+`add`, консервативный дефолт и чекбоks затирания, ограничение по explanations, поведение при отсутствующем в глоссарии языке.

```bash
git add docs/changes.rst docs/specs/loc-kit-ingest.md AGENTS.md
git commit -m "docs(loc-kit): document updating a glossary from a table"
```

---

### Task 7: полный прогон и живой smoke

**Step 1:**

```bash
./rundev.sh test weblate/trans/tests/test_loc_kit_ingest_contract.py -n0
uv run prek run --files weblate/trans/views/create.py weblate/trans/forms.py weblate/trans/models/loc_kit.py weblate/templates/trans/loc_kit_glossary_preview.html weblate/templates/trans/loc_kit_glossary_update.html weblate/templates/component.html weblate/urls.py weblate/trans/tests/test_loc_kit_ingest_contract.py
```

**Step 2: живой smoke (кликами, не `form.submit()`)** на dev-инстансе (:3001, admin/admin), проект Heart Abyss:
1. Глоссарий, созданный фазой 1 → меню Files → «Update glossary from a loc-kit table».
2. Загрузить `Heart Abyss_Localization - Terms.csv` с добавленной строкой нового термина → превью → «Update glossary».
3. Ожидание: сообщение «Glossary updated: 1 added, 0 refreshed», новый термин виден в глоссарии.
4. Отредактировать перевод одного термина в Weblate, снова загрузить исходный CSV без чекбокса → правка сохранена, счётчик refreshed не включает её.
5. Повторить с чекбоксом → правка заменена значением из таблицы.
