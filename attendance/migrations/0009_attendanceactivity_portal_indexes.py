from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0008_attendancegeneralsetting_portal_lunch_minutes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="attendanceactivity",
            index=models.Index(
                fields=["employee_id", "clock_out"],
                name="att_act_emp_open_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="attendanceactivity",
            index=models.Index(
                fields=["employee_id", "attendance_date", "activity_type"],
                name="att_act_emp_date_type_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="attendanceactivity",
            index=models.Index(
                fields=["employee_id", "attendance_date", "clock_in_date", "clock_in"],
                name="att_act_emp_date_in_idx",
            ),
        ),
    ]
