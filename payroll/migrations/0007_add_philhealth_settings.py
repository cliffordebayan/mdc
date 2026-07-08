from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0006_make_is_sss_editable'),
    ]

    operations = [
        migrations.CreateModel(
            name='PhilHealthSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True, verbose_name='Created At')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('floor_amount', models.FloatField(default=10000.0, help_text='Minimum monthly basic salary used as the contribution base.', verbose_name='Floor Amount')),
                ('ceiling_amount', models.FloatField(default=100000.0, help_text='Maximum monthly basic salary used as the contribution base.', verbose_name='Ceiling Amount')),
                ('total_rate', models.FloatField(default=5.0, help_text='Total premium rate as a percentage of the contribution base.', verbose_name='Total Rate (%)')),
                ('employee_share_rate', models.FloatField(default=2.5, verbose_name='Employee Share Rate (%)')),
                ('employer_share_rate', models.FloatField(default=2.5, verbose_name='Employer Share Rate (%)')),
                ('created_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('modified_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_modified_by', to=settings.AUTH_USER_MODEL, verbose_name='Modified By')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
