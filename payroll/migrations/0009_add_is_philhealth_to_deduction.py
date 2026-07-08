from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0008_seed_philhealth_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='deduction',
            name='is_philhealth',
            field=models.BooleanField(default=False, editable=False, help_text='Marks this deduction as the PhilHealth contribution, whose amount             is computed per employee from the PhilHealth Settings instead of a             fixed amount or rate.', verbose_name='PhilHealth Contribution'),
        ),
    ]
