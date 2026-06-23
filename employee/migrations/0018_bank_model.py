from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


PHILIPPINE_BANKS = [
    "AUB (Asia United Bank)",
    "BDO Unibank",
    "BPI (Bank of the Philippine Islands)",
    "Chinabank (China Banking Corporation)",
    "CIMB Bank Philippines",
    "DBP (Development Bank of the Philippines)",
    "EastWest Bank",
    "GCash",
    "GoTyme Bank",
    "Land Bank of the Philippines",
    "Maya (PayMaya)",
    "Metrobank",
    "OFBank",
    "PNB (Philippine National Bank)",
    "PSBank (Philippine Savings Bank)",
    "RCBC (Rizal Commercial Banking Corporation)",
    "Robinsons Bank",
    "SeaBank Philippines",
    "Security Bank",
    "Tonik Digital Bank",
    "UnionBank of the Philippines",
]


def seed_banks_and_migrate(apps, schema_editor):
    Bank = apps.get_model("employee", "Bank")
    EmployeeBankDetails = apps.get_model("employee", "EmployeeBankDetails")

    for name in PHILIPPINE_BANKS:
        Bank.objects.get_or_create(name=name)

    for detail in EmployeeBankDetails.objects.filter(bank__isnull=True):
        raw = (detail.bank_name_old or "").strip()
        if not raw:
            continue
        bank = Bank.objects.filter(name=raw).first()
        if bank is None:
            bank = Bank.objects.filter(name__iexact=raw).first()
        if bank is None:
            bank, _ = Bank.objects.get_or_create(name=raw)
        detail.bank = bank
        detail.save(update_fields=["bank"])


def reverse_migrate(apps, schema_editor):
    EmployeeBankDetails = apps.get_model("employee", "EmployeeBankDetails")
    for detail in EmployeeBankDetails.objects.select_related("bank"):
        if detail.bank:
            detail.bank_name_old = detail.bank.name
            detail.save(update_fields=["bank_name_old"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("employee", "0017_employeeonboardingportal_sent_at"),
    ]

    operations = [
        # 1. Create Bank table
        migrations.CreateModel(
            name="Bank",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, null=True, verbose_name="Created At")),
                ("is_active", models.BooleanField(default=True, verbose_name="Is Active")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("created_by", models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL, verbose_name="Created By")),
                ("modified_by", models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bank_modified_by", to=settings.AUTH_USER_MODEL, verbose_name="Modified By")),
            ],
            options={
                "verbose_name": "Bank",
                "verbose_name_plural": "Banks",
                "ordering": ["name"],
            },
        ),
        # 2. Rename old bank_name to temp column so we can read it during data migration
        migrations.RenameField(
            model_name="employeebankdetails",
            old_name="bank_name",
            new_name="bank_name_old",
        ),
        # 3. Add nullable FK column
        migrations.AddField(
            model_name="employeebankdetails",
            name="bank",
            field=models.ForeignKey(
                blank=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="employee.bank",
                verbose_name="Bank",
            ),
        ),
        # 4. Seed banks and migrate existing rows
        migrations.RunPython(seed_banks_and_migrate, reverse_migrate),
        # 5. Drop the old temp column
        migrations.RemoveField(
            model_name="employeebankdetails",
            name="bank_name_old",
        ),
    ]
