# Git-Backed Localization Quality Gate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Connect HCGameLoc Weblate to the game repository through GitHub pull requests and make every agreed deterministic release defect fail before merge or artifact delivery, while reporting subjective quality risks for human review.

**Architecture:** Weblate remains the authoring and review system, while the game repository is the delivery source of truth. Weblate pushes only translation files to a dedicated same-repository branch and opens a pull request; a reusable, offline validator checks the exact PR contents, and the game build imports only artifacts produced from a passing commit. The same pure validation helpers back the Weblate checks and the GitHub gate so authoring feedback and release enforcement cannot drift.

**Tech Stack:** Weblate 2026.8.1.dev0, Python 3.13, `translate-toolkit`, `regex`, Pillow, `openpyxl`, GitHub pull requests and rulesets, GitHub Actions, `uv`, `pytest`, `prek`, Unity batch-mode tests when the game repository is available.

---

## Scope and fixed decisions

This plan uses two repositories:

1. **Weblate repository:** this `weblate-HC` checkout. It owns reusable validation code, Weblate checks/autofixes, deployment registration, and the reusable GitHub Action.
2. **Game repository:** the developer-owned Choice of Life 4 repository. Paths prefixed with `game-repo:` below are new canonical paths in that repository.

The implementation uses these fixed decisions:

- Use Weblate's `GitHub` VCS backend, not plain `Git`, so Weblate opens pull requests.
- Use a dedicated same-repository push branch named `translations/weblate`; do not use a fork and do not push directly to `main`.
- Use a dedicated `weblate-bot` identity with access only to the game repository.
- Use the game repository as the delivery source of truth.
- Store localization in text PO files in Git. Generate `LocalizeData.xlsx` and `LocalizeCommon.xlsx` as deterministic build artifacts; do not edit or email them as an untracked source of truth.
- Run validation on `pull_request`, never `pull_request_target`.
- Give validation jobs `contents: read` and no secrets.
- Pin every GitHub Action to a full 40-character commit SHA.
- Merge Weblate pull requests with a regular merge commit. Do not squash them; Weblate must recognize its original commits after merge.
- Require review in Weblate and use the project commit policy `Only include approved translations`.
- Treat deterministic format, syntax, script, layout, and import defects as blocking.
- Treat spelling, fluency, tone, terminology morphology, and LLM judging as advisory. A probabilistic judge must never be a required status check.
- Do not create a new public endpoint, webhook family, VCS backend, or privileged GitHub Actions workflow.

Official basis:

- Weblate describes VCS access as its core and easiest integration path: <https://docs.weblate.org/en/latest/devel/integration.html>.
- Weblate's GitHub backend opens pull requests, and a non-empty push branch uses an upstream branch instead of a fork: <https://docs.weblate.org/en/latest/admin/code-hosting.html#github-pull-requests>.
- GitHub recommends required status checks through repository rulesets: <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets>.
- GitHub recommends least-privilege `GITHUB_TOKEN` permissions and immutable action references: <https://docs.github.com/en/actions/concepts/security/github_token> and <https://docs.github.com/en/actions/reference/security/secure-use#using-third-party-actions>.
- Weblate documents why squash-merging its pull requests causes repository divergence: <https://docs.weblate.org/en/latest/admin/continuous.html#avoiding-merge-conflicts-by-focusing-on-git-operations>.

## Required external inputs

Do not start the game-repository tasks until the developer supplies all of these:

- GitHub repository owner/name and default branch.
- Production Weblate base URL, project slug, and the current `data` and `common`
  component slugs; do not infer production slugs from the local dev database.
- Confirmation that `translations/weblate` may be created by `weblate-bot`.
- Actual game UI font file and its license-compatible repository path.
- Font size, letter spacing, and maximum pixel width for each constrained UI key.
- Unity version and the existing command that imports localization or builds the game.
- Confirmation that the generated workbooks need exactly these columns:
  `LocalizeString`, `ru_RU`, `en_US`, `tr_TR`, `fr_FR`.

If the developer cannot make PO files canonical, stop before Task 9. Rework only the repository adapter; do not weaken the pull-request or validation gates.

## Canonical game-repository layout

```text
localization/col4/
├── policy.json
├── data/
│   ├── ru.po
│   ├── en_US.po
│   ├── tr_TR.po
│   └── fr.po
└── common/
    ├── ru.po
    ├── en_US.po
    ├── tr_TR.po
    └── fr.po

build/localization/
├── LocalizeData.xlsx
├── LocalizeCommon.xlsx
├── validation-report.json
└── SHA256SUMS
```

The font should remain in its real game path, for example
`Assets/Fonts/GameUI.ttf`; `policy.json` references it instead of copying it into
the localization directory.

## Blocking and advisory policy

### Blocking in both Weblate and Git CI

| Check | Components | Reason |
|---|---|---|
| Strict UTF-8 PO parses successfully | `data`, `common` | Broken or ambiguously encoded resources cannot load. |
| Exact language files and source/target key set | `data`, `common` | Missing, extra, or duplicate resources are runtime defects. |
| No duplicate keys | `data`, `common` | Lookup result would be ambiguous. |
| No empty or fuzzy target in release languages | `data`, `common` | Release artifact must be complete. |
| `game-markup` | `data`, `common` | Tags and placeholders are engine syntax. |
| `game-line-break` | `data`, `common` | `$` and `$$` are engine line separators. |
| Actual newline count | `data`, `common` | Literal newlines must not appear or disappear accidentally. |
| Starting/trailing whitespace and control characters | `data`, `common` | These change rendering or parsing. |
| NFC normalization | `data`, `common` | Prevent byte-level duplicates and unstable diffs. |
| Forbidden script | `fr`, `en_US`, `tr_TR` | Cyrillic leakage is a known machine-output defect. Explicit per-key allowlists are reviewed in Git. |
| `max-size` | `common`, after Pillow/TextMeshPro calibration | Short UI labels have measured bounds. Narrative remains unconstrained. |
| Deterministic XLSX generation and parse-back | both workbooks | Developer receives a reproducible, consumable artifact. |
| Unity localization import smoke test | both workbooks | Final authority for the real runtime importer. |

### Initially advisory

- `end_stop` on `data` until the existing French debt is corrected.
- `punctuation_spacing`, `reused`, `same`, `duplicate`, spelling, and glossary morphology.
- LLM judge results.
- Python pixel-width approximation until it is calibrated against TextMeshPro.

Move an advisory check to blocking only after one full production cycle shows no
false positives and existing findings are zero. Do not create a permanent
baseline that silently accepts new defects.

Two constraints on this promotion path follow from the Cathedral comparison
(`docs/LLM-first/2026-08-11-cathedral-localizer-analysis.md`) and from the
verified glossary check semantics:

- LLM judge results enter `validation-report.json` only through the closed,
  typed advisory schema defined in Task 6. Cathedral lost 19.6 % of judge
  verdicts to free-text parsing; more than half of its correction queue
  (75 963 of 141 936 rows) were unparsed verdicts, not bad translations. An
  unparsable judge response is an infrastructure error, never a silent
  default verdict that routes work.
- Never promote `check_glossary` wholesale to blocking or to Weblate
  `enforced_checks`: the check fires on the union of its hard part
  (`exact`/`read-only`/`forbidden`, deterministic) and its advisory
  morphology part, so enforcing it would block on morphological
  uncertainty. Verified on the dev instance in both term orders:
  `docs/misc/glossary-or-probe.py`. The deterministic strengthening path is
  per-term `exact` mode in the Weblate glossary. The offline gate never
  reads the glossary at all: it lives in the Weblate database, not in the
  game repository.

---

## Execution prerequisite: isolated worktrees

Create one worktree per repository before implementation. Do not run either
application stack from both worktrees at once; the Weblate dev Docker stack has
fixed shared ports.

```bash
# From the Weblate repository.
git worktree add ~/.config/superpowers/worktrees/weblate/feat-git-localization-gate \
  -b feat/git-localization-gate

# From the game repository after it is supplied.
git worktree add ~/.config/superpowers/worktrees/col4/feat-localization-gate \
  -b feat/localization-gate
```

Record the current targeted baselines before changing code:

```bash
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_autofixes.py -n0 -q
```

Expected: existing tests pass. If either command fails, stop and establish whether
the failure reproduces on `main` before continuing.

---

### Task 1: Add the closed validation policy schema

**Files:**

- Modify: `weblate_customization/pyproject.toml`
- Create: `weblate_customization/src/weblate_customization/validation/__init__.py`
- Create: `weblate_customization/src/weblate_customization/validation/model.py`
- Create: `weblate_customization/src/weblate_customization/validation/policy.py`
- Create: `weblate_customization/tests/test_validation_policy.py`
- Create: `weblate_customization/tests/fixtures/validation/policy.json`

#### Step 1: Add exact standalone dependencies

Set the package dependencies to the versions already used by the main project:

```toml
[project]
dependencies = [
  "openpyxl==3.1.5",
  "Pillow==12.3.0",
  "regex==2026.7.19",
  "translate-toolkit==3.19.17",
]
description = "Hero Craft Weblate customizations"
name = "weblate-customization"
requires-python = ">=3.12"
version = "0.2.0"

[project.scripts]
hcgameloc-l10n = "weblate_customization.validation.cli:main"
```

Do not add Django as a dependency. The validation package must import and run
offline without a Weblate process.

#### Step 2: Write failing policy tests

Cover all schema boundaries:

```python
def test_policy_loads_closed_schema(tmp_path: Path) -> None:
    policy = load_policy(FIXTURE_POLICY)
    assert policy.source_language == "ru"
    assert [item.name for item in policy.components] == ["data", "common"]


def test_policy_rejects_unknown_field(tmp_path: Path) -> None:
    document = json.loads(FIXTURE_POLICY.read_text())
    document["surprise"] = True
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document))
    with pytest.raises(PolicyError, match="unknown field.*surprise"):
        load_policy(path)


def test_policy_rejects_path_outside_repository(tmp_path: Path) -> None:
    document = json.loads(FIXTURE_POLICY.read_text())
    document["components"][0]["file_mask"] = "../../outside/{language}.po"
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document))
    with pytest.raises(PolicyError, match="repository-relative"):
        load_policy(path)
```

Also test duplicate languages, duplicate component names, an absent source
language, a file mask without `{language}`, a layout constraint without a font,
and unknown severity values.

#### Step 3: Run the tests to verify red

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_policy.py -q
```

Expected: FAIL because the validation modules do not exist.

#### Step 4: Implement immutable policy models

Use frozen dataclasses and no mutable default values:

```python
class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    OFF = "off"


@dataclass(frozen=True)
class LayoutPolicy:
    font: str
    font_size_px: int
    letter_spacing_px: float
    replacements: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ComponentPolicy:
    name: str
    file_mask: str
    languages: tuple[str, ...]
    forbidden_scripts: tuple[tuple[str, tuple[str, ...]], ...]
    allowed_script_keys: tuple[str, ...]
    severity_overrides: tuple[tuple[str, Severity], ...]
    layout: LayoutPolicy | None
    xlsx_output: str
    xlsx_sheet: str


@dataclass(frozen=True)
class ValidationPolicy:
    schema_version: int
    source_language: str
    components: tuple[ComponentPolicy, ...]
```

`load_policy` must reject unknown fields, absolute paths, `..` traversal, empty
language sets, duplicate names, and schema versions other than `1`. Resolve all
runtime paths relative to the repository root, not the process working directory.

#### Step 5: Run the policy tests

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_policy.py -q
```

Expected: PASS.

#### Step 6: Commit

```bash
git add \
  weblate_customization/pyproject.toml \
  weblate_customization/src/weblate_customization/validation/__init__.py \
  weblate_customization/src/weblate_customization/validation/model.py \
  weblate_customization/src/weblate_customization/validation/policy.py \
  weblate_customization/tests/test_validation_policy.py \
  weblate_customization/tests/fixtures/validation/policy.json
git commit -m "feat(validation): add localization policy schema"
```

---

### Task 2: Validate monolingual PO repository structure

**Files:**

- Create: `weblate_customization/src/weblate_customization/validation/po.py`
- Create: `weblate_customization/src/weblate_customization/validation/structure.py`
- Create: `weblate_customization/tests/test_validation_structure.py`
- Create: `weblate_customization/tests/fixtures/validation/data/ru.po`
- Create: `weblate_customization/tests/fixtures/validation/data/fr.po`

#### Step 1: Write failing structural tests

Create one test for each observable contract:

- valid source and target files pass;
- malformed or non-UTF-8 PO returns `po.parse`;
- an unexpected language file returns `po.unexpected-language`;
- duplicate `msgid` returns `po.duplicate-key`;
- missing target key returns `po.missing-key`;
- unexpected target key returns `po.unexpected-key`;
- blank target returns `po.empty-target`;
- fuzzy target returns `po.fuzzy-target`;
- plural units return `po.plural-unsupported`;
- PO type comments retain `max-size:N` for the layout validator;
- header entries are ignored;
- diagnostics are sorted identically on repeated runs.

Example:

```python
def test_target_key_set_must_equal_source(tmp_path: Path) -> None:
    result = validate_component(load_policy(FIXTURE_POLICY).components[0], ROOT)
    assert [(item.code, item.language, item.key) for item in result] == []

    target = tmp_path / "fr.po"
    target.write_text('msgid "ONLY_SOURCE"\nmsgstr "Seulement"\n')
    result = validate_po_pair(SOURCE_PO, target, language="fr")
    assert [item.code for item in result] == ["po.missing-key"]
```

#### Step 2: Run the tests to verify red

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_structure.py -q
```

Expected: FAIL because `validation.po` and `validation.structure` do not exist.

#### Step 3: Implement the PO loader

Use Translate Toolkit, which already defines the repository's PO semantics:

```python
from translate.storage.po import pofile


def load_po(path: Path) -> tuple[POEntry, ...]:
    try:
        content = path.read_bytes()
        content.decode("utf-8", errors="strict")
        store = pofile.parsestring(content)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise POParseError(path, str(error)) from error

    entries: list[POEntry] = []
    seen: set[str] = set()
    for unit in store.units:
        if unit.hasplural():
            raise POUnsupportedPluralError(path, unit.getid())
        if unit.isheader():
            continue
        key = unit.getid()
        if key in seen:
            raise PODuplicateKeyError(path, key)
        seen.add(key)
        entries.append(
            POEntry(
                key=key,
                target=str(unit.target),
                fuzzy=bool(unit.isfuzzy()),
                type_flags=parse_po_type_flags(unit.typecomments),
            )
        )
    return tuple(entries)
```

Reject plural units because the game workbook schema has one cell per key and
language. Do not strip values. Leading/trailing whitespace is validation input,
not parser noise. Scan the file-mask directory and reject every `*.po` whose
language is not declared by policy; do not silently ignore stale release files.

#### Step 4: Implement exact source-target comparison

Compare sets first, then values. Never silently intersect key sets. Emit one
diagnostic per key and stable-sort by `(component, language, key, code)`.

#### Step 5: Run the structural tests

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_structure.py -q
```

Expected: PASS.

#### Step 6: Commit

```bash
git add \
  weblate_customization/src/weblate_customization/validation/po.py \
  weblate_customization/src/weblate_customization/validation/structure.py \
  weblate_customization/tests/test_validation_structure.py \
  weblate_customization/tests/fixtures/validation/data/ru.po \
  weblate_customization/tests/fixtures/validation/data/fr.po
git commit -m "feat(validation): validate localization repository structure"
```

---

### Task 3: Extract existing engine syntax rules for Git CI

The line-break check and autofix described in
`docs/plans/2026-08-10-game-line-break-rule.md` already exist in this checkout.
Move their pure logic without changing behavior; do not implement a second
separator algorithm.

**Files:**

- Create: `weblate_customization/src/weblate_customization/validation/syntax.py`
- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Modify: `weblate_customization/src/weblate_customization/autofixes.py`
- Modify: `weblate_customization/tests/test_checks.py`
- Modify: `weblate_customization/tests/test_autofixes.py`
- Create: `weblate_customization/tests/test_validation_syntax.py`

#### Step 1: Write failing pure syntax tests

Cover:

- exact markup and placeholder multiset passes;
- lost, added, or attribute-changed tags fail;
- `{0}`, `{c}`, `{SEASON}`, `%PLAYER%`, and `%SHIP%` are preserved;
- one `$` lost out of two fails;
- whitespace beside `$` fails for U+0020, TAB, U+00A0, U+2009, and U+202F;
- `5 $` in a source is treated as currency and is not rewritten;
- a target-only `$` is ignored when the source does not establish separator syntax.

#### Step 2: Run the tests to verify red

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_syntax.py -q
```

Expected: FAIL because the pure helpers do not exist.

#### Step 3: Implement pure syntax functions

The module must have no Django imports:

```python
TAG_PATTERN = regex.compile(
    r"</?(color|link|size|b|i|u|s)(?:=[^>]*)?/?>",
    regex.IGNORECASE,
)
PLACEHOLDER_PATTERN = regex.compile(r"\{[^{}]*\}|%[A-Z][A-Z0-9_]+%")
SEPARATOR_SPACE = r"[ \t\u00a0\u2009\u202f]"
SEPARATOR_HUGGED = regex.compile(rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}")
SEPARATOR_LOOSE_IN_SOURCE = regex.compile(
    rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}|^\$|\$$"
)


def extract_markup_tokens(text: str) -> tuple[str, ...]:
    combined = rf"(?:{TAG_PATTERN.pattern})|(?:{PLACEHOLDER_PATTERN.pattern})"
    return tuple(match.group() for match in regex.finditer(combined, text))


def markup_matches(source: str, target: str) -> bool:
    return sorted(extract_markup_tokens(source)) == sorted(
        extract_markup_tokens(target)
    )


def separator_is_tight(source: str) -> bool:
    return "$" in source and not SEPARATOR_LOOSE_IN_SOURCE.search(source)


def line_separator_matches(source: str, target: str) -> bool:
    if not separator_is_tight(source):
        return True
    return source.count("$") == target.count("$") and not SEPARATOR_HUGGED.search(
        target
    )


def fix_line_separator_spacing(source: str, target: str) -> str:
    if not separator_is_tight(source):
        return target
    return regex.sub(rf"{SEPARATOR_SPACE}*\${SEPARATOR_SPACE}*", "$", target)
```

#### Step 4: Delegate the Weblate check classes to pure helpers

Keep check IDs stable:

```python
class GameMarkupCheck(TargetCheck):
    check_id = "game-markup"
    # Existing translated labels remain unchanged.

    def check_single(self, source: str, target: str, unit) -> bool:
        return not markup_matches(source, target)


class GameLineBreakCheck(TargetCheck):
    check_id = "game-line-break"
    name = gettext_lazy("Game line break")
    description = gettext_lazy(
        "The number of $ line separators does not match the source, or "
        "whitespace sits next to a separator."
    )
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        return not line_separator_matches(source, target)
```

Implement `LineSeparatorSpacing` as an `AutoFix` delegating to
`fix_line_separator_spacing`. Honor `ignore-game-line-break` in both mechanisms.

#### Step 5: Run pure and Weblate tests

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_syntax.py -q
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_autofixes.py -n0 -q
```

Expected: PASS. Temporarily replace one valid `$` target with a space and confirm
both the pure validator test and Weblate check test fail before restoring it.

#### Step 6: Commit

```bash
git add \
  weblate_customization/src/weblate_customization/validation/syntax.py \
  weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/src/weblate_customization/autofixes.py \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_autofixes.py \
  weblate_customization/tests/test_validation_syntax.py
git commit -m "feat(checks): enforce game syntax in Weblate and CI"
```

---

### Task 4: Add Unicode, whitespace, and target-script validation

**Files:**

- Create: `weblate_customization/src/weblate_customization/validation/text.py`
- Create: `weblate_customization/tests/test_validation_text.py`
- Modify: `weblate_customization/tests/fixtures/validation/policy.json`

#### Step 1: Write failing text-integrity tests

Cover:

- NFC text passes;
- decomposed text emits `text.not-nfc`;
- unpaired control characters emit `text.control-character`;
- source and target leading/trailing whitespace mismatch emits
  `text.boundary-whitespace`;
- a literal newline-count mismatch emits `text.newline-count`;
- Cyrillic in `fr`, `en_US`, or `tr_TR` emits `text.forbidden-script`;
- a key listed in `allowed_script_keys` passes;
- punctuation, NBSP, and narrow NBSP are not classified as forbidden script;
- incorrect French spacing around `;`, `:`, `?`, `!`, `«`, and `»` emits the
  advisory `text.french-punctuation-spacing`;
- adding or losing a final full stop emits advisory `text.end-stop`;
- one source rendered by multiple different targets emits advisory
  `corpus.inconsistent-translation`.

For `corpus.inconsistent-translation`, take regression fixtures from the
measured col4/fr drift corpus: 84 of 146 repeated-source groups render
differently while the stock Weblate `inconsistent` check flags none of them
(`docs/LLM-first/plans/2026-08-14-intra-component-consistency-check.md`).

Use the measured defects as regression fixtures:

```python
@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("fr", 'Formulaire "6ЕСП0ЛЗНА9_БМЖКА3532"'),
        ("fr", "Чернобог"),
    ],
)
def test_cyrillic_leak_is_blocking(language: str, text: str) -> None:
    assert [
        item.code for item in validate_text("KEY", "Исходник", text, language, POLICY)
    ] == ["text.forbidden-script"]
```

#### Step 2: Run the tests to verify red

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_text.py -q
```

Expected: FAIL because `validation.text` does not exist.

#### Step 3: Implement deterministic text and corpus checks

Use `unicodedata.normalize("NFC", text) == text`. Detect scripts only on
alphabetic code points; do not classify punctuation or digits. Keep the
allowlist as exact keys in version-controlled policy. Do not add regex-based
allowlists or arbitrary executable policy. Encode the French spacing table as
data with explicit tests matching Weblate's current punctuation-spacing
behavior. Group repeated-source units in one component to find inconsistent
translations, and keep the grouping key and source normalization in a single
helper inside this validation package: the planned Weblate-side repeat-drift
check (`docs/LLM-first/plans/2026-08-14-intra-component-consistency-check.md`)
must import that helper rather than re-derive the groups, the same way the
custom autofixes import the separator regexes from the custom checks. Two
implementations of the grouping are exactly the authoring/enforcement drift
this plan forbids. Severity comes from policy; these advisory diagnostics
must not change the process exit code until promoted.

#### Step 4: Run the text tests

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_text.py -q
```

Expected: PASS.

#### Step 5: Commit

```bash
git add \
  weblate_customization/src/weblate_customization/validation/text.py \
  weblate_customization/tests/test_validation_text.py \
  weblate_customization/tests/fixtures/validation/policy.json
git commit -m "feat(validation): reject Unicode and script defects"
```

---

### Task 5: Add deterministic one-line UI width validation

**Files:**

- Create: `weblate_customization/src/weblate_customization/validation/layout.py`
- Create: `weblate_customization/tests/test_validation_layout.py`
- Create: `weblate_customization/tests/fixtures/validation/common/ru.po`

#### Step 1: Write failing layout tests

Inject a Pillow font object in unit tests so tests do not depend on a workstation
font. Store key-specific limits in source PO comments such as
`#, max-size:300`; this is the same form Weblate imports as a unit flag. Cover:

- text exactly at the width passes;
- text one pixel over emits `layout.max-size`;
- placeholder replacements are applied before measurement;
- a source unit without a `max-size` flag passes;
- `data` has no layout policy and is never measured;
- missing production font path is a configuration error;
- malformed or duplicate `max-size` flags are rejected.

#### Step 2: Run the tests to verify red

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_layout.py -q
```

Expected: FAIL because `validation.layout` does not exist.

#### Step 3: Implement one-line measurement

Use the actual font and explicit letter spacing:

```python
def rendered_width(
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    letter_spacing_px: float,
) -> float:
    grapheme_count = len(regex.findall(r"\X", text))
    spacing = max(0, grapheme_count - 1) * letter_spacing_px
    return float(font.getlength(text)) + spacing
```

Parse `max-size:N` from each source PO unit's type comments, replace configured
placeholders before measuring, and report `actual_px`, `maximum_px`, and the
overage percentage. Support one line only in schema version 1; do not invent a
TextMeshPro wrapping algorithm in Python.

#### Step 4: Run layout tests

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_layout.py -q
```

Expected: PASS.

#### Step 5: Calibrate before making the check blocking

In the game repository, compare Python and TextMeshPro widths for at least 20
representative labels, including `GO_TO_FLAG`, `RESET_DATA`, `SKILLS`, and
`FON_ANIMATIONS`. Record results in the pull request. If any Python pass is a
Unity overflow, keep Python layout advisory and make the Unity job the sole
blocking layout gate.

#### Step 6: Commit

```bash
git add \
  weblate_customization/src/weblate_customization/validation/layout.py \
  weblate_customization/tests/test_validation_layout.py \
  weblate_customization/tests/fixtures/validation/common/ru.po
git commit -m "feat(validation): check UI labels against pixel budgets"
```

---

### Task 6: Add a deterministic CLI and machine-readable reports

**Files:**

- Create: `weblate_customization/src/weblate_customization/validation/runner.py`
- Create: `weblate_customization/src/weblate_customization/validation/report.py`
- Create: `weblate_customization/src/weblate_customization/validation/cli.py`
- Create: `weblate_customization/src/weblate_customization/validation/__main__.py`
- Create: `weblate_customization/tests/test_validation_cli.py`

#### Step 1: Write failing CLI tests

Test the public process contract with `subprocess.run`:

- valid repository exits `0`;
- blocking diagnostics exit `1`;
- invalid policy or unreadable file exits `2`;
- JSON report bytes are identical across two runs;
- GitHub annotations escape `%`, CR, and LF;
- output is stable-sorted;
- no command performs a network request;
- a policy path or symlink escaping the repository root exits `2`;
- `validate-changes` accepts only explicitly allowed paths;
- `validate-changes` rejects additions, modifications, deletions, and both sides
  of a rename outside the allowlist.

#### Step 2: Run the tests to verify red

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_cli.py -q
```

Expected: FAIL because the CLI does not exist.

#### Step 3: Implement explicit subcommands

```text
hcgameloc-l10n validate --root . --policy localization/col4/policy.json \
  --json-report build/localization/validation-report.json --github

hcgameloc-l10n export-xlsx --root . \
  --policy localization/col4/policy.json --output-dir build/localization

hcgameloc-l10n validate-changes --root . --base <BASE_SHA> --head <HEAD_SHA> \
  --allowed-paths "$ALLOWED_PATHS"
```

The `validate` result owns these exit codes:

```python
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_CONFIGURATION = 2
```

`--github` prints annotations only; it does not call the GitHub API and requires
no token. JSON uses UTF-8, `ensure_ascii=False`, sorted keys, a final newline, and
no timestamps. `validate-changes` invokes `git diff --name-status -z` with an
argument vector, never a shell, and checks both the old and new name of renames.

The report schema reserves a typed `advisories` array with a closed severity
enum for probabilistic findings. A future LLM judge writes only into that
slot, produces its verdict through structured output with a strict JSON
schema (the contract `weblate_customization` machinery already uses for
  OpenRouter), and treats an unparsable model response as an infrastructure
error, never as a default verdict or a finding. Cathedral's correction queue
doubled because unparsed free-text verdicts were routed as findings.

`runner.py` must apply the structural, syntax, text, corpus, and layout helpers
to every declared component and language. It must not reimplement any check in
the CLI layer.

#### Step 4: Run the CLI tests

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_cli.py -q
```

Expected: PASS.

#### Step 5: Smoke-test the fixture repository

```bash
uv run --project weblate_customization hcgameloc-l10n validate \
  --root weblate_customization/tests/fixtures/validation \
  --policy policy.json \
  --json-report /tmp/localization-validation.json
```

Expected: exit `0` and a JSON report with zero blocking findings.

#### Step 6: Commit

```bash
git add \
  weblate_customization/src/weblate_customization/validation/runner.py \
  weblate_customization/src/weblate_customization/validation/report.py \
  weblate_customization/src/weblate_customization/validation/cli.py \
  weblate_customization/src/weblate_customization/validation/__main__.py \
  weblate_customization/tests/test_validation_cli.py
git commit -m "feat(validation): add offline localization gate CLI"
```

---

### Task 7: Generate reproducible game XLSX artifacts

**Files:**

- Create: `weblate_customization/src/weblate_customization/validation/xlsx.py`
- Create: `weblate_customization/tests/test_validation_xlsx.py`
- Modify: `weblate_customization/tests/fixtures/validation/policy.json`

#### Step 1: Write failing exporter tests

For both component shapes, test:

- exact header order;
- exact source-unit order from `ru.po`;
- correct language column mapping;
- `$`, tags, whitespace, and Unicode preserved byte-for-text;
- text beginning with `=`, `+`, `-`, or `@` is stored as text, never an Excel formula;
- export is read back and compared cell-for-cell;
- two exports from the same inputs have the same SHA-256 digest;
- missing or extra target keys abort before writing the destination.

#### Step 2: Run the tests to verify red

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_xlsx.py -q
```

Expected: FAIL because `validation.xlsx` does not exist.

#### Step 3: Implement the exporter

Write one worksheet with policy-defined names and columns:

```json
{
  "xlsx": {
    "output": "LocalizeCommon.xlsx",
    "sheet": "LocalizeCommon",
    "columns": {
      "key": "LocalizeString",
      "ru": "ru_RU",
      "en_US": "en_US",
      "tr_TR": "tr_TR",
      "fr": "fr_FR"
    }
  }
}
```

Assign every localization cell as an explicit string. Write to a temporary file,
reopen with `data_only=False`, compare every cell, then atomically replace the
destination.

Normalize the XLSX ZIP before hashing:

- sort member names;
- set every member timestamp to `1980-01-01 00:00:00`;
- use fixed ZIP permissions;
- clear workbook created/modified timestamps.

Do not embed build time, user name, host path, or Git branch in the workbook.

After both workbooks pass parse-back, write sorted `<sha256>  <filename>` lines
to `SHA256SUMS` with a final newline. Hash only the normalized final files.

#### Step 4: Run exporter tests

```bash
uv run --project weblate_customization pytest \
  weblate_customization/tests/test_validation_xlsx.py -q
```

Expected: PASS, including identical SHA-256 output on two runs.

#### Step 5: Commit

```bash
git add \
  weblate_customization/src/weblate_customization/validation/xlsx.py \
  weblate_customization/tests/test_validation_xlsx.py \
  weblate_customization/tests/fixtures/validation/policy.json
git commit -m "feat(validation): export reproducible localization workbooks"
```

---

### Task 8: Publish the validator as a pinned composite GitHub Action

**Files:**

- Create: `.github/actions/localization-validate/action.yml`
- Create: `.github/workflows/localization-validation.yml`
- Modify: `weblate_customization/pyproject.toml`
- Create: `weblate_customization/uv.lock`

#### Step 1: Lock the standalone package

```bash
uv lock --project weblate_customization
uv sync --project weblate_customization --locked
```

Expected: `weblate_customization/uv.lock` is created and a second `uv lock
--check --project weblate_customization` succeeds without changes.

#### Step 2: Create the composite action

The action must:

- install Python 3.13;
- install `uv` at the repository's pinned version;
- run `uv sync --locked` against `weblate_customization`;
- invoke `hcgameloc-l10n validate` against `${GITHUB_WORKSPACE}`;
- write reports under `build/localization`;
- use no secret and make no network call after dependency installation.

Use full action SHAs already present in this repository:

```yaml
name: Validate HCGameLoc localization
description: Run deterministic localization checks against the checked-out repository

inputs:
  policy:
    description: Repository-relative localization policy path
    required: true
  export:
    description: Generate deterministic XLSX artifacts after validation
    required: false
    default: 'true'
  output-dir:
    description: Repository-relative artifact directory
    required: false
    default: build/localization
  bot-actor:
    description: GitHub actor restricted to translation target files
    required: false
    default: weblate-bot
  bot-allowed-paths:
    description: Newline-delimited exact paths the bot may change
    required: false
    default: ''
runs:
  using: composite
  steps:
  - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
    with:
      python-version: '3.13'
  - uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
    with:
      version: 0.12.1
  - name: Install validator
    shell: bash
    run: uv sync --project "$GITHUB_ACTION_PATH/../../../weblate_customization" --locked
  - name: Validate Weblate bot change scope
    if: ${{ github.event_name == 'pull_request' && github.actor == inputs.bot-actor && inputs.bot-allowed-paths != '' }}
    shell: bash
    env:
      ALLOWED_PATHS: ${{ inputs.bot-allowed-paths }}
      BASE_SHA: ${{ github.event.pull_request.base.sha }}
      HEAD_SHA: ${{ github.event.pull_request.head.sha }}
    run: >-
      uv run --project "$GITHUB_ACTION_PATH/../../../weblate_customization"
      hcgameloc-l10n validate-changes
      --root "$GITHUB_WORKSPACE"
      --base "$BASE_SHA"
      --head "$HEAD_SHA"
      --allowed-paths "$ALLOWED_PATHS"
  - name: Validate localization
    shell: bash
    env:
      POLICY: ${{ inputs.policy }}
      REPORT: ${{ github.workspace }}/build/localization/validation-report.json
    run: >-
      uv run --project "$GITHUB_ACTION_PATH/../../../weblate_customization"
      hcgameloc-l10n validate
      --root "$GITHUB_WORKSPACE"
      --policy "$POLICY"
      --json-report "$REPORT"
      --github
  - name: Export localization workbooks
    if: ${{ inputs.export == 'true' }}
    shell: bash
    env:
      OUTPUT_DIR: ${{ inputs.output-dir }}
      POLICY: ${{ inputs.policy }}
    run: >-
      uv run --project "$GITHUB_ACTION_PATH/../../../weblate_customization"
      hcgameloc-l10n export-xlsx
      --root "$GITHUB_WORKSPACE"
      --policy "$POLICY"
      --output-dir "$GITHUB_WORKSPACE/$OUTPUT_DIR"
```

#### Step 3: Add the action's own pull-request test

Use `pull_request`, `permissions: contents: read`, path filters, concurrency, and
a 10-minute timeout. Validate only committed fixtures in this repository.

#### Step 4: Run local workflow linters

```bash
uv run prek run actionlint --files \
  .github/actions/localization-validate/action.yml \
  .github/workflows/localization-validation.yml
uv run prek run zizmor --files .github/workflows/localization-validation.yml
```

Expected: both hooks pass.

#### Step 5: Run the standalone suite

```bash
uv run --project weblate_customization pytest weblate_customization/tests -q
```

Expected: PASS.

#### Step 6: Commit

```bash
git add \
  .github/actions/localization-validate/action.yml \
  .github/workflows/localization-validation.yml \
  weblate_customization/pyproject.toml \
  weblate_customization/uv.lock
git commit -m "ci(validation): publish reusable localization gate"
```

Record the resulting full commit SHA. The game repository must reference that
SHA, never `main` or a mutable tag.

---

### Task 9: Harden deployment of the shared Weblate checks

**Files:**

- Inspect: `dev-docker/docker-compose.yml`
- Inspect: `deploy/environment.example`
- Modify: `deploy/Dockerfile`
- Test: `weblate_customization/tests/test_checks.py`
- Test: `weblate_customization/tests/test_autofixes.py`

#### Step 1: Verify the existing registrations

The current checkout already contains the required comma-separated values:

```yaml
WEBLATE_ADD_CHECK: weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck
WEBLATE_ADD_AUTOFIX: weblate_customization.autofixes.LineSeparatorSpacing
```

Confirm the same values remain in `deploy/environment.example`. Do not add a
second registration mechanism or duplicate an item in either list.

#### Step 2: Verify the production environment

Confirm the real untracked production `.env` contains the same
`WEBLATE_ADD_CHECK` and `WEBLATE_ADD_AUTOFIX` values. Add them only if missing;
never print or commit the rest of that file.

#### Step 3: Extend the image import smoke test

Make `deploy/Dockerfile` assert that the autofix and CLI modules are importable.
If the validator remains inside `weblate_customization`, no second package copy
is needed.

#### Step 4: Run focused tests and Django checks

```bash
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_autofixes.py -n0 -q
./rundev.sh check
```

Expected: tests pass and Django reports no errors.

#### Step 5: Verify registry contents in the dev container

```bash
docker compose -f dev-docker/docker-compose.yml exec -T weblate \
  weblate shell -c \
  'from django.conf import settings; print(settings.CHECK_LIST); print(settings.AUTOFIX_LIST)'
```

Expected: each custom class appears exactly once.

#### Step 6: Commit

```bash
git add deploy/Dockerfile
git commit -m "test(deploy): verify localization gate imports"
```

---

### Task 10: Bootstrap canonical localization files in the game repository

**Files in game repository:**

- Create: `game-repo:localization/col4/policy.json`
- Create: `game-repo:localization/col4/data/ru.po`
- Create: `game-repo:localization/col4/data/en_US.po`
- Create: `game-repo:localization/col4/data/tr_TR.po`
- Create: `game-repo:localization/col4/data/fr.po`
- Create: `game-repo:localization/col4/common/ru.po`
- Create: `game-repo:localization/col4/common/en_US.po`
- Create: `game-repo:localization/col4/common/tr_TR.po`
- Create: `game-repo:localization/col4/common/fr.po`
- Modify: `game-repo:.gitattributes`
- Modify: `game-repo:.gitignore`

#### Step 1: Export each current production translation

Use the existing project-scoped token without printing it. Download through the
translation file endpoint:

```bash
BASE=<production-weblate-api-base>
DATA_COMPONENT_SLUG=<current-data-component-slug>
COMMON_COMPONENT_SLUG=<current-common-component-slug>
for component in "$DATA_COMPONENT_SLUG" "$COMMON_COMPONENT_SLUG"; do
  for language in ru en_US tr_TR fr; do
    curl --fail --silent --show-error \
      -H "Authorization: Token $WEBLATE_API_TOKEN" \
      "$BASE/translations/col4/$component/$language/file/" \
      --output "/tmp/$component-$language.po"
  done
done
```

Expected: eight non-empty PO files parse with `pofile.parsestring`.

#### Step 2: Copy files into canonical paths

Map the confirmed production data component to `data` and the common component
to `common`. Do not normalize, trim, or rewrite content during this bootstrap.

#### Step 3: Create the real policy

Configure:

- source language `ru`;
- release languages `ru`, `en_US`, `tr_TR`, `fr`;
- exact file masks shown above;
- Cyrillic forbidden in `en_US`, `tr_TR`, and `fr`;
- no layout section for `data`;
- actual game font, default font size, letter spacing, and placeholder
  replacements for `common`;
- measured `#, max-size:N` flags on constrained units in `common/ru.po`;
- workbook names and column mapping from Task 7;
- check severities from the blocking/advisory table.

#### Step 4: Add repository text rules

```gitattributes
*.po text eol=lf
*.json text eol=lf
*.xlsx binary
```

Ignore generated artifacts:

```gitignore
/build/localization/
```

#### Step 5: Run the pinned validator locally

From the Weblate worktree SHA recorded in Task 8:

```bash
uvx --from \
  'git+https://github.com/v9833078908/weblate-HC@<FULL_VALIDATOR_COMMIT_SHA>#subdirectory=weblate_customization' \
  hcgameloc-l10n validate \
  --root . \
  --policy localization/col4/policy.json \
  --json-report build/localization/validation-report.json
```

Replace the placeholder with the actual 40-character SHA before committing.
Expected: no configuration error. Existing advisory findings may remain; every
blocking finding must be fixed before continuing.

#### Step 6: Commit in the game repository

```bash
git add \
  .gitattributes \
  .gitignore \
  localization/col4/policy.json \
  localization/col4/data/ru.po \
  localization/col4/data/en_US.po \
  localization/col4/data/tr_TR.po \
  localization/col4/data/fr.po \
  localization/col4/common/ru.po \
  localization/col4/common/en_US.po \
  localization/col4/common/tr_TR.po \
  localization/col4/common/fr.po
git commit -m "chore(localization): add canonical Weblate resources"
```

---

### Task 11: Add the game-repository pull-request gate

**Files in game repository:**

- Create: `game-repo:.github/workflows/localization.yml`
- Modify: `game-repo:.github/CODEOWNERS`
- Modify or create: `game-repo:.github/dependabot.yml`

#### Step 1: Write the workflow with read-only permissions

Use this shape and replace only the validator SHA:

```yaml
name: Localization

on:
  pull_request:
    paths:
    - localization/**
    - Assets/Fonts/**
    - .github/workflows/localization.yml
  push:
    branches:
    - main
    paths:
    - localization/**
    - Assets/Fonts/**

concurrency:
  group: localization-${{ github.event_name }}-${{ github.event.pull_request.number || github.sha }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  localization-static:
    name: localization-static
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
    - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      with:
        persist-credentials: false
        fetch-depth: 0
    - uses: v9833078908/weblate-HC/.github/actions/localization-validate@<FULL_VALIDATOR_COMMIT_SHA>
      with:
        policy: localization/col4/policy.json
        export: 'true'
        output-dir: build/localization
        bot-actor: weblate-bot
        bot-allowed-paths: |
          localization/col4/data/en_US.po
          localization/col4/data/tr_TR.po
          localization/col4/data/fr.po
          localization/col4/common/en_US.po
          localization/col4/common/tr_TR.po
          localization/col4/common/fr.po
    - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
      if: always()
      with:
        name: localization-${{ github.sha }}
        path: build/localization
        if-no-files-found: error
        retention-days: 90
```

Keep `retention-days` at 90, the GitHub maximum: the advisory-to-blocking
promotion rule in "Blocking and advisory policy" needs the
`validation-report.json` findings from one full production cycle, and a
14-day window silently discards that evidence when a cycle runs longer.

#### Step 2: Configure and test the Weblate-bot path gate

The immutable action's `bot-allowed-paths` input allows only the six target PO
files listed in the workflow. It rejects source `ru.po`, policy, fonts,
workflows, scripts, and game code in bot-authored pull requests. Verify this by
opening a bot-authored test PR that changes one target PO and one harmless file
outside localization: `localization-static` must fail. Remove the unrelated
file and confirm the scope check passes.

#### Step 3: Add ownership rules

```text
/localization/col4/policy.json @localization-lead @game-ui-owner
/localization/col4/data/ru.po @localization-lead @game-developer
/localization/col4/common/ru.po @localization-lead @game-ui-owner
/Assets/Fonts/ @game-ui-owner
/.github/workflows/localization.yml @build-owner
```

Translation PO files may be owned by the localization team. Source `ru.po`
requires a developer owner as well.

#### Step 4: Configure Dependabot for action SHAs

Add a `github-actions` ecosystem entry with a weekly schedule. Updates still
require the same workflow and CODEOWNER review.

#### Step 5: Lint and test the workflow

```bash
actionlint .github/workflows/localization.yml
zizmor .github/workflows/localization.yml
```

Expected: no findings.

Open a test PR that deliberately removes `{0}` from one target. Expected:
`localization-static` fails with a key-specific annotation. Restore `{0}` and
confirm the same check passes.

#### Step 6: Commit in the game repository

```bash
git add \
  .github/workflows/localization.yml \
  .github/CODEOWNERS \
  .github/dependabot.yml
git commit -m "ci(localization): require deterministic localization checks"
```

---

### Task 12: Add the authoritative Unity import and layout test

This task must be completed in the game repository by an engineer who can run
its existing Unity project. Do not invent game APIs before opening that
repository.

**Files in game repository:**

- Create: `game-repo:Assets/Tests/Editor/Localization/LocalizationImportTests.cs`
- Create: `game-repo:Assets/Tests/Editor/Localization/LocalizationLayoutTests.cs`
- Modify: `game-repo:.github/workflows/localization.yml`

#### Step 1: Write a failing import test against the existing loader

The test must import both generated workbooks through the same production code
path used by the game, then assert:

- all expected keys load exactly once;
- all four release languages load;
- no parser warning or fallback is emitted;
- `$`, tags, and placeholders survive import;
- an intentionally malformed fixture is rejected.

Run through the project's existing Unity test command. Expected: the new test
fails before the generated artifact is wired to the loader.

#### Step 2: Implement only the adapter needed by the existing loader

Do not add a second localization parser. Reuse the runtime importer and expose
the smallest editor-test seam required to call it.

#### Step 3: Write a failing layout test

For every source key carrying a `max-size` flag in `localization/col4/common/ru.po`:

1. Instantiate the real prefab or scene binding.
2. Apply the localized French text.
3. Use the real TextMeshPro font asset and configured resolution.
4. Force canvas/layout rebuild.
5. Assert `isTextOverflowing` is false and no clipping/truncation mode hides text.

Test each supported mobile aspect ratio, not arbitrary desktop sizes.

#### Step 4: Add the Unity job

Add `localization-unity` to `.github/workflows/localization.yml`. Use the game
repository's existing pinned Unity action or self-hosted command; do not add a
new unreviewed marketplace action. Upload NUnit/JUnit results on failure.

#### Step 5: Verify the test catches a real overflow

Temporarily replace `GO_TO_FLAG` French text with
`Aller a l'indicateur extremement eloigne`. Expected: the Unity job fails.
Restore the text and confirm it passes.

#### Step 6: Commit in the game repository

```bash
git add \
  Assets/Tests/Editor/Localization/LocalizationImportTests.cs \
  Assets/Tests/Editor/Localization/LocalizationLayoutTests.cs \
  .github/workflows/localization.yml
git commit -m "test(localization): validate runtime import and UI layout"
```

---

### Task 13: Configure Weblate-to-GitHub pull requests in staging

This task changes production configuration, not repository code. Perform it
first against a staging Weblate project and a non-default game-repository branch.

#### Step 1: Create the least-privilege identity

Create `weblate-bot`, register Weblate's SSH public key, and grant write access
only to the game repository. Store the GitHub API credential in Weblate's
code-hosting connection or protected production environment. Never put it in a
component URL, Git commit, CI variable, or producer account.

#### Step 2: Configure a staging project

Create `col4-git-staging` with review enabled and translation quality filter
`Only include approved translations`.

#### Step 3: Configure the main `data` component

Use:

```text
Version control system: GitHub
Source code repository: git@github.com:<owner>/<game-repo>.git
Repository branch: <staging-base-branch>
Repository push URL: git@github.com:<owner>/<game-repo>.git
Push branch: translations/weblate
File mask: localization/col4/data/*.po
Monolingual base language file: localization/col4/data/ru.po
Source language: Russian
Push on commit: enabled
```

The push branch must differ from the base branch. Otherwise Weblate changes the
permission and push behavior.

#### Step 4: Configure `common` as a linked component

Use repository URL `weblate://col4-git-staging/data` so both components share one
clone and one PR. Configure its file mask and source file under
`localization/col4/common/`.

#### Step 5: Configure the GitHub webhook

Use the authenticated GitHub App webhook where available. Enable project hooks
only for this project. Confirm one game-repository push queues one update and
does not expose credentials in logs.

#### Step 6: Configure Weblate check enforcement

After current blocking findings are zero, set component enforced checks:

```text
data:
  game-markup
  game-line-break
  begin_space
  end_space
  newline-count
  zero-width-space

common:
  game-markup
  game-line-break
  begin_space
  end_space
  newline-count
  zero-width-space
  max-size  # only after Task 5 calibration passes
```

Add `end_stop` to `common` only after its current findings are corrected. Keep
`end_stop` advisory on `data` during the measured cleanup campaign.

Upload the actual TTF to the project Fonts area, create font group `game`, and
set `font-family:game` plus the verified font size. Store per-key `max-size`
flags in source `ru.po` so Git, Weblate, and CI review the same limits.

Populate and approve all six French glossary targets before enabling this Git
workflow for new automatic translation. The glossary must retain source notes
for `ГИГАХРУЩ`, `САМОСБОР`, `Ячейка`, `Ликвидатор`, `Партия`, and `Концентрат`.
Keep terminology morphology advisory: French inflection and context require
human judgment, but every PR reviewer must see the glossary warnings. Where a
term must appear verbatim in the target (proper names such as `ГИГАХРУЩ`),
set per-term `exact` mode instead of enforcing the whole check; only the hard
part of `check_glossary` is deterministic (see "Blocking and advisory
policy").

#### Step 7: Compare staging against production

For every component and language compare:

- unit count;
- sorted context/key set;
- SHA-256 of every target string encoded as UTF-8;
- empty, fuzzy, translated, and approved counts;
- blocking check IDs and counts.

Expected: keys and targets match current production before accepting any new
translation.

#### Step 8: Exercise the full staging round trip

1. Change one French staging translation.
2. Approve it.
3. Force Weblate commit/push.
4. Confirm a PR from `translations/weblate` appears.
5. Confirm required CI checks run without secrets.
6. Merge with a regular merge commit.
7. Trigger Weblate update.
8. Confirm Weblate has no outgoing/missing-commit alert.

Do not continue to production if any step needs a manual repository reset.

---

### Task 14: Configure GitHub rulesets and repository security

#### Step 1: Restrict workflow tokens

In `Settings -> Actions -> General`, set default workflow permissions to read
repository contents only. Do not enable write tokens for fork pull requests.

#### Step 2: Require immutable Actions

Enable the repository or organization policy requiring full-length commit SHAs
for Actions. Verify the localization workflow still starts.

#### Step 3: Create the default-branch ruleset

Target `main` and require:

- pull request before merge;
- one approval;
- CODEOWNER approval for policy, source PO limits, font, and workflow changes;
- conversation resolution;
- status checks `localization-static` and `localization-unity`;
- blocked force pushes and branch deletion.

Do not grant `weblate-bot` bypass permission. Do not require linear history if
regular merge commits are required.

#### Step 4: Disable squash merge for the repository

Keep `Create a merge commit` available. Disable `Squash and merge` for Weblate
pull requests, preferably repository-wide if the development team agrees.

#### Step 5: Prove the ruleset

Open three test PRs:

1. valid translation change: merge becomes available after review and checks;
2. missing placeholder: merge remains blocked;
3. policy change without CODEOWNER approval: merge remains blocked.

Record screenshots or API output in the implementation PR.

---

### Task 15: Cut production components over without losing translations

#### Step 1: Take recoverable backups

Create a Weblate project backup and record the existing component repository,
file mask, template, source-language, and push settings. Verify the backup can be
listed and downloaded before changing configuration.

#### Step 2: Freeze translation writes

Lock `col4` and its linked components. Wait for active automatic-translation and
repository tasks to finish. Force-commit pending Weblate changes.

#### Step 3: Re-run the production snapshot comparison

Repeat the counts, key sets, target hashes, states, and check-count comparison
from staging. Save it outside the repository as rollback evidence.

#### Step 4: Apply the rehearsed Git settings to production

Update `data` first, then link `common` to `weblate://col4/data`. Do not change
component slugs, project slug, language codes, or user-facing URLs.

#### Step 5: Update and recalculate checks

```bash
./deploy/vps.sh ssh \
  'cd /srv/hcgameloc/deploy && docker compose exec -T weblate weblate updatechecks --all'
```

Use the exact management-command arguments supported by the deployed version;
verify with `weblate updatechecks --help` before executing. Expected: no new
blocking findings.

#### Step 6: Run one production pull-request round trip

Approve one harmless French correction, commit/push, verify CI, merge normally,
and update Weblate. Confirm:

- the production unit contains the merged target;
- no VCS alert exists;
- `translations/weblate` is clean or contains only expected newer commits;
- the generated XLSX artifact imports into Unity.

#### Step 7: Unlock production

Unlock only after the round trip passes. Announce the new workflow: producer
works in Weblate, developer receives reviewed Git pull requests or CI artifacts,
not email attachments.

**Rollback:** If update, push, PR creation, CI, merge recognition, or runtime
import fails, lock the project, restore the previous component configuration and
project backup, and reopen translation work only after the old internal-repo
workflow is verified. Never repair a failed cutover by force-pushing `main`.

---

### Task 16: Document the workflow and finish verification

**Files:**

- Modify: `docs/specs/producer-guide.md`
- Modify: `deploy/README.md`
- Modify: `docs/changes.rst`
- Review: `docs/security/threat-model.rst`

#### Step 1: Update the producer guide

Document:

- producer never needs Git credentials;
- only approved translations are committed;
- failed checks prevent delivery;
- the `translations/weblate` PR is the delivery event;
- generated XLSX is downloaded from the passing workflow artifact;
- manual file handoff is emergency-only and must pass the same CLI and Unity
  importer.

#### Step 2: Update deployment operations

Document bot credential location without secret values, key rotation, webhook
health, push-branch cleanup, failed-push recovery, and the prohibition on squash
merging Weblate PRs.

#### Step 3: Add a concise unreleased changelog entry

Add one entry to the current unreleased section describing GitHub PR delivery
and deterministic release validation. Do not edit a released section.

#### Step 4: Review the threat model

The existing threat model already covers GitHub App connections, VCS pushes,
webhooks, external repository content, credentials, and downstream localization
risk. Because this plan uses the existing VCS backend and adds no public endpoint
or new outbound integration class, no threat-model change is expected. If
implementation adds a custom webhook, endpoint, VCS execution path, add-on
execution capability, or privileged workflow, update
`docs/security/threat-model.rst` in the same change.

#### Step 5: Run focused validation

```bash
uv run --project weblate_customization pytest weblate_customization/tests -q
./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  weblate_customization/tests/test_autofixes.py -n0 -q
uv run prek run --files \
  weblate_customization/pyproject.toml \
  weblate_customization/src/weblate_customization/validation/__init__.py \
  weblate_customization/src/weblate_customization/validation/model.py \
  weblate_customization/src/weblate_customization/validation/policy.py \
  weblate_customization/src/weblate_customization/validation/po.py \
  weblate_customization/src/weblate_customization/validation/structure.py \
  weblate_customization/src/weblate_customization/validation/syntax.py \
  weblate_customization/src/weblate_customization/validation/text.py \
  weblate_customization/src/weblate_customization/validation/layout.py \
  weblate_customization/src/weblate_customization/validation/runner.py \
  weblate_customization/src/weblate_customization/validation/report.py \
  weblate_customization/src/weblate_customization/validation/cli.py \
  weblate_customization/src/weblate_customization/validation/__main__.py \
  weblate_customization/src/weblate_customization/validation/xlsx.py \
  .github/actions/localization-validate/action.yml \
  .github/workflows/localization-validation.yml \
  dev-docker/docker-compose.yml \
  deploy/environment.example \
  deploy/Dockerfile \
  deploy/vps.sh \
  docs/specs/producer-guide.md \
  deploy/README.md \
  docs/changes.rst
```

Expected: all focused tests and hooks pass.

#### Step 6: Run the end-to-end acceptance scenario

The final implementation is complete only when all of these are observed:

1. A Weblate-approved French change opens a same-repository PR from
   `translations/weblate`.
2. Removing a placeholder fails `localization-static` and blocks merge.
3. Adding Cyrillic to French fails `localization-static` and blocks merge.
4. Expanding a constrained UI label fails the Unity layout job and blocks merge.
5. A valid PR passes both required checks and merges with a regular merge commit.
6. Weblate pulls the merge without repository divergence.
7. Two builds of the same localization commit produce byte-identical XLSX files.
8. The generated workbooks import through the real Unity localization loader.
9. A plain unvalidated workbook cannot enter the release build path.

#### Step 7: Commit documentation

```bash
git add \
  docs/specs/producer-guide.md \
  deploy/README.md \
  docs/changes.rst
git commit -m "docs(localization): document Git delivery and release gates"
```

## Completion standard

The project is not complete when Weblate merely opens a pull request. It is
complete when invalid translation content cannot reach the default branch or a
deliverable workbook, the game importer has consumed a passing artifact, and a
rollback has been rehearsed. Prompt improvements and human review reduce how
often the gate fires; they do not replace the gate.
