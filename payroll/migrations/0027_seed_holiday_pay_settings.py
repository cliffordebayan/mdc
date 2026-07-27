from django.db import migrations

# Default Philippine DOLE holiday/rest-day pay premium rates. Verify these
# figures against the current DOLE labor advisory before trusting them in
# production; they are editable afterwards under
# Settings > Payroll > Holiday & Rest Day Pay.


def seed_holiday_pay_settings(apps, schema_editor):
    HolidayPaySettings = apps.get_model("payroll", "HolidayPaySettings")
    if not HolidayPaySettings.objects.exists():
        HolidayPaySettings.objects.create(
            regular_holiday_worked_rate=200.0,
            regular_holiday_unworked_rate=100.0,
            regular_holiday_rest_day_worked_rate=260.0,
            special_holiday_worked_rate=130.0,
            special_holiday_unworked_rate=0.0,
            special_holiday_rest_day_worked_rate=150.0,
            rest_day_worked_premium_rate=30.0,
            night_differential_rate=10.0,
        )


def remove_holiday_pay_settings(apps, schema_editor):
    HolidayPaySettings = apps.get_model("payroll", "HolidayPaySettings")
    HolidayPaySettings.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0026_add_holiday_pay_settings"),
    ]

    operations = [
        migrations.RunPython(seed_holiday_pay_settings, remove_holiday_pay_settings),
    ]
