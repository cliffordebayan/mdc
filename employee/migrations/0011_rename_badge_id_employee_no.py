from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employee", "0010_employeeworkinformation_pin_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="employee",
            name="unique_badge_id",
        ),
        migrations.RenameField(
            model_name="employee",
            old_name="badge_id",
            new_name="employee_no",
        ),
        migrations.RenameField(
            model_name="employeegeneralsetting",
            old_name="badge_id_prefix",
            new_name="employee_no_prefix",
        ),
        migrations.AddConstraint(
            model_name="employee",
            constraint=models.UniqueConstraint(
                condition=models.Q(employee_no__isnull=False),
                fields=["employee_no"],
                name="unique_employee_no",
            ),
        ),
    ]
