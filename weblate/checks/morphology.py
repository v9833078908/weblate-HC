# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Snowball-based morphological comparison for glossary enforcement."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from threading import Lock
from typing import TYPE_CHECKING

import snowballstemmer

if TYPE_CHECKING:
    from collections.abc import Iterator

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Closed language allowlist: a language joins only after a corpus measurement,
# see docs/llm-first/measurements/2026-08-11-glossary-enforcement-analysis.md. Indonesian is
# deliberately absent (Snowball conflates distinct words there).
MORPHOLOGY_LANGUAGES: dict[str, str] = {
    "ru": "russian",
    "de": "german",
    "tr": "turkish",
    "fr": "french",
    "es": "spanish",
    "pt": "portuguese",
    "hi": "hindi",
    "fa": "persian",
}

# Source-side stem matching is enabled only for Russian: measured +140%
# recovered lines on COL4. English is excluded because its false matches
# concentrate on homographs, see spec item 7.
SOURCE_STEM_LANGUAGES: frozenset[str] = frozenset({"ru"})

_STEMMERS: dict[str, tuple[str, snowballstemmer.SnowballProgram]] = {}
_STEMMERS_LOCK = Lock()


def get_snowball_version() -> str:
    try:
        return version("snowballstemmer")
    except PackageNotFoundError:
        return "unknown"


def get_algorithm(language_code: str) -> str | None:
    """Return Snowball algorithm for the base language code, if allowlisted."""
    base = language_code.replace("_", "-").split("-", 1)[0]
    return MORPHOLOGY_LANGUAGES.get(base.lower())


def get_stemmer(
    language_code: str,
) -> tuple[str, snowballstemmer.SnowballProgram] | None:
    """Return cached (algorithm, stemmer) pair for the language code."""
    algorithm = get_algorithm(language_code)
    if algorithm is None:
        return None
    with _STEMMERS_LOCK:
        stemmer = _STEMMERS.get(algorithm)
        if stemmer is None:
            stemmer = (algorithm, snowballstemmer.stemmer(algorithm))
            _STEMMERS[algorithm] = stemmer
        return stemmer


@lru_cache(maxsize=65536)
def stem_word(algorithm: str, word: str) -> str:
    return _STEMMERS[algorithm][1].stemWord(word)


def get_text_stems(text: str, language_code: str) -> list[str]:
    """Stem every word of the text; empty when the language is not allowlisted."""
    stemmer = get_stemmer(language_code)
    if stemmer is None:
        return []
    algorithm = stemmer[0]
    return [stem_word(algorithm, word.lower()) for word in WORD_RE.findall(text)]


def iter_word_spans(text: str) -> Iterator[tuple[str, int, int]]:
    for match in WORD_RE.finditer(text):
        yield match.group(), match.start(), match.end()


def is_acronym(term: str) -> bool:
    """
    Return whether the term is a short all-caps abbreviation.

    An abbreviation does not inflect the way a word does, and stemming one is
    actively harmful: Russian Snowball turns ``НИИ`` into ``ни``, which is a
    very common particle, so the term would match unrelated text. The rule
    matches the one the measurements in
    ``docs/llm-first/measurements/2026-08-11-glossary-enforcement-analysis.md`` were taken with -
    the probes there compared acronyms case-sensitively for the same reason.
    """
    return term.isupper() and len(term) <= 5


def contains_inflected(term: str, text: str, language_code: str) -> bool:
    """
    Return whether the term occurs in the text as a whole-word sequence of stems.

    Multi-word terms require every word to match its corresponding stem in
    order; a stem can only match a whole word, never a substring.
    """
    return count_inflected(term, text, language_code) > 0


def count_inflected(term: str, text: str, language_code: str) -> int:
    """
    Count occurrences of the term's stem sequence as whole-word runs.

    An acronym never counts as inflected: see :func:`is_acronym`.
    """
    if is_acronym(term):
        return 0
    needle = get_text_stems(term, language_code)
    if not needle:
        return 0
    haystack = get_text_stems(text, language_code)
    count = 0
    limit = len(haystack) - len(needle)
    for i in range(limit + 1):
        if haystack[i : i + len(needle)] == needle:
            count += 1
    return count
