"""
Forms for handling payroll-related operations.

This module provides Django ModelForms for creating and managing
payroll-related statutory deduction/withholding-tax data (SSS, PhilHealth,
Pag-IBIG, BIR withholding tax).

The forms in this module inherit from the Django `forms.ModelForm` class and customize
the widget attributes to enhance the user interface and provide a better user experience.

"""

from django.utils.translation import gettext_lazy as _

from base.forms import ModelForm
from payroll.models.tax_models import (
    BIRWithholdingTax,
    HolidayPaySettings,
    PagibigSettings,
    PerfectAttendanceBonusSettings,
    PhilHealthSettings,
    SSSContribution,
)


class SSSContributionForm(ModelForm):
    """Form for creating and updating SSS contribution brackets."""

    class Meta:
        """Meta options for the form."""

        model = SSSContribution
        fields = "__all__"
        exclude = ["is_active"]


class PhilHealthSettingsForm(ModelForm):
    """Form for editing the singleton PhilHealth settings."""

    class Meta:
        """Meta options for the form."""

        model = PhilHealthSettings
        fields = "__all__"
        exclude = ["is_active"]


class PagibigSettingsForm(ModelForm):
    """Form for editing the singleton Pag-IBIG settings."""

    class Meta:
        """Meta options for the form."""

        model = PagibigSettings
        fields = "__all__"
        exclude = ["is_active"]


class PerfectAttendanceBonusSettingsForm(ModelForm):
    """Form for editing the singleton Perfect Attendance Bonus settings."""

    class Meta:
        """Meta options for the form."""

        model = PerfectAttendanceBonusSettings
        fields = "__all__"
        exclude = ["is_active"]


class HolidayPaySettingsForm(ModelForm):
    """Form for editing the singleton Holiday & Rest Day Pay settings."""

    class Meta:
        """Meta options for the form."""

        model = HolidayPaySettings
        fields = "__all__"
        exclude = ["is_active"]


class BIRWithholdingTaxForm(ModelForm):
    """Form for creating and updating BIR withholding tax brackets."""

    class Meta:
        """Meta options for the form."""

        model = BIRWithholdingTax
        fields = "__all__"
        exclude = ["is_active"]
