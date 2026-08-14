# Estimate the OpenRouter cost of the col4/data/id run from real text sizes.
# Read-only. Pipe into weblate shell on prod.
#
# The LLM runs in batches of `batch_size` units. Each request carries:
#   system prompt + (glossary <=300 terms, since col4 has 95) + previous batch
#   (in-context example) + the current batch of sources.
# We measure the real byte sizes and price them at gemini-2.5-flash rates.
from __future__ import annotations

from weblate.glossary.models import get_glossary_tuples
from weblate.trans.models import Translation
from weblate.utils.state import STATE_TRANSLATED

translation = Translation.objects.get(
    component__project__slug="col4", component__slug="data", language__code="id"
)
units = list(
    translation.unit_set.filter(state__gte=STATE_TRANSLATED).order_by("position")
)
n = len(units)

# gemini-2.5-flash pricing (OpenRouter, USD per 1M tokens)
PRICE_IN = 0.30
PRICE_OUT = 2.50
# rough tokens-per-byte for mixed Cyrillic source / Latin target JSON
BYTES_PER_TOKEN_RU = 3.0   # Cyrillic ~2-3 bytes/token in UTF-8 cl100k
BYTES_PER_TOKEN_EN = 4.0   # prompt scaffolding + id output

BATCH = 10  # batch_size default
import math

batches = math.ceil(n / BATCH)

# source text volume
src_bytes = sum(len(u.source.encode("utf-8")) for u in units)
tgt_bytes = sum(len(u.get_target_plurals()[0].encode("utf-8")) for u in units)
avg_src = src_bytes / n

# glossary volume (95 terms ru->id) sent with every batch
gloss_tuples = list(get_glossary_tuples(units[:0]))  # noqa - need real units
# get_glossary_tuples needs units with glossary_terms; approximate via pairs
from weblate.trans.models import Unit
gloss_pairs = Unit.objects.filter(
    translation__component__project__slug="col4",
    translation__component__is_glossary=True,
    translation__language__code="ru",
).count()
# estimate glossary text bytes from the recon dump (95 terms, avg ~30 chars pair)
gloss_bytes = gloss_pairs * 60  # ru source + id target + JSON framing
gloss_tokens = gloss_bytes / BYTES_PER_TOKEN_RU

# system prompt: fixed instruction block, measure from llm.py length ~ constant
PROMPT_BYTES = 6000  # instruction + few-shot scaffolding
prompt_tokens = PROMPT_BYTES / BYTES_PER_TOKEN_EN

# per-batch input = prompt + glossary + one previous-batch example + current batch
prev_example_tokens = (avg_src * BATCH) / BYTES_PER_TOKEN_RU * 1.5  # src+tgt echo
batch_input_tokens = prompt_tokens + gloss_tokens + prev_example_tokens + (avg_src * BATCH) / BYTES_PER_TOKEN_RU
total_in_tokens = batch_input_tokens * batches
total_out_tokens = (tgt_bytes / BYTES_PER_TOKEN_EN)

print(f"units={n} batches={batches} (batch_size={BATCH})")
print(f"source bytes={src_bytes} target bytes={tgt_bytes}")
print(f"glossary terms={gloss_pairs} glossary bytes~{gloss_bytes}")
print(f"per-batch input tokens~{batch_input_tokens:.0f}")
print(f"total input tokens~{total_in_tokens:.0f}")
print(f"total output tokens~{total_out_tokens:.0f}")
cost_in = total_in_tokens / 1e6 * PRICE_IN
cost_out = total_out_tokens / 1e6 * PRICE_OUT
print(f"COST input  = {total_in_tokens/1e6:.3f}M tok * ${PRICE_IN} = ${cost_in:.4f}")
print(f"COST output = {total_out_tokens/1e6:.3f}M tok * ${PRICE_OUT} = ${cost_out:.4f}")
print(f"COST total  = ${cost_in + cost_out:.4f}")
