# Учётная запись Weblate и делегированный доступ Wizard

Реализация плана `docs/product/plans/2026-10-26-weblate-wizard-single-sign-on.md`.
Стандартный OIDC Authorization Code + S256 PKCE; пароль обрабатывает только
штатный вход Weblate. Внешняя личность — issuer + случайный неизменяемый subject.
Проверенные библиотечные зависимости: Django OAuth Toolkit 3.4.1 (Django 6),
Authlib 1.9.1 (httpx/FastAPI), Cryptography 50.0.2. Это не обновление Weblate целиком.

## Матрица маршрутов

Источник вызовов: `backend/app/integrations/weblate.py`, `routes/project_links.py`,
`routes/runs.py`, `services/languages.py` и `worker.py` репозитория Wizard.
Замкнутый исполняемый перечень — `weblate/wizard_auth/permissions.py:RULES`.
Scopes являются дополнительным ограничением, а не заменой прав Weblate.

| Методы и семейство | Scope | Дополнительная граница |
| --- | --- | --- |
| GET projects/list/detail/components/translations | wizard.read | Native allowed_projects/filter_access |
| GET components/translations/units/files | wizard.read | Native component/unit permission filtering |
| GET languages | wizard.read | Только каталог языков |
| GET tasks/UUID | wizard.read | Native get_task проверяет component/user metadata |
| GET producer/me, projects, runs, receipts, quality results, repeat plans | wizard.read | Native actor/project binding |
| POST projects, components, translations, source units, upload | wizard.import | Native add/upload permissions; доступ к проекту не разрешает создание |
| PATCH units | wizard.import | Только target/explanation плюс native permissions |
| GET/PATCH producer translation-profile | read/import | PATCH требует project.edit; только persona/style/language_instructions |
| POST producer MT batch/reconcile | wizard.translate | Native translation.auto, actor, revision, durable paid reservations |
| POST producer judge/recheck/apply/repeat/resume/cancel | wizard.quality | Native unit.review/translate и неизменяемый execution mode |
| Остальные методы/маршруты | Запрещены | Включая machinery_settings, users/admin и legacy autotranslate |

В SSO-режиме Wizard использует durable MT batches, не legacy autotranslate.
Всем WeblateClient необходима пользовательская делегация, общий токен не fallback.
Существующий независимый REST Token/Bearer/Session доступ не меняется.
Профиль инструкций больше не требует выгрузки ключей и routing из Weblate.

## Сессии и фоновая работа

Code 60 секунд, login transaction 10 минут, access token 5 минут, Wizard session
8 часов; refresh family имеет абсолютный предел 24 часа и ротацию с защитой от
reuse. Шифрованные токены остаются в серверной БД Wizard, ключ — отдельно.
Logout сначала блокирует локальную делегацию и ставит durable намерение remote
revocation; при недоступном Weblate worker повторяет только отзыв, без MT.
Принятый моделью ответ не отбрасывается из-за последующего выхода пользователя.

Новые задания привязаны к grant и личности. Остановка доступа сохраняет ключи и
checkpoints; продолжение требует явного действия после входа той же личности.
Старые задания служебного актора не получают новую делегацию автоматически.
Локальные аккаунты сопоставляются оператором с dry-run; совпадение имени/email
не объединяет пользователей. Исходные владельцы записей и акторы квитанций неизменны.
