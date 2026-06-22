from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0007_alter_payrollgroup_start_end_day'),
    ]

    operations = [
        migrations.AddField(
            model_name='payrollgroup',
            name='frequency',
            field=models.CharField(
                blank=True,
                choices=[
                    ('monthly', 'Monthly'),
                    ('semi_monthly', 'Semi-Monthly'),
                    ('weekly', 'Weekly'),
                ],
                max_length=20,
                null=True,
                verbose_name='Frequency',
            ),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='first_payout_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Payout Day'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='second_cut_off_start',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off Start'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='second_cut_off_end',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off End'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='second_payout_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Payout Day'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='third_cut_off_start',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off Start'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='third_cut_off_end',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off End'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='third_payout_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Payout Day'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='fourth_cut_off_start',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off Start'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='fourth_cut_off_end',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off End'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='fourth_payout_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Payout Day'),
        ),
        migrations.AlterField(
            model_name='payrollgroup',
            name='start_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off Start'),
        ),
        migrations.AlterField(
            model_name='payrollgroup',
            name='end_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Cut-Off End'),
        ),
    ]
