# Query OpenRouter account usage with the key stored in the prod MT setting.
# Read-only. Pipe into weblate shell on prod. Never prints the key.
from __future__ import annotations

import json
import urllib.request

from weblate.configuration.models import Setting

row = Setting.objects.get(category=2, name="openrouter")
value = row.value
config = value if isinstance(value, dict) else json.loads(value)
key = config["key"]
print("configured routing models:", sorted(set(config.get("routing", {}).values())))

request = urllib.request.Request(
    "https://openrouter.ai/api/v1/auth/key",
    headers={"Authorization": f"Bearer {key}"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    data = json.load(response)["data"]

for field in ("label", "usage", "usage_daily", "usage_weekly", "usage_monthly", "limit", "is_free_tier", "rate_limit"):
    if field in data:
        print(f"{field}: {data[field]}")
