from django import forms
from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from employee.models import Employee

from .models import GeoFencing


class GeoFencingSetupForm(ModelForm):
    verbose_name = _("Geofence Configuration")

    class Meta:
        model = GeoFencing
        fields = ["name", "latitude", "longitude", "radius_in_meters", "start"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "oh-input w-100", "placeholder": _("e.g. HQ Office")}),
        }


class EmployeeGeofenceForm(forms.Form):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.filter(is_active=True),
        widget=forms.Select(attrs={"class": "oh-select oh-select-2 w-100", "data-placeholder": _("Select Employee")}),
        label=_("Select Employee"),
    )
    geofences = forms.ModelMultipleChoiceField(
        queryset=GeoFencing.objects.filter(start=True),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "oh-select oh-select-2 w-100", "data-placeholder": _("Select Geofences")}),
        label=_("Select Geofences"),
    )


class QuickGeoFenceForm(forms.ModelForm):
    class Meta:
        model = GeoFencing
        fields = ["name", "latitude", "longitude", "radius_in_meters", "start"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "oh-input w-100", "placeholder": _("HQ, Client Site A, etc.")}),
            "latitude": forms.NumberInput(attrs={"class": "oh-input w-100", "placeholder": "e.g. 14.5995"}),
            "longitude": forms.NumberInput(attrs={"class": "oh-input w-100", "placeholder": "e.g. 120.9842"}),
            "radius_in_meters": forms.NumberInput(attrs={"class": "oh-input w-100", "placeholder": "e.g. 200"}),
            "start": forms.CheckboxInput(attrs={"class": "oh-switch__input"}),
        }

