from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payroll', '0025_allowance_payslip_category_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='HolidayPaySettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True, verbose_name='Created At')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('regular_holiday_worked_rate', models.FloatField(default=200.0, help_text='Percentage of the daily rate paid when a regular holiday is worked.', verbose_name='Regular Holiday, Worked (%)')),
                ('regular_holiday_unworked_rate', models.FloatField(default=100.0, help_text='Percentage of the daily rate paid when a regular holiday is not worked but the employee is eligible ("no work, still pay").', verbose_name='Regular Holiday, Unworked (%)')),
                ('regular_holiday_rest_day_worked_rate', models.FloatField(default=260.0, help_text="Percentage of the daily rate paid when a regular holiday that also falls on the employee's scheduled rest day is worked.", verbose_name='Regular Holiday on Rest Day, Worked (%)')),
                ('special_holiday_worked_rate', models.FloatField(default=130.0, verbose_name='Special Non-Working Holiday, Worked (%)')),
                ('special_holiday_unworked_rate', models.FloatField(default=0.0, help_text='"No work, no pay" unless company policy states otherwise.', verbose_name='Special Non-Working Holiday, Unworked (%)')),
                ('special_holiday_rest_day_worked_rate', models.FloatField(default=150.0, verbose_name='Special Holiday on Rest Day, Worked (%)')),
                ('rest_day_worked_premium_rate', models.FloatField(default=30.0, help_text="Additional percentage of the daily rate paid when an employee works on their scheduled rest day (non-holiday). This is added on top of the day's regular pay, which is already included in basic pay.", verbose_name='Ordinary Rest Day, Worked - Premium Addition (%)')),
                ('night_differential_rate', models.FloatField(default=10.0, help_text='Additional percentage of the hourly rate for hours worked between 10:00 PM and 6:00 AM.', verbose_name='Night Differential (%)')),
                ('created_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name='Created By')),
                ('modified_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='%(class)s_modified_by', to=settings.AUTH_USER_MODEL, verbose_name='Modified By')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
