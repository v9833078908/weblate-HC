# game-number: nine-language live replay against heart-abyss/hub-1

Date: 2026-08-25. Instance: `l10n.herocraft.com`. Component: `heart-abyss/hub-1`,
source language `ru`, 396 units, nine target languages
(`de`, `en`, `es`, `fr`, `it`, `ja`, `ko`, `zh_Hans`, `zh_Hant`). Read-only: fetched via the
`/api/translations/<lang>/units/` endpoint with `PROD_WEBLATE_API_TOKEN`
(`deploy/.env.local`, labelled "prod, read-only analysis"); nothing was written to Weblate.

**Purpose:** `docs/product/plans/2026-08-24-game-number-value-comparison.md` §"Coverage
caveat" notes that no local corpus holds a parsed CJK scale run, so the invariant (the new
rule never fires where the old rule was silent, outside a CJK run) is untested for `ja`,
`zh` and `ko` by the corpus gate alone. This replay closes that gap against the one
production component that actually carries CJK targets.

## Method

The live data was fetched once (3564 target strings across nine languages) and saved to
`analysis/data/heart-abyss-hub-1-units-9lang.tsv`. Two implementations of
`GameNumberCheck.check_single` were then run against that one snapshot, so both "runs" see
identical text and only the code differs:

- **HEAD~** (`d71af44`, the state before this branch, matching what shipped in production on
  2026-08-24): the `_numbers`/`_fold_scales` digit-and-symmetric-fold comparison.
- **branch** (`26b699e`, this plan's implementation): `game_number_fails`, the two-reading,
  open/closed comparison.

## Results

| Language | HEAD~ firings | branch firings |
| --- | ---: | ---: |
| de | 0 | 0 |
| en | 0 | 0 |
| es | 0 | 0 |
| fr | 0 | 0 |
| it | 0 | 0 |
| ja | 0 | 0 |
| ko | 0 | 0 |
| zh_Hans | 0 | 0 |
| zh_Hant | 0 | 0 |

Invariant violations (branch fires, HEAD~ is silent, target holds no parsed CJK run):
**0 / 3564** pairs.

Bounty keys (`hub1_guard_1_3`, `hub1_guard_1_4`, repaired 2026-08-24): silent under **both**
implementations, on every target language. Neither regressed and neither is masking the old
defect.

Targets holding at least one parsed CJK scale run: **12** (all `ja` and `ko`, none `zh_Hans`
or `zh_Hant`), for example `hub1_guard_1_3` (`ja` `1万文！`, `ko` `10천 몬!`) and
`hub1_first_1_18` (`ja` `私の「千の雑貨」にも遊びに来てください！`, a shop-name idiom where
the bare section scale `千` parses as 1000 per Rule 2's documented figurative-reading
tradeoff). None of the 12 produced an invariant violation or a bounty-key regression.

## Reading these zeros

The component is 100% translated and was fully repaired on 2026-08-24
(`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md`), so zero firings
under both implementations is the expected outcome for a clean component, not a null result:
it confirms no regression on real translations, including the 12 real CJK-run targets this
plan's design specifically had to get right. It does not exercise the abbreviation
false-positive rows (`10 Tsd.`, `5 mln`, `10k`, ...) fixed by the value-comparison rewrite -
those are covered by the frozen matrix and behavior-table tests
(`weblate_customization/tests/test_checks.py::GameNumberCheckTest`), not by this component's
current (correct) production text.

## Data

`analysis/data/heart-abyss-hub-1-units-9lang.tsv` - one row per `context`, one column per
language (`ru` plus the nine targets above), 396 rows.
