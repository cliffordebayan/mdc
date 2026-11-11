from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("base", "0016_historical_shiftrequest_portal_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="historicalshiftrequest",
            name="legacy_portal_id",
            field=models.PositiveBigIntegerField(
                blank=True,
                db_index=True,
                editable=False,
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="historicalshiftrequest",
            name="request_source",
            field=models.CharField(
                choices=[("web", "Web"), ("portal", "Portal")],
                db_index=True,
                default="web",
                max_length=12,
                verbose_name="Request Source",
            ),
        ),
    ]
