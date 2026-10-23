# Producer Console design notes

This production slice keeps the frozen `frontend-wizard/` prototype as a
visual reference only. The top bar, spacious project cards, direct creation
dialog, and empty-project hierarchy carry over; prototype fixtures, invented
statistics, upload actions, and client-built Weblate URLs do not.

All state comes from `ProducerApi`. Navigation after creation waits for both
the create response and a fresh project-detail response. The dialog traps and
returns keyboard focus, async states are announced, and narrow layouts use
logical CSS properties without horizontal page overflow.

The first-localization wizard is a production route, not a prototype route.
Quick (four stages) and detailed (eight steps) are two projections of the same
owner-bound `LocKitImportDraft`. The server's opaque revision, analysis,
answers, gate errors and confirmation totals are authoritative. Refresh and
deep links restore the draft from `?u=`, while mode and step changes PATCH only
navigation fields and never re-read the workbook or call a model.

Every worksheet remains visible until the producer decides. `(Old)` sheets are
archive recommendations, not automatic exclusions. Formula helper columns are
shown and persisted as ignored only with an explicit selected-sheet answer.
`Store` is visible but disabled because its keyless ingestion belongs to Wave
1C. Ambiguous columns, source language and target languages require explicit
roles before the server gate enables localization.

The Context step reads the project profile without invoking a model; its Save
button is the explicit action that may generate translator instructions using
the server-configured profile endpoint. An optional prepared glossary has its
own draft and is bound to the localization request, so the backend publishes it
before machine translation. Advanced links are always supplied by the API.

Run pages poll only while the run is active or reports `no-update`, at a fixed
five-second interval. Persisted stages remain the source of truth. Resume uses
the attempt number stored in the run and appears only when the API says the
unfinished work is safe to resume.

The download screen selects only permission-visible loc-kit components and
shows totals from the exact export revision. XLSX, CSV and JSON are rebuilt
from durable component provenance rather than the temporary upload draft.
Source is the first language column, original key and metadata order survives,
and identical keys in separate components remain separate. A stale download
revision refreshes the summary instead of silently exporting changed content.
