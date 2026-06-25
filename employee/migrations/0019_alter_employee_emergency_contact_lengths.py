from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("employee", "0018_bank_model"),
    ]

    operations = [
        migrations.AlterField(
            model_name="employee",
            name="emergency_contact_name",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="employee",
            name="emergency_contact_relation",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
    ]
