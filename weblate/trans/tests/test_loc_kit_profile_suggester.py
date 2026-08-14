# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
from importlib import resources
from unittest.mock import patch

import httpx2
from django.test import SimpleTestCase, override_settings

from weblate.trans import loc_kit
from weblate.utils.tests import http_mock

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_request_json(request: httpx2.Request) -> dict:
    return json.loads(request.content or b"{}")


def _one_row_glossary() -> list[list[str]]:
    return [
        ["section", "ru", "en"],
        ["Combat", "Меч", "Sword"],
        ["Magic", "Посох", "Staff"],
    ]


def _stride_two_glossary() -> list[list[str]]:
    return [
        ["ru", "en"],
        ["Меч", "Sword"],
        ["a melee weapon", "a bladed weapon"],
        ["Посох", "Staff"],
        ["a magic weapon", "a two-handed weapon"],
    ]


# --------------------------------------------------------------------------- #
# C2: structural sampler
# --------------------------------------------------------------------------- #


class StructureSampleShapeTest(SimpleTestCase):
    """The sampler preserves headers, coordinates, and distinguishable layouts."""

    def test_one_row_layout_preserves_signatures(self) -> None:
        rows = _one_row_glossary()
        sample = loc_kit.build_glossary_structure_sample(rows, "Sheet1", 131072)
        self.assertEqual(sample["metadata"]["sheet"], "Sheet1")
        self.assertEqual(sample["metadata"]["row_count"], 3)
        self.assertEqual(sample["metadata"]["column_count"], 3)
        # Every row is represented by a signature run. Signatures carry column
        # indexes, cell counts, and bounded value lengths, so the layout stays
        # distinguishable: the header differs from each data row, and the two
        # data rows differ by their term-value lengths.
        runs = sample["signature_runs"]
        self.assertEqual(len(runs), 3)
        # Each run spans exactly one row (counts are all 1 here).
        self.assertEqual(sorted(run["count"] for run in runs), [1, 1, 1])
        # Signatures encode which columns are nonempty plus value lengths.
        for run in runs:
            signature = run["signature"]
            self.assertTrue(
                all(isinstance(pair, list) and len(pair) == 2 for pair in signature)
            )

    def test_identical_rows_collapse_via_rle(self) -> None:
        # Two data rows with identical shape and value lengths collapse.
        rows = [
            ["section", "ru", "en"],
            ["AAA", "BBB", "CCC"],
            ["DDD", "EEE", "FFF"],
        ]
        sample = loc_kit.build_glossary_structure_sample(rows, "Sheet1", 131072)
        runs = sample["signature_runs"]
        # Header is its own run; the two identical-shape data rows collapse.
        self.assertEqual(len(runs), 2)
        collapsed = runs[-1]
        self.assertEqual(collapsed["count"], 2)
        self.assertEqual(collapsed["first_row"], 1)
        self.assertEqual(collapsed["last_row"], 2)

    def test_stride_two_layout_distinguishable(self) -> None:
        rows = _stride_two_glossary()
        sample = loc_kit.build_glossary_structure_sample(rows, "Gloss", 131072)
        runs = sample["signature_runs"]
        # Every row has its own signature because term and description lengths
        # differ across rows; the stride-two structure stays distinguishable
        # rather than collapsing. At least four runs (one per row) exist.
        self.assertGreaterEqual(len(runs), 4)
        # Signatures are preserved for every row coordinate.
        covered = set()
        for run in runs:
            covered.update(range(int(run["first_row"]), int(run["last_row"]) + 1))
        self.assertEqual(covered, set(range(len(rows))))

    def test_headers_and_coordinates_preserved(self) -> None:
        rows = _one_row_glossary()
        sample = loc_kit.build_glossary_structure_sample(rows, "S", 131072)
        # Row 0 (the header) is represented with its full short cells.
        reps = {int(r["row"]): r["cells"] for r in sample["representatives"]}
        self.assertIn(0, reps)
        self.assertEqual(reps[0], ["section", "ru", "en"])
        # Coordinates are 0-based and within bounds.
        for rep in sample["representatives"]:
            self.assertGreaterEqual(int(rep["row"]), 0)
            self.assertLess(int(rep["row"]), len(rows))

    def test_unicode_survives_excerpt(self) -> None:
        rows = [["ru", "en"], ["Раздел", "Section"], ["Термин", "Term"]]
        sample = loc_kit.build_glossary_structure_sample(rows, "U", 131072)
        reps = {int(r["row"]): r["cells"] for r in sample["representatives"]}
        self.assertIn("Раздел", reps[1])
        self.assertIn("Термин", reps[2])


class StructureSampleDeterminismTest(SimpleTestCase):
    """Identical input yields byte-identical output."""

    def test_deterministic_output(self) -> None:
        rows = _one_row_glossary()
        first = loc_kit.build_glossary_structure_sample(rows, "Sheet1", 131072)
        second = loc_kit.build_glossary_structure_sample(rows, "Sheet1", 131072)
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )

    def test_deterministic_serialization_compact(self) -> None:
        rows = _one_row_glossary()
        sample = loc_kit.build_glossary_structure_sample(rows, "Sheet1", 131072)
        # Re-serializing with the same kwargs produces a stable byte string.
        encoded = json.dumps(
            sample, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 131072)


class StructureSampleBoundsTest(SimpleTestCase):
    """A too-small cap returns the size diagnostic instead of a lossy sample."""

    def test_tiny_cap_raises_size_diagnostic(self) -> None:
        rows = _one_row_glossary()
        with self.assertRaises(loc_kit.SampleTooLargeError) as ctx:
            loc_kit.build_glossary_structure_sample(rows, "Sheet1", 8)
        self.assertEqual(str(ctx.exception), loc_kit.SAMPLE_TOO_LARGE)

    def test_zero_cap_raises(self) -> None:
        with self.assertRaises(loc_kit.SampleTooLargeError):
            loc_kit.build_glossary_structure_sample([["a"]], "S", 0)

    def test_no_raw_full_sheet_dump_for_many_rows(self) -> None:
        # A 500-row synthetic sheet with realistic long term values. The
        # signature encoding stores only column indexes and value lengths, so
        # it must be meaningfully smaller than a naive verbatim dump, and the
        # representative payload must stay within the cap by representing only a
        # bounded subset of rows verbatim (the rest live in signature_runs).
        long_term = "Очень длинный термин глоссария с пояснительным текстом " * 3
        long_note = "Подробное пояснение к переводу данного термина. " * 2
        rows = [["section", "ru", "en", "note"]]
        rows.extend(
            [f"sec{i % 4}", long_term, f"term{i:03d}", long_note] for i in range(500)
        )
        naive = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        sample = loc_kit.build_glossary_structure_sample(rows, "Big", 131072)
        encoded = json.dumps(
            sample, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), 131072)
        # The signature-only encoding is far smaller than the verbatim dump.
        self.assertLess(len(encoded) * 2, len(naive))
        # Only a bounded minority of rows carry verbatim cell excerpts; the
        # remainder are represented solely by their structural signature.
        self.assertLess(len(sample["representatives"]), len(rows))
        self.assertGreater(int(sample["omitted_rows"]), 0)


# --------------------------------------------------------------------------- #
# C2: prompt asset
# --------------------------------------------------------------------------- #


class PromptAssetTest(SimpleTestCase):
    """The packaged prompt is loadable and carries the required instructions."""

    def test_prompt_loadable_via_importlib(self) -> None:
        prompt = loc_kit.load_profile_prompt()
        self.assertIsInstance(prompt, str)
        self.assertIn("response envelope", prompt)
        self.assertIn("unsupported", prompt)

    def test_prompt_asset_on_disk_matches_module(self) -> None:
        text = (
            resources.files("weblate.trans.prompts")
            .joinpath("loc_kit_profile.txt")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(text, loc_kit.load_profile_prompt())

    def test_prompt_requires_only_envelope_json(self) -> None:
        prompt = loc_kit.load_profile_prompt()
        self.assertIn("ONLY", prompt)
        self.assertIn("JSON", prompt)


# --------------------------------------------------------------------------- #
# C3: OpenRouter proposal client
# --------------------------------------------------------------------------- #

_OPENROUTER_URL = loc_kit.OPENROUTER_CHAT_COMPLETIONS_URL


def _profile_envelope_content() -> str:
    return json.dumps(
        {
            "status": "profile",
            "profile": {
                "schema_version": 2,
                "components": [
                    {
                        "sheet": "Sheet1",
                        "component": "Proposed",
                        "kind": "tbx",
                        "source_lang": "ru",
                        "languages": [
                            {
                                "code": "ru",
                                "xml_lang": "ru",
                                "column": 1,
                                "header": "ru",
                            },
                            {
                                "code": "en",
                                "xml_lang": "en",
                                "column": 2,
                                "header": "en",
                            },
                        ],
                        "grammar": {
                            "type": "record-map",
                            "regions": [
                                {
                                    "first_record_row": 1,
                                    "last_record_row": 2,
                                    "record_stride": 1,
                                }
                            ],
                            "term_row_offset": 0,
                        },
                        "initial_target_languages": ["en"],
                    }
                ],
            },
            "assumptions": ["Row 0 is the header"],
            "reason": None,
        }
    )


def _ok_response(content: str | None = None) -> None:
    body = {
        "choices": [
            {
                "message": {
                    "content": content
                    if content is not None
                    else _profile_envelope_content()
                }
            }
        ]
    }
    http_mock.register("POST", _OPENROUTER_URL, json=body)


_ENABLED = override_settings(
    LOC_KIT_PROFILE_ANALYSIS_ENABLED=True,
    LOC_KIT_PROFILE_OPENROUTER_KEY="sk-test-secret-do-not-leak",
    LOC_KIT_PROFILE_OPENROUTER_MODEL="openai/gpt-4o",
)


class ProposalShortCircuitTest(SimpleTestCase):
    """No HTTP calls when disabled or misconfigured."""

    @_ENABLED
    @http_mock.activate
    def test_disabled_makes_no_calls(self) -> None:
        with (
            override_settings(LOC_KIT_PROFILE_ANALYSIS_ENABLED=False),
            self.assertRaises(loc_kit.ProfileProposalError),
        ):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "x"}})
        self.assertEqual(len(http_mock.calls), 0)

    @http_mock.activate
    def test_missing_key_makes_no_calls(self) -> None:
        with (
            override_settings(
                LOC_KIT_PROFILE_ANALYSIS_ENABLED=True,
                LOC_KIT_PROFILE_OPENROUTER_KEY="",
                LOC_KIT_PROFILE_OPENROUTER_MODEL="openai/gpt-4o",
            ),
            self.assertRaises(loc_kit.ProfileProposalError),
        ):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "x"}})
        self.assertEqual(len(http_mock.calls), 0)

    @http_mock.activate
    def test_missing_model_makes_no_calls(self) -> None:
        with (
            override_settings(
                LOC_KIT_PROFILE_ANALYSIS_ENABLED=True,
                LOC_KIT_PROFILE_OPENROUTER_KEY="sk-test",
                LOC_KIT_PROFILE_OPENROUTER_MODEL="",
            ),
            self.assertRaises(loc_kit.ProfileProposalError),
        ):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "x"}})
        self.assertEqual(len(http_mock.calls), 0)


class ProposalRequestShapeTest(SimpleTestCase):
    """Exact endpoint, headers, payload fields, and timeout."""

    @_ENABLED
    @http_mock.activate
    def test_exact_endpoint_and_method(self) -> None:
        _ok_response()
        loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})
        self.assertEqual(len(http_mock.calls), 1)
        call = http_mock.calls[0]
        self.assertEqual(call.request.method, "POST")
        self.assertEqual(str(call.request.url), _OPENROUTER_URL)

    @_ENABLED
    @http_mock.activate
    def test_authorization_bearer_header_present(self) -> None:
        _ok_response()
        loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})
        call = http_mock.calls[0]
        self.assertEqual(
            call.request.headers.get("Authorization"),
            "Bearer sk-test-secret-do-not-leak",
        )
        self.assertEqual(call.request.headers.get("Content-Type"), "application/json")

    @_ENABLED
    @http_mock.activate
    def test_secret_never_in_exception_text(self) -> None:
        http_mock.register("POST", _OPENROUTER_URL, status_code=500)
        with self.assertRaises(loc_kit.ProfileProposalError) as ctx:
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})
        self.assertNotIn("sk-test-secret-do-not-leak", str(ctx.exception))

    @_ENABLED
    @http_mock.activate
    def test_payload_required_fields(self) -> None:
        _ok_response()
        sample = {"metadata": {"sheet": "Sheet1"}}
        loc_kit.request_profile_proposal(sample)
        body = _load_request_json(http_mock.calls[0].request)
        self.assertEqual(body["model"], "openai/gpt-4o")
        self.assertIs(body["stream"], False)
        # Strict JSON schema response format.
        response_format = body["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        schema_block = response_format["json_schema"]
        self.assertIs(schema_block["strict"], True)
        root_schema = schema_block["schema"]
        self.assertIs(root_schema["additional_properties"], False)
        for field in ("status", "profile", "assumptions", "reason"):
            self.assertIn(field, root_schema["required"])
        # OpenRouter provider preference.
        self.assertEqual(body["provider"], {"require_parameters": True})
        # Messages: static instruction as system, sample as its own user message.
        messages = body["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], loc_kit.load_profile_prompt())
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(json.loads(messages[1]["content"]), sample)

    @_ENABLED
    @http_mock.activate
    def test_timeout_threaded_to_request(self) -> None:
        captured: dict = {}

        def fake_fetch(method, url, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            response = httpx2.Response(
                200,
                json={
                    "choices": [{"message": {"content": _profile_envelope_content()}}]
                },
            )
            response.request = httpx2.Request(method, url)
            return response

        with patch("weblate.trans.loc_kit.fetch_validated_url", side_effect=fake_fetch):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})
        self.assertEqual(captured["timeout"], loc_kit.OPENROUTER_REQUEST_TIMEOUT)
        self.assertEqual(loc_kit.OPENROUTER_REQUEST_TIMEOUT, 120)


class ProposalResponseHandlingTest(SimpleTestCase):
    """Malformed, 4xx, 5xx, and bad-envelope handling never raise uncaught."""

    @_ENABLED
    @http_mock.activate
    def test_malformed_json_content_returns_typed_failure(self) -> None:
        body = {"choices": [{"message": {"content": "{not valid json"}}]}
        http_mock.register("POST", _OPENROUTER_URL, json=body)
        with self.assertRaises(loc_kit.ProfileProposalError):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})

    @_ENABLED
    @http_mock.activate
    def test_missing_choices_returns_typed_failure(self) -> None:
        http_mock.register("POST", _OPENROUTER_URL, json={"foo": "bar"})
        with self.assertRaises(loc_kit.ProfileProposalError):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})

    @_ENABLED
    @http_mock.activate
    def test_4xx_returns_typed_failure(self) -> None:
        http_mock.register("POST", _OPENROUTER_URL, status_code=401)
        with self.assertRaises(loc_kit.ProfileProposalError) as ctx:
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})
        self.assertIn("401", str(ctx.exception))

    @_ENABLED
    @http_mock.activate
    def test_5xx_returns_typed_failure(self) -> None:
        http_mock.register("POST", _OPENROUTER_URL, status_code=500)
        with self.assertRaises(loc_kit.ProfileProposalError):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})

    @_ENABLED
    @http_mock.activate
    def test_unsupported_envelope_returned(self) -> None:
        content = json.dumps(
            {
                "status": "unsupported",
                "profile": None,
                "assumptions": [],
                "reason": "Too few rows",
            }
        )
        _ok_response(content=content)
        envelope = loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})
        self.assertEqual(envelope["status"], "unsupported")
        self.assertIsNone(envelope["profile"])
        self.assertEqual(envelope["reason"], "Too few rows")

    @_ENABLED
    @http_mock.activate
    def test_bad_envelope_unsupported_with_profile_rejected(self) -> None:
        # status unsupported but profile non-null violates the contract.
        content = json.dumps(
            {
                "status": "unsupported",
                "profile": {"schema_version": 2, "components": []},
                "assumptions": [],
                "reason": "x",
            }
        )
        _ok_response(content=content)
        with self.assertRaises(loc_kit.ProfileProposalError):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})

    @_ENABLED
    @http_mock.activate
    def test_bad_envelope_unsupported_empty_reason_rejected(self) -> None:
        content = json.dumps(
            {
                "status": "unsupported",
                "profile": None,
                "assumptions": [],
                "reason": "",
            }
        )
        _ok_response(content=content)
        with self.assertRaises(loc_kit.ProfileProposalError):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})

    @_ENABLED
    @http_mock.activate
    def test_bad_envelope_profile_status_without_profile_rejected(self) -> None:
        content = json.dumps(
            {
                "status": "profile",
                "profile": None,
                "assumptions": [],
                "reason": None,
            }
        )
        _ok_response(content=content)
        with self.assertRaises(loc_kit.ProfileProposalError):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})

    @_ENABLED
    @http_mock.activate
    def test_network_exception_returns_typed_failure(self) -> None:
        http_mock.register_exception(
            "POST", _OPENROUTER_URL, httpx2.ConnectError("boom")
        )
        with self.assertRaises(loc_kit.ProfileProposalError):
            loc_kit.request_profile_proposal({"metadata": {"sheet": "Sheet1"}})


# --------------------------------------------------------------------------- #
# C4: local validation of a candidate profile
# --------------------------------------------------------------------------- #


def _record_map_rows() -> list[list[str]]:
    return [
        ["domain", "ru", "en", "note_ru", "note_en"],
        ["Персонажи", "Герой", "Hero", "Главный протагонист", "Main protagonist"],
        ["Оружие", "Меч", "Sword", "Ближний бой", "Melee weapon"],
    ]


def _record_map_document(**component_overrides) -> dict:
    component = {
        "sheet": "Glossary",
        # A candidate always proposes some name; the server overrides it.
        "component": "ModelChosenName",
        "kind": "tbx",
        "source_lang": "ru",
        "header_row": 1,
        "languages": [
            {"code": "ru", "xml_lang": "ru", "column": 2, "header": "ru"},
            {"code": "en", "xml_lang": "en", "column": 3, "header": "en"},
        ],
        "grammar": {
            "type": "record-map",
            "skip_rows": [],
            "regions": [
                {"first_record_row": 2, "last_record_row": 3, "record_stride": 1}
            ],
            "term_row_offset": 0,
            "section_field": {"column": 1, "header": "domain", "row_offset": 0},
            "notes": [
                {"scope": "source", "column": 4, "header": "note_ru", "row_offset": 0},
                {
                    "scope": "target",
                    "language": "en",
                    "column": 5,
                    "header": "note_en",
                    "row_offset": 0,
                },
            ],
        },
        "initial_target_languages": ["en"],
    }
    component.update(component_overrides)
    return {"schema_version": 2, "components": [component]}


def _validate(document=None, rows=None, sheet="Glossary", name="Draft-Glossary"):
    return loc_kit.validate_glossary_profile(
        profile_document=document if document is not None else _record_map_document(),
        rows=rows if rows is not None else _record_map_rows(),
        sheet_name=sheet,
        component_name=name,
    )


class LocKitGlossaryValidationTest(SimpleTestCase):
    """A candidate profile is validated against the real sheet, locally."""

    def test_valid_candidate_produces_a_preview(self) -> None:
        preview = _validate()

        self.assertEqual(preview.source_language, "ru")
        self.assertEqual(preview.target_languages, ("en",))
        self.assertEqual(preview.term_count, 2)
        # Two source notes and two target notes across two records.
        self.assertEqual(preview.note_count, 4)
        self.assertEqual(preview.warnings, ())
        self.assertEqual(sorted(preview.files), ["en.tbx"])
        self.assertIn(b"Hero", preview.files["en.tbx"])

    def test_preview_exposes_a_bounded_term_sample(self) -> None:
        preview = _validate()
        first = preview.terms[0]

        self.assertEqual(first.section, "Персонажи")
        self.assertEqual(first.source, "Герой")
        self.assertEqual(first.targets, {"en": "Hero"})
        self.assertEqual(first.source_explanation, "Главный протагонист")
        self.assertEqual(first.target_explanations, {"en": "Main protagonist"})
        self.assertLessEqual(len(preview.terms), loc_kit.PREVIEW_TERM_LIMIT)

    def test_server_generated_component_name_replaces_the_candidate_name(self) -> None:
        preview = _validate(name="Draft-Glossary")

        self.assertEqual(preview.component, "Draft-Glossary")
        self.assertNotIn("ModelChosenName", preview.profile_json)
        self.assertIn("Draft-Glossary", preview.profile_json)

    def test_downloadable_profile_round_trips(self) -> None:
        """The preview's JSON is exactly what a correction upload accepts."""
        preview = _validate()
        reparsed = json.loads(preview.profile_json)
        again = _validate(document=reparsed)

        self.assertEqual(again.term_count, preview.term_count)
        self.assertEqual(again.files, preview.files)

    def test_llm_and_manual_paths_produce_identical_results(self) -> None:
        document = _record_map_document()
        envelope = {
            "status": "profile",
            "profile": document,
            "assumptions": ["column 1 is the domain"],
            "reason": None,
        }
        from_llm = _validate(document=loc_kit.profile_document_from_envelope(envelope))
        from_manual = _validate(document=document)

        self.assertEqual(from_llm.profile_json, from_manual.profile_json)
        self.assertEqual(from_llm.files, from_manual.files)
        self.assertEqual(from_llm.term_count, from_manual.term_count)

    def test_unsupported_envelope_surfaces_the_reason(self) -> None:
        envelope = {
            "status": "unsupported",
            "profile": None,
            "assumptions": [],
            "reason": "the layout has no consistent record stride",
        }
        with self.assertRaises(loc_kit.GlossaryProfileError) as ctx:
            loc_kit.profile_document_from_envelope(envelope)
        self.assertIn("no consistent record stride", str(ctx.exception))

    def test_profile_for_another_sheet_is_rejected(self) -> None:
        with self.assertRaises(loc_kit.GlossaryProfileError) as ctx:
            _validate(sheet="OtherSheet")
        self.assertIn("selected", str(ctx.exception).lower())

    def test_more_than_one_component_is_rejected(self) -> None:
        document = _record_map_document()
        document["components"].append(dict(document["components"][0]))
        with self.assertRaises(loc_kit.GlossaryProfileError):
            _validate(document=document)

    def test_v1_schema_is_rejected_for_this_flow(self) -> None:
        document = _record_map_document()
        document["schema_version"] = 1
        with self.assertRaises(loc_kit.GlossaryProfileError):
            _validate(document=document)

    def test_po_kind_is_rejected(self) -> None:
        with self.assertRaises(loc_kit.GlossaryProfileError):
            _validate(document=_record_map_document(kind="po"))

    def test_source_only_glossary_is_rejected(self) -> None:
        with self.assertRaises(loc_kit.GlossaryProfileError):
            _validate(document=_record_map_document(initial_target_languages=[]))

    def test_flag_declaration_is_rejected(self) -> None:
        document = _record_map_document()
        document["components"][0]["grammar"]["flags"] = [{"column": 6}]
        with self.assertRaises(loc_kit.GlossaryProfileError):
            _validate(document=document)

    def test_header_mismatch_is_rejected(self) -> None:
        rows = _record_map_rows()
        rows[0][3] = "comment_ru"  # profile still declares note_ru
        with self.assertRaises(loc_kit.GlossaryProfileError) as ctx:
            _validate(rows=rows)
        self.assertTrue(ctx.exception.details)

    def test_unmapped_populated_cell_is_rejected(self) -> None:
        rows = _record_map_rows()
        rows[0].append("status")
        rows[1].append("approved")
        with self.assertRaises(loc_kit.GlossaryProfileError):
            _validate(rows=rows)

    def test_missing_target_term_is_rejected(self) -> None:
        rows = _record_map_rows()
        rows[1][2] = ""
        with self.assertRaises(loc_kit.GlossaryProfileError):
            _validate(rows=rows)

    def test_parse_back_failure_yields_no_files(self) -> None:
        """A renderer that loses data must block the preview entirely."""
        real = loc_kit.validate_glossary_profile

        def broken_validate(component, result, out_dir):
            # ruff: ignore[import-outside-top-level]
            from loc_kit_ingest.model import Diagnostic, Severity

            return (
                Diagnostic(
                    Severity.ERROR,
                    "render.value_mismatch",
                    component.component,
                    "",
                    0,
                    "simulated parse-back mismatch",
                ),
            )

        with (
            patch("loc_kit_ingest.writer.validate_rendered_component", broken_validate),
            self.assertRaises(loc_kit.GlossaryProfileError) as ctx,
        ):
            real(
                profile_document=_record_map_document(),
                rows=_record_map_rows(),
                sheet_name="Glossary",
                component_name="Draft-Glossary",
            )
        self.assertIn("parse-back", str(ctx.exception))


class LocKitGlossaryFullTermsTest(SimpleTestCase):
    """The append flow needs every validated term, not the preview sample."""

    @staticmethod
    def _document(term_count: int) -> dict:
        return _record_map_document(
            grammar={
                "type": "record-map",
                "skip_rows": [],
                "regions": [
                    {
                        "first_record_row": 2,
                        "last_record_row": 1 + term_count,
                        "record_stride": 1,
                    }
                ],
                "term_row_offset": 0,
                "section_field": {"column": 1, "header": "domain", "row_offset": 0},
                "notes": [
                    {
                        "scope": "source",
                        "column": 4,
                        "header": "note_ru",
                        "row_offset": 0,
                    },
                    {
                        "scope": "target",
                        "language": "en",
                        "column": 5,
                        "header": "note_en",
                        "row_offset": 0,
                    },
                ],
            }
        )

    @staticmethod
    def _rows(term_count: int) -> list[list[str]]:
        rows = [["domain", "ru", "en", "note_ru", "note_en"]]
        rows.extend(
            [
                "Domain",
                f"Термин-{index}",
                f"Term-{index}",
                f"Note {index} ru",
                f"Note {index} en",
            ]
            for index in range(term_count)
        )
        return rows

    def test_full_terms_survive_the_preview_cap(self) -> None:
        """
        preview.terms stays bounded for the UI.

        The apply path reads preview.all_terms so a wide table loses
        nothing past the cap.
        """
        term_count = loc_kit.PREVIEW_TERM_LIMIT + 1
        preview = _validate(
            document=self._document(term_count), rows=self._rows(term_count)
        )

        self.assertEqual(len(preview.terms), loc_kit.PREVIEW_TERM_LIMIT)
        self.assertEqual(len(preview.all_terms), term_count)
        self.assertEqual(
            [term.values["ru"] for term in preview.all_terms],
            [f"Термин-{index}" for index in range(term_count)],
        )
        self.assertEqual(
            [term.values["en"] for term in preview.all_terms],
            [f"Term-{index}" for index in range(term_count)],
        )
