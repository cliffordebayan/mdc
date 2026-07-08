from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0019_seed_bir_withholding_tax_monthly'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollsettings',
            name='tax_engine',
            field=models.CharField(choices=[('custom', 'Custom Tax Bracket'), ('bir', 'BIR Withholding Tax')], default='custom', help_text='Custom Tax Bracket uses the configurable Filing Status/Tax Bracket engine. BIR Withholding Tax uses the Philippine BIR withholding tax table, keyed by payroll frequency.', max_length=10, verbose_name='Tax Engine'),
        ),
    ]
