# Copyright © Michal Čihař <michal@weblate.org>
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal
from typing import Any, ClassVar
from urllib.parse import quote, urljoin

import httpx2
from asgiref.sync import sync_to_async
from django.core.cache import cache

from weblate.logger import LOGGER
from weblate.utils.requests import JSON_RESPONSE_ERRORS

from .base import (
    MachineryRateLimitError,
    MachineTranslationError,
)
from .forms import AzureOpenAIMachineryForm, MistralMachineryForm, OpenAIMachineryForm
from .llm import (
    BaseLLMTranslation,
    llm_batch_component,
    llm_batch_component_id,
    llm_batch_project,
    llm_batch_project_id,
    llm_batch_target_language,
    llm_batch_unit_count,
    llm_delivery_usage,
    llm_delivery_work,
    llm_usage_record,
)


class BaseOpenAITranslation(BaseLLMTranslation):
    #: Stage of the opted-in MT requests this instance makes, set by the
    #: orchestrator before the first send. The per-request stage context is
    #: carried by ContextVars, never by this attribute alone.
    _mt_stage: str | None = None
    _mt_fallback_authorization_id = None
    #: The original actor a fallback stage acts for. The pre-send guard
    #: re-reads this account's current rights, never the worker's.
    _mt_stage_actor = None
    _mt_stage_mode: str = "translate"

    def request(self, method, url, skip_auth=False, **kwargs):
        # The stage of this internal request comes from the per-request
        # context, never from the engine instance: a scheduler thread sharing
        # one engine must not leak another unit's stage into this request.
        stage, slots, authorization_id = self._mt_stage_context()
        work = llm_delivery_work.get()
        if method.lower() != "post":
            return super().request(method, url, skip_auth=skip_auth, **kwargs)
        if self.durable_single_attempt and stage is None:
            # The durable journal owns its own identity and spend fence; it is
            # not an opted-in MT stage and keeps its original lifecycle.
            if not work:
                return super().request(method, url, skip_auth=skip_auth, **kwargs)
            return self._durable_request(method, url, skip_auth, work, **kwargs)
        if stage is None or not self.credit_fallback_single_attempt:
            return super().request(method, url, skip_auth=skip_auth, **kwargs)
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.llm_delivery import encode_http_response

        codec = getattr(self, "delivery_request_codec", "opaque-v1")
        digest = hashlib.sha256(
            json.dumps(
                {"url": url, "method": method.lower(), "payload": kwargs.get("json")},
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode()
        ).hexdigest()
        # Secret-free snapshot of the wire request actually sent, committed
        # before transport: method, URL, payload, codec and occurrence mapping.
        # The parser data below it is frozen too, so a local replay restores
        # the same answer from the same sources without rebuilding a request
        # from the current configuration.
        snapshot = {
            "method": method.lower(),
            "url": url,
            "payload": kwargs.get("json"),
            "codec": codec,
            "slots": [
                {"unit_stage_id": str(unit_stage_id), "source_slot": source_slot}
                for unit_stage_id, source_slot in slots
            ],
            "parser": self._frozen_parser_state(codec),
        }
        request = super().request

        def send():
            return encode_http_response(
                request(method, url, skip_auth=skip_auth, **kwargs)
            )

        if stage == "fallback":
            # ruff: ignore[import-outside-top-level]
            from weblate.trans.models.mt_fallback import (
                ProducerMTFallbackAuthorization,
            )

            # ruff: ignore[import-outside-top-level]
            from weblate.trans.mt_fallback import (
                dispatch_mt_fallback_exchange,
                stage_send_guard,
            )

            # The guard is rebuilt per request from the exact members this
            # request is about to pay for, so a target a human wrote in the
            # meantime blocks this send.
            authorization = ProducerMTFallbackAuthorization.objects.get(
                pk=authorization_id
            )
            before_send = stage_send_guard(
                authorization,
                actor=getattr(self, "_mt_stage_actor", None),
                slots=slots,
                mode=self._mt_stage_mode,
            )
            result = dispatch_mt_fallback_exchange(
                authorization_id,
                slots,
                digest,
                send,
                request_snapshot=snapshot,
                request_codec=codec,
                run_id=self.usage_run_id,
                before_send=before_send,
            )
        else:
            if not slots or authorization_id is None:
                msg = "Opted-in primary stage requires an enrolled stage context"
                raise MachineTranslationError(msg)
            if self.durable_single_attempt:
                if not work:
                    msg_0 = "Durable primary stage requires canonical work"
                    raise MachineTranslationError(msg_0)
                # ruff: ignore[import-outside-top-level]
                from weblate.trans.llm_delivery import (
                    dispatch_exchange,
                    exchange_for_work,
                )

                receipt = dispatch_exchange(
                    work,
                    digest,
                    send,
                    run_id=self.usage_run_id,
                    request_codec=codec,
                    replay_only=self.delivery_replay_only or codec == "routed-parts-v1",
                    mt_primary_context=(authorization_id, slots, snapshot),
                )
                llm_delivery_usage.set(exchange_for_work(work))
                saved = json.loads(receipt)
            else:
                # ruff: ignore[import-outside-top-level]
                from weblate.trans.mt_fallback import dispatch_mt_primary_exchange

                result = dispatch_mt_primary_exchange(
                    authorization_id,
                    slots,
                    digest,
                    send,
                    request_snapshot=snapshot,
                    request_codec=codec,
                    run_id=self.usage_run_id,
                )
        if stage == "fallback" or not self.durable_single_attempt:
            llm_delivery_usage.set(result.exchange_id)
            saved = json.loads(result.receipt)
        # Usage belongs to the actual primary or fallback exchange.
        response = httpx2.Response(
            saved["status"],
            content=saved["body"].encode(),
            headers=saved["headers"],
            request=httpx2.Request(method, url),
        )
        self.check_failure(response)
        return response

    def _durable_request(self, method, url, skip_auth, work, **kwargs):
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.llm_delivery import (
            dispatch_exchange,
            encode_http_response,
            exchange_for_work,
        )

        codec = getattr(self, "delivery_request_codec", "opaque-v1")
        digest = hashlib.sha256(
            json.dumps(
                {"url": url, "method": method.lower(), "payload": kwargs.get("json")},
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode()
        ).hexdigest()
        request = super().request

        def send():
            return encode_http_response(
                request(method, url, skip_auth=skip_auth, **kwargs)
            )

        saved = json.loads(
            dispatch_exchange(
                work,
                digest,
                send,
                run_id=self.usage_run_id,
                request_codec=codec,
                replay_only=self.delivery_replay_only or codec == "routed-parts-v1",
            )
        )
        llm_delivery_usage.set(exchange_for_work(work))
        response = httpx2.Response(
            saved["status"],
            content=saved["body"].encode(),
            headers=saved["headers"],
            request=httpx2.Request(method, url),
        )
        self.check_failure(response)
        return response

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
            code = error.get("code")
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None

    def check_failure(self, response) -> None:
        if response.status_code == 429:
            message = self.get_error_detail(response) or "Rate limit exceeded"
            raise MachineryRateLimitError(message)
        error = self._get_upstream_error(response)
        if error is not None:
            message = str(error.get("message") or "Upstream error")
            upstream_status = self._get_upstream_error_status(error)
            if upstream_status in self.retry_statuses:
                raise MachineryRateLimitError(message)
            # A gateway answers 200 with the upstream refusal inside; classify
            # it by that status, not by the HTTP 200 envelope. Without an
            # upstream status, the response's own HTTP status applies.
            if upstream_status is not None:
                reason = self.get_service_failure_reason_for_status(
                    response, upstream_status
                )
            else:
                reason = self.get_service_failure_reason(response)
            if reason:
                self.raise_service_failure(
                    response, reason, upstream_status=upstream_status
                )
            raise MachineTranslationError(message)
        if reason := self.get_service_failure_reason(response):
            self.raise_service_failure(response, reason)
        super().check_failure(response)

    def get_service_failure_reason_for_status(
        self, response, status: int
    ) -> str | None:
        """Classify by an upstream status reported inside a 200 envelope."""
        synthetic = copy.copy(response)
        synthetic.status_code = status
        return self.get_service_failure_reason(synthetic)

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
        payload = response.json(parse_float=Decimal)
        self.record_llm_usage(payload, model, self.batch_string_count(content))
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
        payload = response.json(parse_float=Decimal)
        await sync_to_async(self.record_llm_usage, thread_sensitive=True)(
            payload, model, self.batch_string_count(content)
        )
        return self.parse_chat_response(payload)

    @staticmethod
    def batch_string_count(content: str) -> int:
        """How many strings the request asks for, read back from its body."""
        try:
            strings = json.loads(content)["strings"]
        except (TypeError, KeyError, ValueError):
            return 0
        return len(strings) if isinstance(strings, list) else 0

    def record_llm_usage(
        self, payload: dict[str, Any], model: str, batch_size: int = 0
    ) -> None:
        """
        Persist the token usage and cost OpenRouter billed for this request.

        Never raises: a broken accounting write must not break a translation,
        and the exception is logged so a broken table is visible in the log.
        """
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            delivery_id = llm_delivery_usage.get()
            if (
                self.durable_single_attempt or self.credit_fallback_single_attempt
            ) and delivery_id is not None:
                from weblate.trans.llm_delivery import (  # ruff: ignore[import-outside-top-level]
                    record_usage_once,
                )

                record_usage_once(
                    delivery_id,
                    lambda: self._write_llm_usage(payload, model, batch_size),
                )
            else:
                self._write_llm_usage(payload, model, batch_size)
        except Exception:
            LOGGER.exception("Failed to record LLM usage")

    def _write_llm_usage(
        self, payload: dict[str, Any], model: str, batch_size: int = 0
    ) -> None:
        if not isinstance(payload, dict):
            return
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        total_tokens = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
        # ruff: ignore[import-outside-top-level]
        from weblate.trans.models.llm_usage import LLMUsageLog, parse_provider_cost

        cost = parse_provider_cost(usage.get("cost"))
        if not prompt_tokens and not completion_tokens and cost is None:
            return
        prompt_details = usage.get("prompt_tokens_details")
        completion_details = usage.get("completion_tokens_details")
        if not isinstance(prompt_details, dict):
            prompt_details = {}
        if not isinstance(completion_details, dict):
            completion_details = {}
        cached_tokens = prompt_details.get("cached_tokens")
        if (
            not isinstance(cached_tokens, int)
            or isinstance(cached_tokens, bool)
            or not 0 <= cached_tokens < 2**31
        ):
            cached_tokens = 0
        reasoning_tokens = completion_details.get("reasoning_tokens")
        if (
            not isinstance(reasoning_tokens, int)
            or isinstance(reasoning_tokens, bool)
            or not 0 <= reasoning_tokens < 2**31
        ):
            reasoning_tokens = 0
        project_slug = llm_batch_project.get()
        project_id_snapshot = llm_batch_project_id.get()
        if project_slug is None:
            project = self.settings.get("_project")
            project_slug = project.slug if project is not None else ""
            project_id_snapshot = project.pk if project is not None else None

        record = LLMUsageLog.objects.create(
            model=model,
            service=self.get_identifier(),
            project_id_snapshot=project_id_snapshot,
            project_slug=project_slug,
            component_id_snapshot=llm_batch_component_id.get(),
            component_slug=llm_batch_component.get(),
            target_language_code=llm_batch_target_language.get(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            response_id=str(payload.get("id") or ""),
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            operation=LLMUsageLog.Operation.TRANSLATION,
            unit_count=llm_batch_unit_count.get() or None,
            batch_size=batch_size,
            run_id=self.usage_run_id,
        )
        llm_usage_record.set(record.pk)

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
