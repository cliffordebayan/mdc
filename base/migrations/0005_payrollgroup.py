from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('base', '0004_department_code'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, null=True)),
                ('name', models.CharField(max_length=100, verbose_name='Name')),
                ('description', models.TextField(blank=True, null=True, verbose_name='Description')),
                ('start_date', models.DateField(blank=True, null=True, verbose_name='Start Date')),
                ('end_date', models.DateField(blank=True, null=True, verbose_name='End Date')),
                ('company_id', models.ManyToManyField(blank=True, to='base.company', verbose_name='Company')),
            ],
            options={
                'verbose_name': 'Payroll Group',
                'verbose_name_plural': 'Payroll Groups',
            },
        ),
    ]
