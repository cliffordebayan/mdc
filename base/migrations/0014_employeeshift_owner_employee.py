from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0013_remove_employeeshift_is_flexible"),
        ("employee", "0020_alter_bank_modified_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeshift",
            name="owner_employee",
            field=models.OneToOneField(
                blank=True,
                help_text="Leave empty for a reusable shift template.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="owned_employee_shift",
                to="employee.employee",
                verbose_name="Employee Owner",
            ),
        ),
    ]
