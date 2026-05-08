from django import forms
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from employee.models import Employee

from .models import GeoFencing


class GeoFencingSetupForm(ModelForm):
    verbose_name = _("Geofence Configuration")

    excluded_employees = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(is_active=True),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100"}),
        label=_("Excluded Employees"),
        help_text=_("Employees in this list will bypass geofence validation."),
    )

    class Meta:
        model = GeoFencing
        fields = ["branch_id", "latitude", "longitude", "radius_in_meters", "start", "excluded_employees"]
        widgets = {
            "branch_id": forms.Select(attrs={"class": "oh-select oh-select-2 w-100"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["excluded_employees"].initial = self.instance.excluded_employees.all()
