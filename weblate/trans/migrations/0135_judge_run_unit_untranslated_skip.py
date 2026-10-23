# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later


from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("trans", "0134_alter_lockitimportdraft_prepared_payload")]

    operations = [
        migrations.AlterField(
            model_name="judgerununit",
            name="skip_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("permission", "Permission"),
                    ("cap", "Cap"),
                    ("untranslated", "Untranslated"),
                ],
                max_length=20,
            ),
        ),
    ]
