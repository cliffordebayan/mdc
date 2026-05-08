from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0001_initial'),
        ('employee', '0001_initial'),
        ('geofencing', '0001_initial'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='geofencing',
            name='unique_company_id_when_not_null_geofencing',
        ),
        migrations.RemoveField(
            model_name='geofencing',
            name='company_id',
        ),
        migrations.AddField(
            model_name='geofencing',
            name='branch_id',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='geo_fencing',
                to='base.branch',
            ),
        ),
        migrations.AddField(
            model_name='geofencing',
            name='excluded_employees',
            field=models.ManyToManyField(
                blank=True,
                related_name='geofence_excluded',
                to='employee.employee',
            ),
        ),
        migrations.AddConstraint(
            model_name='geofencing',
            constraint=models.UniqueConstraint(
                condition=models.Q(('branch_id', None), _negated=True),
                fields=('branch_id',),
                name='unique_branch_id_when_not_null_geofencing',
            ),
        ),
    ]
