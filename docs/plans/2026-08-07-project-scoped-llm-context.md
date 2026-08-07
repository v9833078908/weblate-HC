# Project-scoped LLM context - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let every project give its LLM suggestions its own voice and its own per-language rules without copying credentials, and let the model see the developer note attached to each string.

**Architecture:** Three independent changes, none of them specific to a project or a game. (1) Project-level machinery settings become a field-by-field override of the site-wide ones instead of a wholesale replacement, so a project stores only what differs - persona, style, language instructions - and inherits transport fields. (2) `weblate/machinery/llm.py` gains one more per-string context field, the developer note, so context that translators already see reaches the model. (3) A read-only management command reports how much of a project's glossary actually matches its source strings, turning "should we add inflected forms?" into a measurement instead of a guess.

**Tech Stack:** Django, Weblate machinery layer, Django REST Framework, pytest in the dev container (`./rundev.sh test`), `weblate_customization.machinery.RoutedLLMTranslation` as the LLM service under test.

---

## Relationship to `docs/plans/2026-08-07-loc-kit-keyless-and-glossary-ui.md`

That plan and this one can be executed in either order. They share no source file.

| | That plan | This plan |
|---|---|---|
| Code | `loc_kit_ingest/*`, `weblate/utils/views.py`, `weblate/trans/views/create.py`, `weblate/trans/forms.py` | `weblate/trans/models/project.py`, `weblate/machinery/{models,views,llm}.py`, `weblate/api/views.py`, new `weblate/glossary/management/` |
| Tests | `loc_kit_ingest/tests/*`, `weblate/trans/tests/test_loc_kit_ingest_contract.py` | `weblate/machinery/tests.py`, `weblate/glossary/tests.py` |
| Docs | `docs/specs/loc-kit-ingest.md` | `docs/admin/machine.rst`, `docs/api.rst` |

Two files are touched by both and will conflict textually if the branches diverge:

- `docs/changes.rst` - both append to the same unreleased rubric. Whoever lands second re-applies one bullet.
- `AGENTS.md` - that plan edits the `loc_kit_ingest/` bullet, this one edits the `weblate_customization/` bullet. Different lines of the same list.

One behavioural interaction worth knowing: that plan's Task C3 step 4 smoke-tests an OpenRouter suggestion and checks that a glossary term reaches the prompt. This plan changes what else is in that prompt (the note) and where persona comes from. Neither invalidates the other; if both are landed, run that smoke test last.

---

## Background: verified facts

Everything below was read from the current tree. Read this section before starting; the tasks assume it.

### How the system prompt is built

`weblate/machinery/llm.py:56-170` holds one `PROMPT` template shared by every LLM service. `_get_prompt` (`llm.py:1046-1051`) fills exactly three slots:

```python
return PROMPT.format(
    persona=self.format_prompt_part("persona"),
    style=self.format_prompt_part("style"),
    language_instructions=self.format_language_instructions(target_language),
)
```

All three read `self.settings` (`llm.py:284-291`), which is the service configuration dict. Everything else in the prompt - the JSON schema, 25 rules, the placeholder contract - is a constant.

`_get_language_instructions` (`llm.py:481-506`) resolves the target code, then a fuzzy language match, then the base code.

Per-string context is *not* in the system prompt. It travels in the user message, assembled by `_get_string_context` (`llm.py:869-912`): `context`/`key`, `explanation`, `secondary`, `plural`, `failing_checks`, `placeholders`. The matching glossary entries are built separately by `_get_glossary_entry` (`llm.py:510-541`) and carry `source_explanation`, `target_explanation` and glossary flags.

`unit.note` - the developer comment, shown to every translator in the editor - is in neither list.

### How service settings resolve today

Two stores:

- Site-wide: `weblate.configuration.models.Setting`, `category=SettingCategory.MT` (2), `name` = service slug, `value` = the configuration dict. Edited at `/manage/machinery/`.
- Per project: `Project.machinery_settings`, a `JSONField` (`weblate/trans/models/project.py:553`). Edited at `/machinery/<project>/` and through `/api/projects/<slug>/machinery_settings/`.

`Project.get_machinery_settings` (`project.py:1332-1347`) merges them **by whole service**:

```python
for item, value in self.machinery_settings.items():
    if value is None:
        if item in mt_settings:
            del mt_settings[item]
    else:
        mt_settings[item] = value
        mt_settings[item]["_project"] = self
```

So a project entry replaces the site-wide entry completely. To change only the persona, a project must restate `key`, `base_url` and `routing`. Two consequences that this plan removes:

- Rotating the API key means editing every project.
- Both write paths reject a partial configuration before it is ever stored, because they validate the raw payload against the settings form and `key` is a required field (`weblate/machinery/forms.py:123-126`, `weblate/machinery/models.py:70-86`).

The write paths:

- UI: `EditMachineryProjectView.save_settings` (`weblate/machinery/views.py:343-345`) stores `form.cleaned_data`, i.e. every field of the form. `get_initial` (`views.py:250-251`) seeds the form from the raw project entry, so inherited values are invisible.
- API: `weblate/api/views.py:2384-2425` runs `validate_service_configuration(..., allow_private_targets=False)` and stores the result.

`allow_private_targets` is `False` for project scope (`views.py:339-341`), which routes `base_url` through `validate_machinery_hostname` (`weblate/machinery/forms.py:108-120`) and blocks internal addresses. `openrouter.ai` is public and passes.

`get_cache_key` (`weblate/machinery/base.py:464-485`) hashes the entire settings dict, and project-scoped dicts carry `_project`. Two projects with different personas therefore never share a cached suggestion; two projects on identical settings do, which is correct.

Current state, checked on the running instance: all four projects have `machinery_settings == {}`, and the single site-wide `openrouter` record carries a persona written for one specific game. Nothing depends on the replace semantics today.

### How glossary matching works

`fetch_glossary_terms` (`weblate/glossary/models.py:128-199`) runs an Aho-Corasick automaton over the lowercased source string and keeps a hit only when both ends fall on a word boundary (`NON_WORD_RE = re.compile(r"\W")`, `models.py:31`, boundary check at `models.py:189-194`). There is no stemming and no morphology. `get_glossary_units` (`models.py:111-118`) additionally restricts matches to glossaries whose component source language equals the translated component's source language.

Variants (`models.py:242-277`) group entries for display. The LLM path explicitly asks for matches **without** them (`llm.py:550`, `llm.py:1006`), so an inflected form only reaches the model if it is a glossary entry in its own right.

Consequence for any inflected source language: a term matches in its dictionary form only. Whether that costs anything is a per-project measurement, which is what Part C builds.

### Terminology flags

`terminology` lives in `Unit.extra_flags` in the database. TBX carries `forbidden` inbound only (`weblate/formats/ttkit.py:3404-3423` reads `<termNote type="administrativeStatus">`; there is no write path), so the flag does not round-trip through a file and is lost if a glossary component is deleted and re-imported. `BulkEditForm.add_flags` (`weblate/trans/forms.py:4212-4213`) is the supported way to set it in bulk. Part D is therefore a procedure, not code.

---

## Design decisions

**D1. Project machinery settings override the site-wide entry field by field.**
`{**sitewide, **project}`, shallow. A key absent from the project entry inherits; a key present overrides, including an explicit `""`; a whole entry of `None` still disables the service for the project. Shallow, not deep: overriding `language_instructions` replaces the whole map, which is predictable. Nested merging of a user-supplied JSON map is not.

**D2. Validation runs on the merged configuration, storage keeps only the difference.**
Otherwise a partial payload fails on required fields it never intended to set, and a UI save silently freezes today's site-wide values into the project. This is the whole point of D1, so both write paths get the same treatment.

**D3. `{}` at project level means "install this service, inherit everything".**
It already means "install with no settings", and `weblate/trans/tests/test_autotranslate.py:576` and `weblate/trans/tests/test_selenium.py:1137` both rely on installing a service that has no site-wide entry. Under D1 that is unchanged: merging over a missing entry yields `{}`.

**D4. The developer note is sent as its own field, resolved from the source unit first.**
Mirrors `_get_explanation_context` (`llm.py:404-414`), because in bilingual formats the note lives on the source unit while monolingual imports copy it to both. A new field rather than appending to `explanation`, so the model can weigh them separately and so the existing explanation tests keep meaning what they say.

**D5. Glossary coverage is measured, never guessed, and the tool changes nothing.**
A read-only management command reports matched and unmatched candidate forms per project. Adding inflected entries is a data decision a maintainer makes per project, from its output. No automatic variant generation: it would need a per-language stemmer, a new dependency, and a rebuild of a cached hot path, to fix something whose size is currently unknown.

**D6. No new abstractions.** No settings-inheritance framework, no persona registry, no glossary-morphology plugin. Four small edits, one command, one procedure.

---

## Conventions for every task

- Work in the main checkout. **Do not create a git worktree**: `dev-docker/` publishes fixed host ports (5434, 1080) and the compose project name comes from the directory basename, so a second checkout collides with the running stack.
- Tests run in the container: `./rundev.sh test <path>`. `weblate/` is bind-mounted and Granian reloads, so no copy step is needed for anything in this plan.
- Do not run project-wide linters or the full suite between tasks; Task E3 does that once.
- Commit after every green task, Conventional Commits style.
- The dev instance is on port 3001, login `admin`/`admin`. The API token is in `weblate-mcp/.env`; do not mint a second one.

---

# Part A - Project settings inherit the site-wide ones

### Task A1: Failing test for field-by-field inheritance

**Files:**
- Modify: `weblate/machinery/tests.py` (append a new class after `MachineryValidationTest`, which ends at line 8924)

**Step 1: Write the failing tests**

```python
class ProjectMachineryInheritanceTest(TestCase):
    """Project settings override site-wide ones field by field."""

    SITEWIDE: ClassVar[SettingsDict] = {
        "key": "sitewide-key",
        "model": "auto",
        "persona": "You translate spaceship UI.",
        "style": "terse",
    }

    def setUp(self) -> None:
        super().setUp()
        self.project = Project.objects.create(name="Test", slug="test")
        Setting.objects.create(
            category=SettingCategory.MT, name="openai", value=self.SITEWIDE
        )

    def test_absent_field_is_inherited(self) -> None:
        self.project.machinery_settings["openai"] = {"persona": "You write dialogue."}

        resolved = self.project.get_machinery_settings()["openai"]

        self.assertEqual(resolved["persona"], "You write dialogue.")
        self.assertEqual(resolved["key"], "sitewide-key")
        self.assertEqual(resolved["style"], "terse")

    def test_empty_string_overrides_rather_than_inherits(self) -> None:
        self.project.machinery_settings["openai"] = {"style": ""}

        resolved = self.project.get_machinery_settings()["openai"]

        self.assertEqual(resolved["style"], "")
        self.assertEqual(resolved["persona"], "You translate spaceship UI.")

    def test_empty_entry_inherits_everything(self) -> None:
        self.project.machinery_settings["openai"] = {}

        resolved = self.project.get_machinery_settings()["openai"]

        self.assertEqual(resolved["key"], "sitewide-key")

    def test_none_entry_still_disables_the_service(self) -> None:
        self.project.machinery_settings["openai"] = None

        self.assertNotIn("openai", self.project.get_machinery_settings())

    def test_service_without_sitewide_entry_still_works(self) -> None:
        self.project.machinery_settings["dummy"] = {"key": "x"}

        resolved = self.project.get_machinery_settings()["dummy"]

        self.assertEqual(resolved["key"], "x")

    def test_resolution_does_not_mutate_the_stored_entry(self) -> None:
        self.project.machinery_settings["openai"] = {"persona": "You write dialogue."}

        self.project.get_machinery_settings()

        self.assertEqual(
            self.project.machinery_settings["openai"], {"persona": "You write dialogue."}
        )

    def test_resolution_does_not_leak_into_the_sitewide_entry(self) -> None:
        self.project.machinery_settings["openai"] = {"persona": "You write dialogue."}

        self.project.get_machinery_settings()
        other = Project.objects.create(name="Other", slug="other")

        self.assertEqual(
            other.get_machinery_settings()["openai"]["persona"],
            "You translate spaceship UI.",
        )
```

`ClassVar` and `SettingsDict` are already imported in this module; `Project`, `Setting` and `SettingCategory` are imported at lines 100 and 44.

**Step 2: Run them and watch them fail**

```bash
./rundev.sh test weblate/machinery/tests.py::ProjectMachineryInheritanceTest -v
```

Expected: `test_absent_field_is_inherited`, `test_empty_string_overrides_rather_than_inherits`, `test_empty_entry_inherits_everything` and `test_resolution_does_not_mutate_the_stored_entry` FAIL. The first three fail on `KeyError: 'key'` or a wrong value; the fourth fails because today's code writes `_project` into the stored dict. The other three pass already - that is intentional, they pin behaviour that must survive.

**Step 3: Commit the tests**

```bash
git add weblate/machinery/tests.py
git commit -m "test(machinery): pin project settings inheritance semantics"
```

---

### Task A2: Implement the merge

**Files:**
- Modify: `weblate/trans/models/project.py:1332-1347`

**Step 1: Replace the loop body**

```python
    def get_machinery_settings(self) -> dict[str, SettingsDict]:
        mt_settings = cast(
            "dict[str, SettingsDict]",
            Setting.objects.get_settings_dict(SettingCategory.MT),
        )
        for item, value in self.machinery_settings.items():
            if value is None:
                if item in mt_settings:
                    del mt_settings[item]
            else:
                # Project settings override the site-wide ones field by field so
                # that a project can change the persona or language instructions
                # without restating credentials. Building a new dict also keeps
                # both stored configurations free of the _project marker below.
                mt_settings[item] = {**mt_settings.get(item, {}), **value}
                # Include project field so that different projects do not share
                # cache keys via MachineTranslation.get_cache_key when service
                # is installed at project level.
                mt_settings[item]["_project"] = self
        return mt_settings
```

The returned mapping is for reading only. `_project` holds a `Project` instance, so a caller that writes this dict back into `machinery_settings` would break JSON serialisation of that field; both write paths store form or payload data instead, and Task A6 keeps it that way.

**Step 2: Run the tests**

```bash
./rundev.sh test weblate/machinery/tests.py::ProjectMachineryInheritanceTest -v
```

Expected: 7 passed.

**Step 3: Run the existing users of project-level settings**

```bash
./rundev.sh test weblate/trans/tests/test_autotranslate.py -k machinery_settings
```

Expected: PASS. `test_autotranslate.py` is flaky under xdist in the container; if something unrelated fails, re-run it on the unmodified tree before blaming this change.

**Step 4: Commit**

```bash
git add weblate/trans/models/project.py
git commit -m "feat(machinery): inherit site-wide service settings per field"
```

---

### Task A3: Failing test for validating a partial configuration

**Files:**
- Modify: `weblate/machinery/tests.py` (append to `MachineryValidationTest`)

**Step 1: Write the failing tests**

```python
    @http_mock.activate
    def test_partial_configuration_validates_against_the_base(self) -> None:
        # DeepL is used because its settings form has exactly one required
        # field beside the overridden one, and its live check is already
        # mockable from a classmethod.
        DeepLTranslationTest.mock_response()

        service, configuration, errors = validate_service_configuration(
            "deepl",
            {"url": "https://api.deepl.com/"},
            allow_private_targets=False,
            base_configuration={"key": "sitewide-key"},
        )

        self.assertIsNotNone(service)
        self.assertEqual(errors, [])
        # Only the difference is returned, the inherited key is not stored.
        self.assertEqual(configuration, {"url": "https://api.deepl.com/"})

    def test_partial_configuration_without_a_base_still_requires_the_key(self) -> None:
        _service, _configuration, errors = validate_service_configuration(
            "deepl",
            {"url": "https://api.deepl.com/"},
            allow_private_targets=False,
        )

        self.assertTrue(any("key" in error for error in errors))

    def test_partial_configuration_reports_its_own_invalid_field(self) -> None:
        # A bad scheme is a plain field error, unlike a private address which
        # surfaces as a non-field error and depends on the host allowlist.
        _service, _configuration, errors = validate_service_configuration(
            "deepl",
            {"url": "ftp://api.deepl.com/"},
            allow_private_targets=False,
            base_configuration={"key": "sitewide-key"},
        )

        self.assertTrue(any("url" in error for error in errors))
```

The second test asserts that inheritance is opt-in: site-wide edits pass no base and keep failing on a missing key.

Add `from weblate.machinery.models import validate_service_configuration` to the imports (it is not there yet). `http_mock` and `DeepLTranslationTest` are already in the module.

**Why not an LLM service here.** Every LLM settings form verifies the key with a live round trip - OpenAI fetches `/v1/models` and then posts to `/v1/chat/completions` - so a passing case needs both endpoints mocked, and `OpenAITranslationTest.mock_response` is a plain instance method that cannot be borrowed from another test class. `DeepLTranslationTest.mock_response` is a `@classmethod` (`weblate/machinery/tests.py:2480-2488`). The merge being tested is service-independent.

**Step 2: Run them**

```bash
./rundev.sh test weblate/machinery/tests.py::MachineryValidationTest -v
```

Expected: the first and third FAIL with `TypeError: validate_service_configuration() got an unexpected keyword argument 'base_configuration'`.

**Step 3: Commit**

```bash
git add weblate/machinery/tests.py
git commit -m "test(machinery): cover partial service configuration validation"
```

---

### Task A4: Validate the merged configuration, store the difference

**Files:**
- Modify: `weblate/machinery/models.py:37-87`

**Step 1: Add the parameter**

Change the signature and the form construction. Everything else in the function is unchanged.

```python
def validate_service_configuration(
    service_name: str,
    configuration: str | SettingsDict,
    *,
    allow_private_targets: bool = True,
    base_configuration: SettingsDict | None = None,
) -> tuple[type[BatchMachineTranslation] | None, SettingsDict, list[str]]:
    """
    Validate given service configuration.

    :param service_name: Name of the service as defined in WEBLATE_MACHINERY
    :param configuration: JSON encoded configuration for the service
    :param base_configuration: Configuration the given one is layered on top of,
        used for project-level overrides which only carry the changed fields.
        Validation sees the merged result; the returned configuration is the
        given one, so only the difference is stored.
    :return: A tuple containing the validated service class, configuration
             and a list of errors
    :raises ValueError: When service is not found or configuration is invalid
    """
```

Then, in place of the current `form = service.settings_form(...)` call at lines 71-75:

```python
    errors = []
    if service.settings_form is not None:
        validated_configuration = service_configuration
        if base_configuration is not None:
            validated_configuration = cast(
                "SettingsDict", {**base_configuration, **service_configuration}
            )
        form = service.settings_form(
            service,
            data=validated_configuration,
            allow_private_targets=allow_private_targets,
        )
```

`base_configuration` is keyword-only with a default of `None`, so the three existing call sites are unaffected. Two of them are updated in Task A5 (`weblate/api/views.py:2390`, `2431`); the third is the `install_machinery` management command (`weblate/machinery/management/commands/install_machinery.py:34`), which installs site-wide services and must keep validating in full. Leave it alone.

**Step 2: Run the tests**

```bash
./rundev.sh test weblate/machinery/tests.py::MachineryValidationTest -v
```

Expected: all pass.

**Step 3: Commit**

```bash
git add weblate/machinery/models.py
git commit -m "feat(machinery): validate partial configurations against a base"
```

---

### Task A5: Wire the API write path

**Files:**
- Modify: `weblate/api/views.py:2390-2394` and the `PUT` branch at `2427-2440`
- Modify: `weblate/api/tests.py` (next to `test_install_machinery`, line 6049)

**Step 1: Write the failing API test**

Add `from weblate.configuration.models import Setting, SettingCategory` to the imports; `Project`, `patch` and `http_mock` are already there (lines 73-84, 16, 107).

The three decorators and the `mock_response()` call are what let a provider configuration validate without touching the network; they are copied from `test_install_machinery`.

```python
    @http_mock.activate
    @patch("weblate.utils.requests._get_response_peer_ip", return_value="93.184.216.34")
    @patch(
        "weblate.utils.outbound.socket.getaddrinfo",
        return_value=[(0, 0, 0, "", ("93.184.216.34", 443))],
    )
    def test_project_machinery_accepts_partial_configuration(
        self, mocked_getaddrinfo, mocked_get_peer
    ) -> None:
        # Deep import to avoid running these as tests
        # ruff: ignore[import-outside-top-level]
        from weblate.machinery.tests import DeepLTranslationTest

        Setting.objects.create(
            category=SettingCategory.MT,
            name="deepl",
            value={"key": "sitewide-key", "url": "https://api.deepl.com/"},
        )
        DeepLTranslationTest.mock_response()

        self.do_request(
            "api:project-machinery-settings",
            self.project_kwargs,
            method="post",
            code=201,
            superuser=True,
            request={
                "service": "deepl",
                "configuration": {"url": "https://api-free.deepl.com/"},
            },
            format="json",
        )

        project = Project.objects.get(slug=self.project_kwargs["slug"])
        self.assertEqual(
            project.machinery_settings["deepl"],
            {"url": "https://api-free.deepl.com/"},
        )
        self.assertEqual(
            project.get_machinery_settings()["deepl"]["key"], "sitewide-key"
        )
```

`deepl` is used rather than an LLM service because its settings form has exactly one required field beside the one being overridden, and the surrounding test already proves this mocking pattern validates it.

**Step 2: Run it and watch it fail**

```bash
./rundev.sh test weblate/api/tests.py -k machinery_accepts_partial -v
```

Expected: FAIL with a 400 and `Error in key (deepl): This field is required.`

**Step 3: Pass the site-wide entry as the base**

In `weblate/api/views.py`, inside `machinery_settings`, before the `POST`/`PATCH` branch:

```python
        sitewide_settings = cast(
            "dict[str, SettingsDict]",
            Setting.objects.get_settings_dict(SettingCategory.MT),
        )
```

Then in the `POST`/`PATCH` branch replace the validation call:

```python
            service, configuration, errors = validate_service_configuration(
                service_name,
                request.data.get("configuration", "{}"),
                allow_private_targets=False,
                base_configuration=sitewide_settings.get(service_name),
            )
```

And the same for the `PUT` loop:

```python
                service, configuration, errors = validate_service_configuration(
                    service_name,
                    configuration,
                    allow_private_targets=False,
                    base_configuration=sitewide_settings.get(service_name),
                )
```

Add the imports this needs if they are not already present in the module: `Setting`, `SettingCategory` from `weblate.configuration.models`, and `SettingsDict` under `TYPE_CHECKING`.

**Step 4: Run it**

```bash
./rundev.sh test weblate/api/tests.py -k machinery -v
```

Expected: all pass, including the pre-existing machinery settings tests.

**Step 5: Commit**

```bash
git add weblate/api/views.py weblate/api/tests.py
git commit -m "feat(api): accept partial project machinery configuration"
```

---

### Task A6: Wire the UI write path

Two problems in the form flow, both in `weblate/machinery/views.py`. The form starts empty, so an administrator cannot see what is inherited; and it saves every field, so the first save freezes today's site-wide values into the project.

**Files:**
- Modify: `weblate/machinery/views.py:338-355` (`EditMachineryProjectView`)
- Modify: `weblate/machinery/tests.py` (`ViewsTest`, line 8060, or a new class beside it)

**Step 1: Write the failing test**

```python
    def test_project_machinery_form_stores_only_the_difference(self) -> None:
        Setting.objects.create(
            category=SettingCategory.MT,
            name="openai",
            value={"key": "sitewide-key", "model": "auto", "persona": "Spaceships."},
        )
        self.user.is_superuser = True
        self.user.save()

        response = self.client.post(
            reverse(
                "machinery-edit",
                kwargs={"project": self.project.slug, "machinery": "openai"},
            ),
            {"key": "sitewide-key", "model": "auto", "persona": "Dialogue."},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.machinery_settings["openai"], {"persona": "Dialogue."}
        )
```

**Step 2: Run it**

```bash
./rundev.sh test weblate/machinery/tests.py -k stores_only_the_difference -v
```

Expected: FAIL - the stored entry contains every posted field.

**Step 3: Implement**

```python
class EditMachineryProjectView(MachineryProjectMixin, EditMachineryView):
    @property
    def allow_private_targets(self) -> bool:
        return False

    @cached_property
    def sitewide_configuration(self) -> SettingsDict:
        return self.global_settings_dict.get(self.machinery_id) or {}

    def get_initial(self):
        # Show what is currently in effect, so an administrator edits the
        # resolved configuration rather than a form with empty credentials.
        return {**self.sitewide_configuration, **(super().get_initial() or {})}

    def save_settings(self, data: SettingsDict | None) -> None:
        if data is not None:
            data = self.strip_inherited(data)
        self.project.machinery_settings[self.machinery_id] = data
        self.project.save(update_fields=["machinery_settings"])

    def strip_inherited(self, data: SettingsDict) -> SettingsDict:
        """
        Keep only the fields this project actually overrides.

        Site-wide changes such as a rotated key keep propagating to the
        project. A field the site-wide configuration does not define is kept
        only when it carries a value: an empty one overrides nothing and is
        indistinguishable from leaving the field alone.
        """
        sitewide = self.sitewide_configuration
        return {
            field: value
            for field, value in data.items()
            if (field in sitewide and sitewide[field] != value)
            or (field not in sitewide and value not in {"", None, False})
        }
```

`cached_property` and `SettingsDict` are already imported in this module; confirm before adding.

**Leave `delete_service` and `enable_service` alone.** They already do the right thing under the new semantics - dropping the project entry restores the site-wide settings, and storing `None` opts out - and renaming them for readability would be a refactor of working upstream code that `test_configure_project` already covers.

**Why the second clause is not just `field not in sitewide`.** A form posts every field it declares, so `cleaned_data` carries `""` for untouched optional fields and `False` for unticked checkboxes. Without the emptiness test, saving an unmodified DeepL form would store `{"context": "", "next_gen": False}` as if the project had deliberately blanked them. When the site-wide entry *does* define the field, an empty value is a deliberate override and is kept - that is D1, pinned by `test_configure_project_can_blank_an_inherited_field`.

Note the interaction with `install_service` (`views.py:263-264`), which calls `save_settings({})`. The comprehension leaves `{}` as `{}`, which under Task A2 means "installed, inherits everything". That is the intended behaviour, so no change there.

**Step 4: Run it**

```bash
./rundev.sh test weblate/machinery/tests.py -k machinery -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add weblate/machinery/views.py weblate/machinery/tests.py
git commit -m "feat(machinery): store only overridden fields for a project"
```

---

### Task A7: Prove it on the running instance

No code. This is the acceptance gate for Part A, and it works for any project - substitute the slug.

**Step 1: Record the prompt in effect before any override**

```bash
./rundev.sh exec -T weblate weblate shell -c "
from weblate.trans.models import Project
from weblate.machinery.models import MACHINERY
p = Project.objects.get(slug='heart-abyss')
cfg = p.get_machinery_settings()['openrouter']
print('project scoped:', '_project' in cfg)
print(MACHINERY['openrouter'](cfg)._get_prompt('ja')[:400])
"
```

Expected today: `project scoped: False` and the site-wide persona.

**Step 2: Store a persona and language instructions for one project, and nothing else**

Through the UI at <http://localhost:3001/machinery/heart-abyss/openrouter/>, or through the API:

```bash
curl -s -X POST http://localhost:3001/api/projects/heart-abyss/machinery_settings/ \
  -H "Authorization: Token $WEBLATE_API_TOKEN" \
  -F 'service=openrouter' \
  -F 'configuration={"persona": "...", "style": "...", "language_instructions": {"ja": "..."}}'
```

The persona and style text is a per-project editorial decision and is not part of this plan. Any non-empty text proves the mechanism.

**Step 3: Verify inheritance and isolation**

```bash
./rundev.sh exec -T weblate weblate shell -c "
from weblate.trans.models import Project
from weblate.machinery.models import MACHINERY
for slug in ('heart-abyss', 'space-arena'):
    p = Project.objects.get(slug=slug)
    cfg = p.get_machinery_settings()['openrouter']
    print(slug, 'stored:', p.machinery_settings.get('openrouter'))
    print(slug, 'key inherited:', bool(cfg.get('key')), 'routing inherited:', bool(cfg.get('routing')))
    print(MACHINERY['openrouter'](cfg)._get_prompt('ja')[:200])
    print('---')
"
```

Required outcome:

- `heart-abyss` stores only the three fields that were posted.
- `heart-abyss` still resolves a non-empty `key` and `routing`.
- The two projects print different prompts.
- `space-arena` is untouched and still prints the site-wide persona.

**Step 4: Confirm a live suggestion still works**

Open any Heart Abyss string in the editor, request an OpenRouter suggestion, and confirm a translation comes back. This exercises the merged configuration end to end, including the inherited key.

---

# Part B - The developer note reaches the LLM

### Task B1: Failing tests

**Files:**
- Modify: `weblate/machinery/tests.py`, class `OpenAITranslationTest` (line 3789), next to `test_translate_sends_unit_context` (line 3892)

**Step 1: Write the tests**

```python
    def test_translate_sends_developer_note(self) -> None:
        machine = self.get_machine()
        unit = make_unit(code="fr", source="Get out of here!", note="Joe")
        typed_unit = cast("Unit", unit)
        cleaned_source, _replacements = machine.cleanup_text(unit.source, typed_unit)

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            item = json.loads(content)["strings"][0]
            self.assertEqual(item["note"], "Joe")
            return json.dumps(["Sors d'ici !"])

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            translation = machine.download_multiple_translations(
                "en", "fr", [(cleaned_source, typed_unit)]
            )

        self.assertEqual(translation[cleaned_source][0]["text"], "Sors d'ici !")

    def test_translate_omits_an_empty_developer_note(self) -> None:
        machine = self.get_machine()
        unit = make_unit(code="fr", source="Get out of here!")
        typed_unit = cast("Unit", unit)
        cleaned_source, _replacements = machine.cleanup_text(unit.source, typed_unit)

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            self.assertNotIn("note", json.loads(content)["strings"][0])
            return json.dumps(["Sors d'ici !"])

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            machine.download_multiple_translations(
                "en", "fr", [(cleaned_source, typed_unit)]
            )

    def test_developer_note_comes_from_the_source_unit(self) -> None:
        machine = self.get_machine()
        unit = make_unit(code="fr", source="Get out of here!", note="Joe")
        unit.note = "stale copy on the translation"
        typed_unit = cast("Unit", unit)
        cleaned_source, _replacements = machine.cleanup_text(unit.source, typed_unit)

        def request_callback(
            _prompt: str,
            content: str,
            _previous_content: str,
            _previous_response: str,
        ) -> str:
            self.assertEqual(json.loads(content)["strings"][0]["note"], "Joe")
            return json.dumps(["Sors d'ici !"])

        with patch.object(
            machine, "fetch_llm_translations", side_effect=request_callback
        ):
            machine.download_multiple_translations(
                "en", "fr", [(cleaned_source, typed_unit)]
            )

    def test_translation_cache_uses_the_developer_note(self) -> None:
        machine = self.get_machine()
        unit = make_unit(code="fr", source="Get out of here!", note="Joe")
        typed_unit = cast("Unit", unit)
        cleaned_source, replacements = machine.cleanup_text(unit.source, typed_unit)

        original = machine.get_translation_cache_parts(
            typed_unit, "en", "fr", cleaned_source, 75, replacements
        )
        unit.source_unit.note = "Ray"
        changed = machine.get_translation_cache_parts(
            typed_unit, "en", "fr", cleaned_source, 75, replacements
        )

        self.assertNotEqual(original, changed)
```

`make_unit` already accepts `note` and copies it onto the generated source unit (`weblate/trans/tests/factories.py:134, 163, 180`), which is why the third test overwrites `unit.note` by hand.

The fourth test defends a property that comes for free today - `get_translation_cache_parts` hashes the whole string context (`llm.py:977-988`) - and would silently break if someone later special-cased which fields go into the cache key.

**Step 2: Run them and watch them fail**

```bash
./rundev.sh test weblate/machinery/tests.py::OpenAITranslationTest -k note -v
```

Expected: `test_translate_sends_developer_note`, `test_developer_note_comes_from_the_source_unit` and `test_translation_cache_uses_the_developer_note` FAIL with `KeyError: 'note'` or an equal cache tuple. `test_translate_omits_an_empty_developer_note` passes already.

**Step 3: Commit**

```bash
git add weblate/machinery/tests.py
git commit -m "test(machinery): expect the developer note in LLM context"
```

---

### Task B2: Implement the context field

**Files:**
- Modify: `weblate/machinery/llm.py:245-252` (`LLMStringContext`)
- Modify: `weblate/machinery/llm.py:404-414` (add a helper after `_get_explanation_context`)
- Modify: `weblate/machinery/llm.py:889-890` (`_get_string_context`)

**Step 1: Add the field to the typed dict**

```python
class LLMStringContext(TypedDict, total=False):
    context: str
    key: str
    explanation: str
    note: str
    secondary: LLMSecondaryContext
    plural: LLMPluralContext
    failing_checks: list[LLMFailingCheckContext]
    placeholders: dict[str, str]
```

**Step 2: Add the resolver right after `_get_explanation_context`**

```python
    @classmethod
    def _get_note_context(cls, unit: Unit) -> str:
        source_unit = getattr(unit, "source_unit", None)
        if source_unit is not None:
            note = cls._normalize_context_text(getattr(source_unit, "note", ""))
            if note:
                return note

        return cls._normalize_context_text(getattr(unit, "note", ""))
```

**Step 3: Emit it in `_get_string_context`, directly after the explanation block**

```python
        if explanation := self._get_explanation_context(unit):
            result["explanation"] = explanation

        if note := self._get_note_context(unit):
            result["note"] = note
```

**Step 4: Run the tests**

```bash
./rundev.sh test weblate/machinery/tests.py::OpenAITranslationTest -k note -v
```

Expected: 4 passed.

**Step 5: Commit**

```bash
git add weblate/machinery/llm.py
git commit -m "feat(machinery): send the developer note as LLM string context"
```

---

### Task B3: Declare the field in the prompt

A field the model receives but the schema never mentions is a field the model may ignore or echo back.

**Files:**
- Modify: `weblate/machinery/llm.py:101` (schema block)
- Modify: `weblate/machinery/llm.py:155` (rule 21)
- Modify: `weblate/machinery/llm.py:159` (append rule 26 after rule 25)

**Step 1: Write the failing test**

In `weblate/machinery/tests.py`, next to `test_prompt_forbids_metadata_output` (line 3863):

```python
    def test_prompt_declares_the_developer_note_in_the_schema(self) -> None:
        self.assertIn('"note"', PROMPT)

    def test_prompt_lists_the_developer_note_as_reference_material(self) -> None:
        self.assertIn("context, key, explanation, note,", PROMPT)
```

Two assertions, two tests, on purpose: the first covers the schema block edited in Step 3, the second covers rule 21 edited in Step 4. A single test would report a schema failure when rule 21 is what was forgotten. `PROMPT` is already imported in this module (line 79).

**Step 2: Run it**

```bash
./rundev.sh test weblate/machinery/tests.py -k prompt_documents -v
```

Expected: FAIL.

**Step 3: Extend the schema block**

After the `"explanation"` line:

```
            "note": "spoken by Joe",             // optional note from the developers about this string
```

`PROMPT` is a `str.format` template - do not introduce unescaped `{` or `}` in the added text.

**Step 4: Extend rule 21**

```
21. Treat context, key, explanation, note, secondary, plural, failing_checks, placeholders, and source fields as reference material only. Do not translate them directly and do not add, copy, or emit their contents unless they are present in source or parts.
```

**Step 5: Add rule 26 after rule 25**

```
26. The "note" field carries developer context about the string, such as the speaking character, the screen it appears on, or usage constraints. Use it to choose register, gender agreement, and tone. Never translate or emit it.
```

**Step 6: Run the prompt tests**

```bash
./rundev.sh test weblate/machinery/tests.py -k prompt -v
```

Expected: all pass, including `test_prompt_forbids_metadata_output`, whose assertions do not overlap the edited text.

**Step 7: Commit**

```bash
git add weblate/machinery/llm.py weblate/machinery/tests.py
git commit -m "feat(machinery): document the developer note in the LLM prompt"
```

---

### Task B4: Verify on real data

**Step 1: Confirm a real project has notes to send**

```bash
./rundev.sh exec -T weblate weblate shell -c "
from weblate.trans.models import Unit
from collections import Counter
qs = Unit.objects.filter(translation__component__slug='temple').exclude(note='')
print('units with a note:', qs.count())
print(Counter(qs.values_list('note', flat=True)).most_common(5))
"
```

Any component with non-empty notes works; substitute the slug.

**Step 2: Watch one real request**

Open a string that has a note in the editor, request an OpenRouter suggestion, and confirm in the container log that the outgoing payload carries `"note"`:

```bash
./rundev.sh logs -f weblate
```

If the payload is not logged at the configured level, assert it in a shell instead:

```bash
./rundev.sh exec -T weblate weblate shell -c "
from weblate.trans.models import Unit
from weblate.machinery.models import MACHINERY
unit = Unit.objects.filter(translation__component__slug='temple').exclude(note='').first()
cfg = unit.translation.component.project.get_machinery_settings()['openrouter']
svc = MACHINERY['openrouter'](cfg)
print(svc._get_string_context(unit.source, unit))
"
```

Required: the printed dict contains `note`.

---

# Part C - Measure glossary coverage before changing any glossary

### Task C1: Failing test for the report

**Files:**
- Create: `weblate/glossary/management/__init__.py` (empty)
- Create: `weblate/glossary/management/commands/__init__.py` (empty)
- Modify: `weblate/glossary/tests.py`

**Step 1: Write the test**

```python
class GlossaryCoverageCommandTest(ViewTestCase):
    """The coverage report names what matched, what did not, and writes nothing."""

    CREATE_GLOSSARIES: bool = True

    def setUp(self) -> None:
        super().setUp()
        self.glossary_component = self.project.glossaries[0]

    def add_source_term(self, source: str) -> None:
        # The report only reads source-language glossary units, so unlike
        # GlossaryTest.add_term no target-language unit is needed.
        self.glossary_component.source_translation.unit_set.create(
            source=source,
            target=source,
            context="",
            id_hash=calculate_hash(source, ""),
            position=1,
            state=STATE_TRANSLATED,
        )

    def run_command(self) -> str:
        output = StringIO()
        call_command("glossary_coverage", self.project.slug, stdout=output)
        return output.getvalue()

    def test_reports_a_term_present_in_the_source_strings(self) -> None:
        self.add_source_term("world")

        report = self.run_command()

        self.assertIn("matched terms:", report)
        self.assertIn("world", report)

    def test_reports_a_term_absent_from_the_source_strings(self) -> None:
        self.add_source_term("dragon")

        report = self.run_command()

        self.assertIn("never matched", report)
        self.assertIn("dragon", report)

    def test_project_without_glossary_terms_says_so(self) -> None:
        self.assertIn("no glossary terms", self.run_command())

    def test_unknown_project_fails_loudly(self) -> None:
        with self.assertRaises(CommandError):
            call_command("glossary_coverage", "no-such-project", stdout=StringIO())

    def test_the_report_writes_nothing(self) -> None:
        self.add_source_term("world")
        before = Unit.objects.count()

        self.run_command()

        self.assertEqual(Unit.objects.count(), before)
```

Verified against the module: `ViewTestCase`, `StringIO` (line 12), `Unit` (line 29), `calculate_hash` (line 32) and `STATE_TRANSLATED` (line 34) are already imported. Add `from django.core.management import call_command` and `from django.core.management.base import CommandError`.

**Do not subclass `GlossaryTest`.** `add_term` and its two-unit fixture live on that class (`weblate/glossary/tests.py:150-190`), not on `ViewTestCase`, so they are not inherited here - but subclassing to borrow them would also re-run every `test_*` method `GlossaryTest` defines. `add_source_term` above is the smaller half of `add_term`, which is all this report needs. `CREATE_GLOSSARIES` and `self.project.glossaries[0]` are copied from the same fixture because there is no other way to get a glossary component.

`get_translation()` is inherited from `ComponentTestCase` (`weblate/trans/tests/test_views.py:301`), and the default component's source string is `Hello, world!\n` (`test_views.py:305`), which is why `world` matches and `dragon` does not.

**Step 2: Run it**

```bash
./rundev.sh test weblate/glossary/tests.py::GlossaryCoverageCommandTest -v
```

Expected: FAIL with `Unknown command: 'glossary_coverage'`.

**Step 3: Commit**

```bash
git add weblate/glossary/management weblate/glossary/tests.py
git commit -m "test(glossary): expect a glossary coverage command"
```

---

### Task C2: Implement the command

Read-only. It must not write to the database.

**Files:**
- Create: `weblate/glossary/management/commands/glossary_coverage.py`

**Step 1: Write it**

```python
# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Report how much of a project's glossary matches its source strings."""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from django.core.management.base import CommandError

from weblate.trans.models import Project
from weblate.utils.management.base import BaseCommand

if TYPE_CHECKING:
    from argparse import ArgumentParser


class Command(BaseCommand):
    help = "report glossary term coverage over the source strings of a project"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("project", help="project slug")
        parser.add_argument(
            "--suffix",
            type=int,
            default=3,
            help=(
                "how many trailing characters an inflected form may add to a "
                "term and still be reported as a candidate (default 3)"
            ),
        )

    def handle(self, *args, **options) -> None:
        try:
            project = Project.objects.get(slug=options["project"])
        except Project.DoesNotExist as error:
            msg = f"No such project: {options['project']}"
            raise CommandError(msg) from error

        glossaries = project.glossaries
        terms = {
            source.strip().lower()
            for glossary in glossaries
            for source in glossary.source_translation.unit_set.values_list(
                "source", flat=True
            )
            if source.strip()
        }
        if not terms:
            self.stdout.write(f"{project.slug}: no glossary terms")
            return

        # Only components sharing a glossary's source language can ever match;
        # see get_glossary_units in weblate/glossary/models.py.
        source_languages = {glossary.source_language_id for glossary in glossaries}
        sources = [
            source.lower()
            for component in project.component_set.filter(
                is_glossary=False, source_language__in=source_languages
            )
            for source in component.source_translation.unit_set.values_list(
                "source", flat=True
            )
        ]

        matched: Counter[str] = Counter()
        candidates: Counter[str] = Counter()
        suffix = options["suffix"]

        for term in sorted(terms):
            escaped = re.escape(term)
            # exact mirrors the word-boundary rule of fetch_glossary_terms.
            # inflected is deliberately looser than that rule: it is a report
            # of near misses, not a matcher, and \w{1,N} is greedy so a form
            # is only reported once, at its longest.
            exact = re.compile(rf"(?<!\w){escaped}(?!\w)")
            inflected = re.compile(rf"(?<!\w)({escaped}\w{{1,{suffix}}})(?!\w)")
            for source in sources:
                matched[term] += len(exact.findall(source))
                for form in inflected.findall(source):
                    candidates[form] += 1

        self.stdout.write(
            f"{project.slug}: {len(terms)} terms, {len(sources)} source strings, "
            f"{sum(matched.values())} matches"
        )

        self.stdout.write("")
        self.stdout.write("matched terms:")
        for term, count in matched.most_common():
            if count:
                self.stdout.write(f"  {count:6}  {term}")

        unmatched = sorted(term for term, count in matched.items() if not count)
        if unmatched:
            self.stdout.write("")
            self.stdout.write(f"never matched ({len(unmatched)}):")
            for term in unmatched:
                self.stdout.write(f"          {term}")

        if candidates:
            self.stdout.write("")
            self.stdout.write(
                "forms a term nearly matched - candidates for a glossary entry:"
            )
            for form, count in candidates.most_common():
                self.stdout.write(f"  {count:6}  {form}")
```

Verified against the tree: `BaseCommand` lives in `weblate/utils/management/base.py` (see `weblate/trans/management/commands/list_versions.py:15`), `Project.glossaries` returns a list of components (`weblate/trans/models/project.py:1283-1286`), and `Component.source_translation` is the source-language translation used by `weblate/glossary/tests.py:172`.

`\w` is Unicode-aware for `str` patterns, so the boundary rule holds for non-Latin scripts, matching `NON_WORD_RE` in `weblate/glossary/models.py:31`.

**Step 2: Run the tests**

```bash
./rundev.sh test weblate/glossary/tests.py::GlossaryCoverageCommandTest -v
```

Expected: 5 passed.

**Step 3: Commit**

```bash
git add weblate/glossary/management/commands/glossary_coverage.py
git commit -m "feat(glossary): add a glossary coverage report command"
```

---

### Task C3: Run it on every project and record the decision

**Step 1: Run**

```bash
for slug in space-arena need-for-greed pirate-ships heart-abyss; do
  ./rundev.sh exec -T weblate weblate glossary_coverage "$slug"
done
```

**Step 2: Decide, per project, with the numbers in hand**

- A term in "never matched" that also has no near misses does not appear in that project's texts. Nothing to do.
- A form under "candidates" with a meaningful count is an inflected form the glossary is losing. Add it as its own glossary entry, with the same target, and group it with the base term as a variant so the sidebar shows them together. It has to be a real entry, not only a variant: the LLM path requests matches with `include_variants=False` (`weblate/machinery/llm.py:550`).
- Below roughly five occurrences it is not worth the glossary noise. Write the number down and stop.

**Step 3: Record the outcome**

Append the counts and the decision to this plan file under a new "Measured coverage" heading, then commit. A future reader needs to know the decision was measured, not assumed.

### Measured coverage

Run on 2026-08-07 against the dev instance, all four projects.

| Project | Terms | Source strings | Matches | Never matched | Candidates |
|---|---|---|---|---|---|
| space-arena | 300 | 4826 | 5037 | 1 (`nanoinjector`) | ~120 forms, 49 of them at 5 or more |
| heart-abyss | 12 | 688 | 25 | 7 | 1 (`дзинково`, once) |
| need-for-greed | 0 | - | - | - | no glossary terms |
| pirate-ships | 0 | - | - | - | no glossary terms |

**Decisions.**

- **need-for-greed, pirate-ships** - nothing to measure, no glossary exists. Revisit if one is created.
- **heart-abyss** - no action. The single candidate occurs once, far below the five-occurrence threshold. The seven never-matched terms are character names absent from the one imported chapter, which is expected, not a defect.
- **space-arena** - a real and large loss, but it needs a maintainer's sign-off before ~50 entries are added to a live glossary, so this task stops at the measurement. The numbers are below.

**What the space-arena candidates actually are.** They split into three kinds, and only the first is worth adding:

1. *Plurals of existing terms* - `ships` 338, `modules` 233, `battles` 165, `blueprints` 59, `shields` 49, `pilots` 47, `commanders` 43, `lasers` 41, `missiles` 41, `engines` 34, `weapons` 30, `clans` 26, `chips` 21, `slots` 21, `members` 20, `reactors` 18, `systems` 18, `skins` 14, `events` 10. Every one of these is the same concept as its singular and currently reaches neither the sidebar nor the LLM prompt. These are the entries to add.
2. *Derivations, not inflections* - `ranking` 59, `ranked` 33, `powerful` 29, `legendary` 14, `armored` 9, `hacking` 9, `massive` 9. Same stem, different part of speech. Adding them would tell a translator to render an adjective as a noun. Do not add.
3. *False positives from very short terms* - `must` 28, `much` 6, `multi` 1 and `music` 1 all come from the Greek-letter term `mu`; `masse` comes from `mass`. The suffix heuristic has no lower bound on term length. Ignore these, and read any candidate for a term under four characters with suspicion.

**Limitation found while running it.** The `--suffix` heuristic is length-blind: a two-character term generates noise at any threshold. The report is still usable because kind 3 is obvious on sight, but a future revision should skip terms shorter than about four characters, or scale the allowed suffix with term length. Not fixed here - it would be an unmeasured change to a tool whose whole point is measuring first.

---

# Part D - Terminology flags

No code. `terminology` is database state (`Unit.extra_flags`), it does not round-trip through TBX (`weblate/formats/ttkit.py:3404-3423` reads `forbidden` only), and Weblate already has the bulk operation.

### Task D1: Flag the source terms of every glossary that should be complete in all languages

**Step 1: Per glossary component, open the source translation and bulk edit**

Go to the glossary component, :guilabel:`Operations` -> :guilabel:`Bulk edit`, query `state:>=empty`, and set :guilabel:`Translation flags to add` to `terminology`.

**Step 2: Verify the flag and the synchronisation**

```bash
./rundev.sh exec -T weblate weblate shell -c "
from weblate.trans.models import Project
p = Project.objects.get(slug='heart-abyss')
for glossary in p.glossaries:
    src = glossary.source_translation
    flagged = src.unit_set.filter(extra_flags__contains='terminology').count()
    print(glossary.slug, flagged, '/', src.unit_set.count())
    for tr in glossary.translation_set.all():
        print('   ', tr.language.code, tr.unit_set.count())
"
```

Required: every source unit is flagged, and every glossary language holds the same number of units. `sync_terminology` (`weblate/glossary/tasks.py:118-128`) creates the missing ones.

**Step 3: Know when this has to be redone**

Deleting and re-importing a glossary component loses the flags, because no glossary file format writes them back. If a glossary is rebuilt - for example by the loc-kit plan's TBX path - repeat this task afterwards.

**Outcome, 2026-08-07.**

Audited first. Every glossary was already complete in every language before the flag was set - space-arena 300 terms across 16 languages, heart-abyss 12 across 3, the other two projects have empty glossaries. So the flag repairs nothing today; it makes that completeness an enforced invariant instead of a coincidence, and that is the only reason to set it.

| Glossary | Terms | Flagged | Action |
|---|---|---|---|
| heart-abyss/terms | 12 | 12/12 | done |
| space-arena/glossary | 300 | 0/300 | needs owner sign-off |
| need-for-greed/glossary | 0 | - | nothing to flag |
| pirate-ships/glossary | 0 | - | nothing to flag |

space-arena is left alone deliberately. Flagging 300 terms commits the project to carrying all 300 into every language it ever adds, which is a product decision, not a mechanical one. The command to run once that is agreed is the same bulk edit, or:

```bash
./rundev.sh exec -T weblate weblate shell -c "
from weblate.auth.models import User
from weblate.trans.bulk import bulk_perform
from weblate.trans.models import Label, Project
p = Project.objects.get(slug='space-arena')
bulk_perform(
    User.objects.get(username='admin'),
    p.glossaries[0].source_translation.unit_set.all(),
    query='state:>=empty',
    target_state=-1,
    add_flags='terminology',
    remove_flags='',
    add_labels=Label.objects.none(),
    remove_labels=Label.objects.none(),
    project=p,
)
"
```

---

# Part E - Documentation and verification

### Task E1: Documentation

**Files:**
- Modify: `docs/admin/machine.rst:69-74` (the `llm-translation-context` section)
- Modify: `docs/admin/machine.rst:11-14` (the sentence about site-wide versus project configuration)
- Modify: `docs/api.rst:1464-1473` (the `machinery_settings` POST description)
- Modify: `AGENTS.md`, the `weblate_customization/` bullet

**Step 1: LLM context list**

Add the note to the enumerated fields:

```rst
LLM-based automatic suggestion services receive additional context about each
translated string, when available. This includes the string context or
monolingual key, additional explanation, the note left by developers,
configured secondary-language translation, plural information, failing quality
checks, and placeholder contents. Matching glossary entries are passed with
their explanations and selected flags, so duplicate glossary terms can be
disambiguated.
```

**Step 2: Inheritance**

Extend the paragraph at `docs/admin/machine.rst:11-14` with a short statement of the new rule: a project configuration overrides the site-wide one field by field, fields it does not set are inherited, and removing the project configuration returns the service to the site-wide settings.

**Step 3: API**

State in `docs/api.rst` that a project-level configuration may contain only the fields it changes and is validated together with the site-wide configuration for that service.

**Step 4: AGENTS.md**

The `weblate_customization/` bullet currently says project-level settings replace the complete global service configuration. That sentence becomes wrong with Task A2. Replace it with the field-by-field rule.

**Step 5: Commit**

```bash
git add docs/admin/machine.rst docs/api.rst AGENTS.md
git commit -m "docs: describe per-field machinery inheritance and the note context"
```

---

### Task E2: Changelog

**Files:**
- Modify: `docs/changes.rst`, the unreleased section at the top

Under :rubric:`Improvements`:

```rst
* Project-level :ref:`automatic suggestion <machine-translation-setup>`
  configuration now overrides the site-wide one field by field, so a project can
  set its own translator persona, style, and language-specific instructions
  without restating credentials.
* LLM-based automatic suggestion services now receive the note left by
  developers for a string, see :ref:`llm-translation-context`.
```

Do not touch released sections. If the loc-kit plan landed first, add these below its bullet.

**Commit:**

```bash
git add docs/changes.rst
git commit -m "docs(changelog): note per-field machinery inheritance and note context"
```

---

### Task E3: Full verification pass

Run once, at the end.

```bash
./rundev.sh test weblate/machinery/tests.py
./rundev.sh test weblate/glossary/tests.py
./rundev.sh test weblate/api/tests.py -k machinery
./rundev.sh test weblate/trans/tests/test_autotranslate.py
uv run prek run --all-files
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

Known pre-existing noise: `uv run reuse lint` fails on six files under `loc_kit_ingest/`, unrelated to this work. `weblate/trans/tests/test_autotranslate.py` is flaky under xdist in the container - compare against a baseline run on an unmodified tree before blaming a change.

**Commit any formatting fixes:**

```bash
git add -A
git commit -m "chore: formatting after project-scoped LLM context"
```

---

## Threat model

`docs/security/threat-model.rst:499-503` already states that configured machine-translation providers are recipients of the data sent to them and that the submitted content varies by provider and enabled feature. Part B sends one more existing unit field to an already-configured recipient; it adds no outbound integration class, no endpoint, and no new trust boundary, so `Conditions that change this model` (line 833) does not apply and the file needs no edit.

Part A does change who may set a provider configuration field - but not the permission: `/machinery/<project>/` already requires `project.edit` (`weblate/machinery/views.py:204-207`) and the API path checks the same permission (`weblate/api/views.py:2379-2382`). Project-scoped configurations keep `allow_private_targets = False`, so the private-target restriction documented at line 373 is unchanged. Record this reasoning in the pull request rather than in the threat model.

---

## Risks and things that will bite

1. **`{}` changes meaning subtly.** Today a project entry of `{}` means "installed, no settings"; afterwards it means "installed, inherits everything". Identical in every current case because no service with a site-wide entry has an empty project entry, and Task A1's `test_empty_entry_inherits_everything` pins it. Do not delete that test.
2. **The UI form saves the resolved values if Task A6 is skipped.** Task A2 alone makes inheritance work for the API and for anything writing `machinery_settings` directly, while the UI keeps freezing a full copy into the project. A2 and A6 belong to the same change.
3. **The current code mutates the project's stored entry.** `Setting.objects.get_settings_dict` builds a fresh dict per call (`weblate/configuration/models.py:13-14`), so nothing is shared between projects today - but `mt_settings[item] = value` puts the project's own dictionary into the result and then writes `_project` into it, so the in-memory `machinery_settings` grows a key that is not configuration. Task A2's `{**a, **b}` stops that, and `test_resolution_does_not_mutate_the_stored_entry` pins it. Do not "optimise" the copy away: `_project` holds a `Project` instance and would be written to the database on the next `save()` of that field.
4. **Notes are not always prose.** Some formats put file references or tool markers into `note`. The model is told to treat it as reference material (rule 21) and never to emit it, but a project with noisy notes will see them consume prompt space. If that shows up, filter at the source - fix the notes - not by special-casing in `llm.py`.
5. **The coverage command reads every source unit of a project.** It is a report, run by hand. Do not wire it into a periodic task without adding pagination.
6. **Terminology flags are database-only.** Any glossary rebuild loses them. Part D step 3 exists for that reason.

---

## Out of scope

- Editorial content. Which persona, style, or language instruction a project should carry is a decision for the people who own that project's text; this plan only makes it settable per project without duplication.
- Morphological glossary matching. Rejected in D5: it needs a per-language stemmer, a new runtime dependency, and a rebuild of a cached hot path, to fix an effect whose size Part C measures first.
- Sending the note to non-LLM machine translation services. They receive plain text and have nowhere to put it.
- Deep merging of nested configuration values. D1 is shallow on purpose.
