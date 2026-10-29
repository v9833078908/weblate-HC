# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0147_producer_quality_operations"),
        ("trans", "0148_preserve_workspace_project_history"),
    ]

    operations = []
