# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("WIZARD_DATA_DIR", "data")))
    static_dir: Path = field(default_factory=lambda: Path(__file__).parents[2] / "console/dist")
    public_origin: str = field(
        default_factory=lambda: os.getenv("WIZARD_PUBLIC_ORIGIN", "http://127.0.0.1:18511")
    )
    secure_cookie: bool = field(
        default_factory=lambda: os.getenv("WIZARD_SECURE_COOKIE", "0") == "1"
    )
    bdhc_url: str = field(
        default_factory=lambda: os.getenv("BDHC_API_BASE_URL", "http://192.168.0.180:19101")
    )
    bdhc_token_file: str = field(default_factory=lambda: os.getenv("BDHC_API_TOKEN_FILE", ""))
    weblate_url: str = field(
        default_factory=lambda: os.getenv("WEBLATE_API_BASE_URL", "https://l10n.herocraft.com/api/")
    )
    weblate_token_file: str = field(default_factory=lambda: os.getenv("WEBLATE_API_TOKEN_FILE", ""))
    engine: str = field(default_factory=lambda: os.getenv("WEBLATE_MT_ENGINE", "openrouter"))
    external_writes_enabled: bool = field(
        default_factory=lambda: os.getenv("WIZARD_EXTERNAL_WRITES", "0") == "1"
    )
    quality_cycle_enabled: bool = field(
        default_factory=lambda: os.getenv("WIZARD_QUALITY_CYCLE_ENABLED", "0") == "1"
    )
    full_quality_cycle_enabled: bool = field(
        default_factory=lambda: os.getenv("WIZARD_FULL_QUALITY_CYCLE_ENABLED", "0") == "1"
    )
    mt_observation_seconds: int = field(
        default_factory=lambda: int(os.getenv("WIZARD_MT_OBSERVATION_SECONDS", "1800"))
    )
    mt_max_observation_seconds: int = field(
        default_factory=lambda: int(os.getenv("WIZARD_MT_MAX_OBSERVATION_SECONDS", "21600"))
    )
    producer_post_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("WIZARD_PRODUCER_POST_INTERVAL_SECONDS", "75"))
    )
    mt_async_batches_enabled: bool = field(
        default_factory=lambda: os.getenv("WIZARD_MT_ASYNC_BATCHES", "0") == "1"
    )
    mt_async_projects: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            p.strip() for p in os.getenv("WIZARD_MT_ASYNC_PROJECTS", "").split(",") if p.strip()
        )
    )
    weblate_connect_timeout: float = field(
        default_factory=lambda: float(os.getenv("WEBLATE_CONNECT_TIMEOUT", "10"))
    )
    weblate_write_read_timeout: float = field(
        default_factory=lambda: float(os.getenv("WEBLATE_WRITE_READ_TIMEOUT", "600"))
    )
    weblate_get_read_timeout: float = field(
        default_factory=lambda: float(os.getenv("WEBLATE_GET_READ_TIMEOUT", "60"))
    )
    weblate_write_timeout: float = field(
        default_factory=lambda: float(os.getenv("WEBLATE_WRITE_TIMEOUT", "60"))
    )
    weblate_pool_timeout: float = field(
        default_factory=lambda: float(os.getenv("WEBLATE_POOL_TIMEOUT", "10"))
    )
    llm_url: str = field(
        default_factory=lambda: os.getenv("PROFILE_LLM_BASE_URL", "https://openrouter.ai/api/v1")
    )
    llm_token_file: str = field(default_factory=lambda: os.getenv("PROFILE_LLM_TOKEN_FILE", ""))
    llm_model: str = field(default_factory=lambda: os.getenv("PROFILE_LLM_MODEL", ""))
    llm_app_title: str = field(
        default_factory=lambda: os.getenv(
            "PROFILE_LLM_APP_TITLE", "HCGameLoc Wizard - Translation Profile Agent"
        )
    )
    llm_http_referer: str = field(default_factory=lambda: os.getenv("PROFILE_LLM_HTTP_REFERER", ""))
    profile_agent_enabled: bool = field(
        default_factory=lambda: os.getenv("WIZARD_PROFILE_AGENT_ENABLED", "0") == "1"
    )
    llm_reasoning_effort: str = field(
        default_factory=lambda: os.getenv("PROFILE_LLM_REASONING_EFFORT", "low")
    )
    llm_max_completion_tokens: int = field(
        default_factory=lambda: int(os.getenv("PROFILE_LLM_MAX_COMPLETION_TOKENS", "16384"))
    )
    llm_model_version: str = field(
        default_factory=lambda: os.getenv("PROFILE_LLM_MODEL_VERSION", "openai/gpt-6-luna-20260922")
    )

    def __post_init__(self):
        if (
            not math.isfinite(self.producer_post_interval_seconds)
            or not 1 <= self.producer_post_interval_seconds <= 86400
        ):
            raise ValueError("Invalid producer POST interval")
        for name, maximum in (
            ("weblate_connect_timeout", 120),
            ("weblate_write_read_timeout", 1800),
            ("weblate_get_read_timeout", 300),
            ("weblate_write_timeout", 300),
            ("weblate_pool_timeout", 120),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not 1 <= value <= maximum:
                raise ValueError(f"Invalid bounded timeout: {name}")
        if not 60 <= self.mt_observation_seconds <= self.mt_max_observation_seconds <= 86400:
            raise ValueError("MT observation requires 60 <= idle <= maximum <= 86400 seconds")


def read_secret_file(path: str) -> str:
    if not path:
        raise ValueError("Integration secret file is not configured")
    value = Path(path).read_text(encoding="utf-8-sig").strip()
    if not value or "\n" in value:
        raise ValueError("Integration secret file is invalid")
    return value
