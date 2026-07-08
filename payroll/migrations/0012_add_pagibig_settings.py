from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0011_make_is_philhealth_editable'),
    ]

    operations = [
        migrations.CreateModel(
            name='PagibigSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True, verbose_name='Created At')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('threshold_amount', models.FloatField(default=1500.0, help_text='Monthly compensation at or below which the lower employee rate applies.', verbose_name='Threshold Amount')),
                ('employee_rate_below_threshold', models.FloatField(default=1.0, verbose_name='Employee Rate At/Below Threshold (%)')),
                ('employee_rate_above_threshold', models.FloatField(default=2.0, verbose_name='Employee Rate Above Threshold (%)')),
                ('employer_rate', models.FloatField(default=2.0, verbose_name='Employer Rate (%)')),
                ('contribution_cap', models.FloatField(default=10000.0, help_text='Maximum monthly fund credit compensation used as the contribution base.', verbose_name='Contribution Cap')),
                ('created_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('modified_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_modified_by', to=settings.AUTH_USER_MODEL, verbose_name='Modified By')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
