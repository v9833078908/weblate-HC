# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import quote, urljoin

from asgiref.sync import sync_to_async
from django.core.cache import cache

from weblate.logger import LOGGER
from weblate.utils.requests import JSON_RESPONSE_ERRORS

from .base import (
    MachineryRateLimitError,
    MachineTranslationError,
)
from .forms import AzureOpenAIMachineryForm, MistralMachineryForm, OpenAIMachineryForm
from .llm import BaseLLMTranslation, llm_batch_project


class BaseOpenAITranslation(BaseLLMTranslation):
    def get_runtime_base_url(self) -> str:
        raise NotImplementedError

    def get_chat_completions_url(self) -> str:
        raise NotImplementedError

    @staticmethod
    def join_api_url(base_url: str, path: str) -> str:
        return urljoin(f"{base_url.rstrip('/')}/", path)

    @staticmethod
    def _get_upstream_error(response) -> dict | None:
        """
        Return the failure a gateway reports inside a successful response.

        OpenRouter answers 200, puts the upstream refusal in the body and still
        sends a truncated fragment of a reply. Read at the failure seam, that
        fragment never reaches the parser, and the shared retry loop can back
        off instead of the caller splitting the batch into more refused
        requests.
        """
        try:
            payload = response.json()
        except JSON_RESPONSE_ERRORS:
            return None
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices") or []
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        error = first_choice.get("error") or payload.get("error")
        return error if isinstance(error, dict) else None

    @staticmethod
    def _get_upstream_error_status(error: dict) -> int | None:
        try:
            return int(error.get("code"))
        except (TypeError, ValueError):
            return None

    def check_failure(self, response) -> None:
        if response.status_code == 429:
            message = self.get_error_detail(response) or "Rate limit exceeded"
            raise MachineryRateLimitError(message)
        error = self._get_upstream_error(response)
        if error is not None:
            message = str(error.get("message") or "Upstream error")
            if self._get_upstream_error_status(error) in self.retry_statuses:
                raise MachineryRateLimitError(message)
            raise MachineTranslationError(message)
        super().check_failure(response)

    def should_retry(self, response, attempt: int) -> bool:
        if super().should_retry(response, attempt):
            return True
        if attempt >= self.retry_attempts:
            return False
        error = self._get_upstream_error(response)
        return (
            error is not None
            and self._get_upstream_error_status(error) in self.retry_statuses
        )

    def fetch_llm_translations(
        self, prompt: str, content: str, previous_content: str, previous_response: str
    ) -> str | None:
        model = self.get_traced_model()
        response = self.request(
            "post",
            self.get_chat_completions_url(),
            json=self.get_chat_payload(
                model, prompt, content, previous_content, previous_response
            ),
        )
        payload = response.json()
        self.record_llm_usage(payload, model)
        return self.parse_chat_response(payload)

    async def afetch_llm_translations(
        self, prompt: str, content: str, previous_content: str, previous_response: str
    ) -> str | None:
        model = await self.aget_traced_model()
        response = await self.arequest(
            "post",
            self.get_chat_completions_url(),
            json=self.get_chat_payload(
                model, prompt, content, previous_content, previous_response
            ),
        )
        payload = response.json()
        await sync_to_async(self.record_llm_usage, thread_sensitive=True)(
            payload, model
        )
        return self.parse_chat_response(payload)

    def record_llm_usage(self, payload: dict[str, Any], model: str) -> None:
        """
        Persist the token usage and cost OpenRouter billed for this request.

        Never raises: a broken accounting write must not break a translation,
        and the exception is logged so a broken table is visible in the log.
        """
        try:
            if not isinstance(payload, dict):
                return
            usage = payload.get("usage")
            if not isinstance(usage, dict):
                return
            prompt_tokens = usage.get("prompt_tokens") or 0
            completion_tokens = usage.get("completion_tokens") or 0
            total_tokens = usage.get("total_tokens") or (
                prompt_tokens + completion_tokens
            )
            if not prompt_tokens and not completion_tokens:
                return
            cost = usage.get("cost")
            prompt_details = usage.get("prompt_tokens_details") or {}
            completion_details = usage.get("completion_tokens_details") or {}
            project = self.settings.get("_project")
            if project is not None:
                project_slug = project.slug
            else:
                project_slug = llm_batch_project.get()
            # ruff: ignore[import-outside-top-level]
            from weblate.trans.models.llm_usage import LLMUsageLog

            LLMUsageLog.objects.create(
                model=model,
                project_slug=project_slug,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=Decimal(str(cost)) if cost else None,
                response_id=str(payload.get("id") or ""),
                cached_tokens=prompt_details.get("cached_tokens") or 0,
                reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
            )
        except Exception:
            LOGGER.exception("Failed to record LLM usage")

    def get_chat_payload(
        self,
        model: str,
        prompt: str,
        content: str,
        previous_content: str,
        previous_response: str,
    ) -> dict:
        payload = {
            "model": model,
            # Translation under a strict markup contract needs determinism,
            # not sampling variety.
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": previous_content},
                {"role": "assistant", "content": previous_response},
                {"role": "user", "content": content},
            ],
        }
        # Only sent when configured: a cap that is too low truncates a valid
        # batch reply, and the provider default has not been observed to
        # truncate one.
        max_tokens = self.settings.get("max_tokens")
        if max_tokens:
            payload["max_tokens"] = max_tokens
        return payload

    @staticmethod
    def parse_chat_response(payload) -> str | None:
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices", [])
        first_choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        # A reply that did not end on its own is truncated, and the resulting
        # JSON is unparsable for a reason the caller cannot see otherwise.
        finish_reason = first_choice.get("finish_reason")
        if finish_reason not in {None, "stop"}:
            LOGGER.warning("LLM reply ended with finish_reason=%s", finish_reason)
        message_payload = first_choice.get("message", {})
        if isinstance(message_payload, dict):
            return message_payload.get("content")
        return None


class OpenAITranslation(BaseOpenAITranslation):
    name = "OpenAI"
    trusted_error_hosts: ClassVar[set[str]] = {"api.openai.com"}

    version_added = "5.3"

    settings_form: type[OpenAIMachineryForm | MistralMachineryForm] = (
        OpenAIMachineryForm
    )

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._models: set[str] | None = None

    def get_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings['key']}"}

    def get_runtime_base_url(self) -> str:
        return self.settings.get("base_url") or "https://api.openai.com/v1"

    def get_models_url(self) -> str:
        return self.join_api_url(self.get_runtime_base_url(), "models")

    def get_chat_completions_url(self) -> str:
        return self.join_api_url(self.get_runtime_base_url(), "chat/completions")

    def get_model(self) -> str:
        if self._models is None:
            cache_key = self.get_cache_key("models")
            models_cache = cache.get(cache_key)
            if models_cache is not None:
                # hiredis-py 3 makes list from set
                self._models = set(models_cache)
            else:
                payload = self.request("get", self.get_models_url()).json()
                self._models = self.parse_models(payload)
                cache.set(cache_key, self._models, 3600)

        return self.select_model()

    async def aget_model(self) -> str:
        if self._models is None:
            cache_key = self.get_cache_key("models")
            models_cache = await cache.aget(cache_key)
            if models_cache is not None:
                self._models = set(models_cache)
            else:
                payload = (await self.arequest("get", self.get_models_url())).json()
                self._models = self.parse_models(payload)
                await cache.aset(cache_key, self._models, 3600)

        return self.select_model()

    @staticmethod
    def parse_models(payload) -> set[str]:
        models = payload.get("data", []) if isinstance(payload, dict) else []
        return {
            model["id"]
            for model in models
            if isinstance(model, dict) and isinstance(model.get("id"), str)
        }

    def select_model(self) -> str:
        models = self._models if self._models is not None else set()
        if self.settings["model"] in models:
            return self.settings["model"]
        if self.settings["model"] == "auto":
            for model, _name in self.settings_form.MODEL_CHOICES:
                if model == "auto":
                    continue
                if model in models:
                    return model
        if self.settings["model"] == "custom":
            return self.settings["custom_model"]

        msg = f"Unsupported model: {self.settings['model']}"
        raise MachineTranslationError(msg)


class AzureOpenAITranslation(BaseOpenAITranslation):
    name = "Azure OpenAI"
    version_added = "5.8"
    settings_form = AzureOpenAIMachineryForm

    api_version = "2024-06-01"

    def get_headers(self) -> dict[str, str]:
        return {"api-key": self.settings["key"]}

    def get_runtime_base_url(self) -> str:
        return self.settings.get("azure_endpoint") or ""

    def get_chat_completions_url(self) -> str:
        deployment = quote(self.settings["deployment"], safe="")
        return self.join_api_url(
            self.get_runtime_base_url(),
            f"openai/deployments/{deployment}/chat/completions"
            f"?api-version={self.api_version}",
        )

    def get_model(self) -> str:
        return self.settings["deployment"]

    async def aget_model(self) -> str:
        return self.get_model()
