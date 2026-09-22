# Прототипы экрана «Повторы»

Три статичных HTML-варианта экрана очереди повторов (задача 3 плана
`docs/product/plans/2026-09-17-repeat-drift-reconciliation-and-managed-reuse.md`).
Контракт выбранного экрана: `docs/product/plans/2026-09-21-repeat-queue-ui-variant.md`.

| Файл | Вариант | Статус |
| --- | --- | --- |
| `queue-variant-c.html` | C: список групп, раскрыта одна, одно решение и одна кнопка | **выбран**, источник контракта |
| `queue-variant-b.html` | B: «один вопрос за раз», карточка на группу | отклонён по ревью `docs/product/reviews/2026-09-22-repeat-queue-ui-variant-review.md` |
| `queue-variant-a.html` | A: плотная таблица с раскрытой группой | базовый, для сравнения |

Прототипы подключают настоящие стили репозитория относительными путями
(`../../../weblate/static/...`), поэтому сервер запускается из корня
репозитория:

```sh
ln -sfn "$(uv run python -c 'import weblate_fonts, os; print(os.path.join(os.path.dirname(weblate_fonts.__file__), "static", "weblate_fonts"))')" analysis/prototypes/repeat-queue/weblate_fonts
python3 -m http.server 8777 --bind 127.0.0.1
open http://127.0.0.1:8777/analysis/prototypes/repeat-queue/queue-variant-c.html
```

Первая команда создаёт локальную (gitignored) ссылку на шрифты из пакета
`weblate_fonts`; без неё страница открывается с системным шрифтом.

Состояния переключает панель внизу страницы или параметр
`?state=queue|preview|report|paid|editor|rule|states`; `?bare=1` прячет панель.
Тёмная тема подключена через `prefers-color-scheme`, а не через `data-bs-theme`,
как в `weblate/templates/base.html`, поэтому её контраст здесь не показателен.
