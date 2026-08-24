# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Probe: verified semantics of evaluate_glossary_terms for ru -> zh_Hans.

Run 2026-08-16 inside the dev container:

    docker exec -i dev-docker-weblate-1 weblate shell < analysis/probes/glossary-or-probe.py

Only the two glossary lookups are replaced with fakes; everything else is
product code. No DB access, no writes.

Verified conclusions (scenario numbers match the output):

- A second plain glossary row with the same source clears the advisory in
  either row order (2, 3, 4): ``advisory.discard`` makes matching order-free.
- ``read-only`` mode sets the expected string to the RUSSIAN source term
  (weblate/checks/glossary.py:58), so it hard-fails even when the target
  contains the wanted short form (5, 6). Never use it for target variants.
- For a zh target ``count_inflected`` returns 0 (zh is not in
  MORPHOLOGY_LANGUAGES), so a plain-term miss is advisory-only (1, 8); hard
  is reachable only via ``exact``/``read-only``/``forbidden`` (9, 10).
- Accepting a short form (级) stops flagging compounds containing it
  (级别): a deliberate recall trade-off (7 vs 8).
"""

import weblate.glossary.models as gm
from weblate.checks.glossary import evaluate_glossary_terms


class FakeLang:
    def __init__(self, code, base, whitespace):
        self.code = code
        self.base_code = base
        self._ws = whitespace

    def uses_whitespace(self):
        return self._ws


class FakeTerm:
    def __init__(self, source, target, modes=()):
        self.source = source
        self.target = target
        self.modes = frozenset(modes)


class FakeComponent:
    def __init__(self, source_language):
        self.source_language = source_language


class FakeTranslation:
    def __init__(self, language, component):
        self.language = language
        self.component = component


class FakeUnit:
    def __init__(self, translation):
        self.translation = translation


RU = FakeLang("ru", "ru", True)
ZH = FakeLang("zh_Hans", "zh", False)
UNIT = FakeUnit(FakeTranslation(ZH, FakeComponent(RU)))

real_terms = gm.get_glossary_terms
real_modes = gm.get_glossary_term_modes


def run(name, terms, source, target):
    gm.get_glossary_terms = lambda unit, include_variants=True: list(terms)
    gm.get_glossary_term_modes = lambda term: term.modes
    try:
        hard, advisory = evaluate_glossary_terms(UNIT, source, target)
    finally:
        gm.get_glossary_terms = real_terms
        gm.get_glossary_term_modes = real_modes
    print(f"{name}\n    hard={sorted(hard)} advisory={sorted(advisory)}")


LONG = FakeTerm("провинция", "省份")
SHORT = FakeTerm("провинция", "省")
SHORT_RO = FakeTerm("провинция", "省", modes=("read-only",))
LVL_LONG = FakeTerm("уровень", "等级")
LVL_SHORT = FakeTerm("уровень", "级")
CONVOY_EXACT = FakeTerm("конвой", "商队", modes=("exact",))

SRC_PROV = "Захвачена провинция"
SRC_LVL = "Уровень отряда"
SRC_CONVOY = "Конвой прибыл"

print("== 1. только длинная строка, в цели короткая форма ==")
run("[1]", [LONG], SRC_PROV, "占领省")

print("== 2. две обычные строки, длинная первой ==")
run("[2]", [LONG, SHORT], SRC_PROV, "占领省")

print("== 3. две обычные строки, короткая первой ==")
run("[3]", [SHORT, LONG], SRC_PROV, "占领省")

print("== 4. две строки, в цели длинная форма ==")
run("[4]", [LONG, SHORT], SRC_PROV, "占领省份")

print("== 5. короткая строка помечена read-only ==")
run("[5]", [LONG, SHORT_RO], SRC_PROV, "占领省")

print("== 6. read-only в одиночку, цель содержит короткую форму ==")
run("[6]", [SHORT_RO], SRC_PROV, "占领省")

print("== 7. уровень: принимаем 级, цель 级别 ==")
run("[7]", [LVL_LONG, LVL_SHORT], SRC_LVL, "小队级别")

print("== 8. уровень: только 等级, цель 级别 ==")
run("[8]", [LVL_LONG], SRC_LVL, "小队级别")

print("== 9. конвой exact, цель 车队 ==")
run("[9]", [CONVOY_EXACT], SRC_CONVOY, "车队抵达")

print("== 10. конвой exact, цель 商队 ==")
run("[10]", [CONVOY_EXACT], SRC_CONVOY, "商队抵达")
