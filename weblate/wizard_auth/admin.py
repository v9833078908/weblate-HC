# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from django.contrib import admin

from weblate.wizard_auth.models import DelegationFamily


@admin.register(DelegationFamily)
class DelegationFamilyAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "client_id", "expires", "revoked")
    readonly_fields = ("id", "user", "client_id", "expires")

    def has_add_permission(self, request):
        return False
