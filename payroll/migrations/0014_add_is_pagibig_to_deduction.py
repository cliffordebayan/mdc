from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0013_seed_pagibig_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='deduction',
            name='is_pagibig',
            field=models.BooleanField(default=False, editable=False, help_text='Marks this deduction as the Pag-IBIG (HDMF) contribution, whose             amount is computed per employee from the Pag-IBIG Settings instead             of a fixed amount or rate.', verbose_name='Pag-IBIG Contribution'),
        ),
    ]
