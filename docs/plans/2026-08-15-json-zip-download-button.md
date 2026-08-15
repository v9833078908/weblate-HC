# Пункт «JSON в ZIP» в меню «Файлы» компонента

Дата: 2026-08-15. Статус: реализован и проверен.

## Контекст

Канонический формат заказчика — «JSON с раздельными файлами по языкам». Бэкенд
уже умеет собирать такой ZIP (`?format=zip:json` на download-URL компонента,
проверено на живом стенде 2026-08-15: `pirate-ships/integration-smoke` отдал
ZIP с `…-ru.json`, `…-en.json`, `…-es.json`, `…-fr.json`, `…-de.json`), но
меню «Файлы» компонента пункта не содержит — там захардкожены только
`zip:csv`, `zip:xliff11`, `zip:xlsx`.

Широкий CSV «key-ru-en-…, все языки на одном листе» как внутренний экспортёр
решено **не делать**: реестр экспортёров работает per-translation
(`BaseExporter(translation=...)`, `download_multi` сериализует каждый язык
отдельно), широкий формат требует pivot'а по ключу через все переводы и не
round-trip'ится через встроенный CSV-импортёр (один `target` на строку).
Потребность в широкой таблице закрывается внешним скриптом поверх `zip:json`,
если возникнет.

## Изменения

### 1. `weblate/templates/component.html`

Новый пункт между XLSX (строка 90 на момент плана) и блоком
`{% if user_can_upload_translation %}`. Показ только для монолингвальных
компонентов: у билингвальных JSON-экспортёр не поддерживается
(`MonolingualExporter.supports` требует `has_template()`,
`weblate/formats/exporters.py:416-418`) и ZIP вышел бы из одних
`.skipped`-заглушек.

```html
{% if object.has_template %}
  <li>
    <a class="dropdown-item"
       href="{% url 'download' path=object.get_url_path %}?format=zip:json"
       title="{% translate "Download for offline translation." %}">{% blocktranslate %}Download translations as JSON in a ZIP file{% endblocktranslate %}</a>
  </li>
{% endif %}
```

`Component.has_template()` — `weblate/trans/models/component.py:6027`.

### 2. `weblate/trans/tests/test_files.py`

Новый класс рядом с `DownloadMultiTest` (после
`test_component_skips_symlinked_template`, ~строка 1521). В шапку файла
добавить `import json` (остальное уже импортировано).

```python
class DownloadMonoComponentTest(ViewTestCase):
    def create_component(self):
        return self.create_json_mono()

    def test_component_json_zip(self) -> None:
        response = self.client.get(
            reverse("download", kwargs=self.kw_component), {"format": "zip:json"}
        )
        content = self.assert_zip(response, "test-test-cs.json")
        payload = json.loads(content)
        self.assertIn("hello", payload)

    def test_bilingual_json_zip_is_all_skipped(self) -> None:
        component = self.create_po()
        response = self.client.get(
            reverse("download", kwargs=self.kw_component), {"format": "zip:json"}
        )
        with ZipFile(BytesIO(response.content), "r") as zipfile:
            self.assertTrue(zipfile.namelist())
            self.assertTrue(all(n.endswith(".skipped") for n in zipfile.namelist()))
```

Опоры:

- фикстура `create_json_mono` — `weblate/trans/tests/utils.py:362`
  (компонент `test/test`, шаблон `json-mono/en.json`, перевод `cs`);
- `assert_zip` — `weblate/trans/tests/test_views.py:352` (проверяет 200,
  `application/zip`, целостность, возвращает содержимое файла);
- имена файлов в ZIP: `<project>-<component>-<lang>.json`
  (`exporter.get_filename()`), отсюда `test-test-cs.json`;
- `hello` — ключ из тестового шаблона `json-mono/en.json`.

### 3. `docs/changes.rst`

Одна строка в невыпущенной секции (Improvements):

```rst
* The component :guilabel:`Files` menu can now download all translations as monolingual JSON files in a ZIP archive, for components with a template.
```

## Проверка

1. `./rundev.sh test weblate/trans/tests/test_files.py -k "DownloadMono or all_skipped"` — оба теста зелёные.
2. Весь файл: `./rundev.sh test weblate/trans/tests/test_files.py` — без регрессий.
3. Браузерный smoke на дев-стенде (localhost:3001, admin/admin): компонент
   `pirate-ships/localization` → меню «Файлы» → новый пункт → скачивается ZIP
   с файлами по языкам; на билингвальном компоненте пункта нет.
4. `uv run prek run --all-files` — djlint/шаблонные проверки.

## Вне скоупа

- Те же пункты в project/category/language-project/workspace-меню.
- Широкий CSV-экспортёр (решено: не делаем, см. «Контекст»).
- Русский перевод новой строки (прецедент форка: кастомные строки в
  `weblate/locale/` не добавляются; до перевода UI покажет английский текст).

## Коммит

```
feat(trans): offer JSON download in component Files menu
```
