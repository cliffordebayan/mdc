from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('employee', '0016_historicalemployeeworkinformation_payroll_group_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeonboardingportal',
            name='sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
