from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0006_payrollgroup_created_by_payrollgroup_modified_by_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='payrollgroup',
            name='start_date',
        ),
        migrations.RemoveField(
            model_name='payrollgroup',
            name='end_date',
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='start_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Start Day'),
        ),
        migrations.AddField(
            model_name='payrollgroup',
            name='end_day',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='End Day'),
        ),
    ]
