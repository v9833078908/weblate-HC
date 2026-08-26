# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    """
    Join the two parallel leaves produced by merging main.

    ``0105_alter_change_action`` (the tip of this branch's chain, which already
    descends from ``0103_judge_target_storage_hash``) and main's
    ``0103_llmusagelog_batch_size_llmusagelog_outcome`` are the two leaves; they
    touch disjoint models, so no data operation is needed - only a common
    successor so later migrations have a single predecessor again.
    """

    dependencies = [
        ("trans", "0103_llmusagelog_batch_size_llmusagelog_outcome"),
        ("trans", "0105_alter_change_action"),
    ]

    operations = []
