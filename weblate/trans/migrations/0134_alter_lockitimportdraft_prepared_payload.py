# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from django.db import migrations, models

import weblate.trans.models.loc_kit


class Migration(migrations.Migration):
    dependencies = [
        ("trans", "0133_judge_application"),
    ]

    operations = [
        migrations.AlterField(
            model_name="lockitimportdraft",
            name="prepared_payload",
            field=models.FileField(
                blank=True,
                max_length=400,
                storage=weblate.trans.models.loc_kit.LOC_KIT_DRAFT_STORAGE,
                upload_to="prepared/%Y/%m/%d/",
            ),
        ),
    ]
