# Copyright © 2026 Weblate contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import csv
from io import BytesIO, StringIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import load_workbook

from weblate.trans.tests.test_views import ViewTestCase


class MultilingualSpreadsheetExportTest(ViewTestCase):
    def create_component(self):
        return self.create_json()

    def test_csv_and_xlsx_export_one_component_table(self) -> None:
        from weblate.trans.multilingual_spreadsheet import (
            export_component,
            parse_upload,
        )

        unit = self.translation.unit_set.order_by("pk").first()
        assert unit is not None
        unit.translate(self.user, '=SUM(1,2), "Привет"\nnext', unit.state)

        expected_headers = (
            "key",
            *self.component.translation_set.order_by("language__code").values_list(
                "language__code", flat=True
            ),
        )
        csv_content = export_component(self.component, "csv")
        xlsx_content = export_component(self.component, "xlsx")

        self.assertEqual(
            parse_upload(
                self.component,
                SimpleUploadedFile("translations.csv", csv_content, content_type="text/csv"),
            ).headers,
            expected_headers,
        )
        self.assertEqual(
            parse_upload(
                self.component,
                SimpleUploadedFile(
                    "translations.xlsx",
                    xlsx_content,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ).headers,
            expected_headers,
        )

        workbook = load_workbook(BytesIO(xlsx_content), data_only=False)
        self.assertEqual(len(workbook.worksheets), 1)
        self.assertEqual(workbook.active.cell(row=2, column=len(expected_headers)).data_type, "s")

    def test_preview_rejects_reordered_placeholders(self) -> None:
        from weblate.trans.multilingual_spreadsheet import (
            build_preview,
            export_component,
            parse_upload,
        )

        unit = self.translation.unit_set.order_by("pk").first()
        assert unit is not None
        source_unit = unit.source_unit
        source_unit.source = "{0} {playerName}"
        source_unit.target = source_unit.source
        source_unit.save()
        unit.source = source_unit.source
        unit.save(update_fields=["source"])

        parsed = parse_upload(
            self.component,
            SimpleUploadedFile("translations.csv", export_component(self.component, "csv")),
        )
        values = list(parsed.rows[0].values)
        values[parsed.headers.index(self.translation.language.code)] = "{playerName} {0}"
        parsed = parsed.__class__(
            parsed.headers,
            (parsed.rows[0].__class__(parsed.rows[0].row_number, tuple(values)), *parsed.rows[1:]),
        )

        with self.assertRaises(ValidationError):
            build_preview(self.component, parsed)


class MultilingualSpreadsheetValidationTest(ViewTestCase):
    def create_component(self):
        return self.create_json()

    def _csv_rows(self) -> list[list[str]]:
        from weblate.trans.multilingual_spreadsheet import export_component

        return list(
            csv.reader(
                export_component(self.component, "csv").decode("utf-8").splitlines(),
                dialect="unix",
            )
        )

    def _parse_csv_rows(self, rows: list[list[str]]) -> None:
        from weblate.trans.multilingual_spreadsheet import parse_upload


        content = StringIO(newline="")
        csv.writer(content, dialect="unix").writerows(rows)
        parse_upload(
            self.component,
            SimpleUploadedFile("translations.csv", content.getvalue().encode("utf-8")),
        )

    def test_rejects_changed_component_schema_and_identity(self) -> None:
        rows = self._csv_rows()
        cases = (
            ("duplicate header", [rows[0][0], rows[0][0], *rows[0][2:]], rows[1:]),
            ("missing language", rows[0][:-1], [row[:-1] for row in rows[1:]]),
            ("unknown language", [*rows[0][:-1], "unknown"], rows[1:]),
            ("unknown key", rows[0], [["unknown", *rows[1][1:]], *rows[2:]]),
            ("duplicate key", rows[0], [rows[1], rows[1], *rows[2:]]),
        )
        for name, headers, body in cases:
            with self.subTest(name), self.assertRaises(ValidationError):
                self._parse_csv_rows([headers, *body])

    def test_rejects_xlsx_with_hidden_extra_worksheet(self) -> None:
        from weblate.trans.multilingual_spreadsheet import export_component, parse_upload

        workbook = load_workbook(BytesIO(export_component(self.component, "xlsx")))
        worksheet = workbook.create_sheet("hidden")
        worksheet.sheet_state = "hidden"
        output = BytesIO()
        workbook.save(output)

        with self.assertRaises(ValidationError):
            parse_upload(
                self.component,
                SimpleUploadedFile("translations.xlsx", output.getvalue()),
            )
