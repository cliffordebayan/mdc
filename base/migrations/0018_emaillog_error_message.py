from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0017_alter_historicalshiftrequest_portal_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="emaillog",
            name="error_message",
            field=models.TextField(blank=True, null=True),
        ),
    ]
