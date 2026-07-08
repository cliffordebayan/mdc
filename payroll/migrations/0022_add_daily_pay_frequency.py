from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0021_remove_federal_tax_engine'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contract',
            name='pay_frequency',
            field=models.CharField(choices=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly'), ('semi_monthly', 'Semi-Monthly')], default='monthly', max_length=20, null=True, verbose_name='Pay Frequency'),
        ),
        migrations.AlterField(
            model_name='historicalcontract',
            name='pay_frequency',
            field=models.CharField(choices=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly'), ('semi_monthly', 'Semi-Monthly')], default='monthly', max_length=20, null=True, verbose_name='Pay Frequency'),
        ),
        migrations.AlterField(
            model_name='birwithholdingtax',
            name='frequency',
            field=models.CharField(choices=[('daily', 'Daily'), ('weekly', 'Weekly'), ('semi_monthly', 'Semi-Monthly'), ('monthly', 'Monthly')], max_length=20, verbose_name='Frequency'),
        ),
    ]
