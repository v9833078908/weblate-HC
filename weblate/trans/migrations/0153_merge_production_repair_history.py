# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0147_judge_application_change_restrict"),
        ("trans", "0152_delivery_retention"),
    ]

    operations = []
