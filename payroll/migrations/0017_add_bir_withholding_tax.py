from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0016_make_is_pagibig_editable'),
    ]

    operations = [
        migrations.CreateModel(
            name='BIRWithholdingTax',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True, verbose_name='Created At')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('frequency', models.CharField(choices=[('weekly', 'Weekly'), ('semi_monthly', 'Semi-Monthly'), ('monthly', 'Monthly')], max_length=20, verbose_name='Frequency')),
                ('min_income', models.FloatField(verbose_name='Min. Income')),
                ('max_income', models.FloatField(blank=True, null=True, verbose_name='Max. Income')),
                ('base_tax', models.FloatField(default=0.0, verbose_name='Base Tax')),
                ('excess_rate', models.FloatField(default=0.0, help_text='Percentage applied to the amount over Min. Income.', verbose_name='Excess Rate (%)')),
                ('created_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('modified_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_modified_by', to=settings.AUTH_USER_MODEL, verbose_name='Modified By')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
