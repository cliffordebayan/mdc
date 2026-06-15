from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0005_payrollgroup'),
        ('employee', '0014_alter_employeeworkinformation_employee_status_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeworkinformation',
            name='payroll_group_id',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='base.payrollgroup',
                verbose_name='Payroll Group',
            ),
        ),
    ]
