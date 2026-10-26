# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import copy

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from weblate.trans.models import Project

FIELDS = {"persona", "style", "language_instructions"}
ENGINES = {"openrouter", "litellm"}


class TranslatorInstructionsSerializer(serializers.Serializer):
    persona = serializers.CharField(required=False, max_length=20000)
    style = serializers.CharField(required=False, max_length=20000)
    language_instructions = serializers.DictField(
        child=serializers.CharField(max_length=20000), required=False
    )


class ProducerProfileResponseSerializer(serializers.Serializer):
    openrouter = TranslatorInstructionsSerializer(required=False)
    litellm = TranslatorInstructionsSerializer(required=False)


class ProducerProfileRequestSerializer(serializers.Serializer):
    service = serializers.ChoiceField(choices=sorted(ENGINES))
    configuration = TranslatorInstructionsSerializer()


def public_profiles(project):
    return {
        engine: {
            key: copy.deepcopy(value) for key, value in config.items() if key in FIELDS
        }
        for engine, config in project.machinery_settings.items()
        if engine in ENGINES and isinstance(config, dict)
    }


class ProducerTranslationProfile(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses=ProducerProfileResponseSerializer)
    def get(self, request, slug):
        project = get_object_or_404(request.user.allowed_projects, slug=slug)
        return Response(public_profiles(project))

    @extend_schema(
        request=ProducerProfileRequestSerializer,
        responses=ProducerProfileResponseSerializer,
    )
    def patch(self, request, slug):
        service, config = request.data.get("service"), request.data.get("configuration")
        if (
            service not in ENGINES
            or not isinstance(config, dict)
            or not set(config) <= FIELDS
        ):
            msg = "Only translator instructions may be updated"
            raise ValidationError(msg)
        for key, value in config.items():
            if key == "language_instructions":
                if (
                    not isinstance(value, dict)
                    or len(value) > 100
                    or any(
                        not isinstance(code, str)
                        or not 1 <= len(code) <= 50
                        or not isinstance(text, str)
                        or len(text) > 20000
                        for code, text in value.items()
                    )
                ):
                    msg = "Invalid language instructions"
                    raise ValidationError(msg)
            elif not isinstance(value, str) or len(value) > 20000:
                msg = "Invalid translator instructions"
                raise ValidationError(msg)
        with transaction.atomic():
            project = get_object_or_404(
                Project.objects.select_for_update().filter(
                    pk__in=request.user.allowed_projects.values("pk")
                ),
                slug=slug,
            )
            if not request.user.has_perm("project.edit", project):
                msg = "Project edit permission required"
                raise PermissionDenied(msg)
            current = project.machinery_settings.get(service, {})
            if not isinstance(current, dict):
                msg = "Invalid existing service configuration"
                raise ValidationError(msg)
            project.machinery_settings = {
                **project.machinery_settings,
                service: {**current, **config},
            }
            project.save(update_fields=["machinery_settings"])
        return Response(public_profiles(project))
