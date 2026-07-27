"""
tax_models.py

This module contains the models for the tax calculation of taxable income.
"""

import math

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from base.horilla_company_manager import HorillaCompanyManager
from base.models import Company
from horilla.models import HorillaModel


class PayrollSettings(HorillaModel):
    """
    Payroll settings model
    """

    choices = [
        ("prefix", _("Prefix")),
        ("postfix", _("Postfix")),
    ]

    currency_symbol = models.CharField(null=True, default="$", max_length=5)
    position = models.CharField(
        max_length=15, null=True, choices=choices, default="postfix"
    )

    company_id = models.ForeignKey(Company, null=True, on_delete=models.PROTECT)
    objects = HorillaCompanyManager("company_id")

    class Meta:
        verbose_name = _("Payroll Settings")
        verbose_name_plural = _("Payroll Settings")

    def __str__(self):
        return f"Payroll Settings {self.currency_symbol}"


class SSSContribution(HorillaModel):
    """
    SSSContribution model

    Stores the Philippine SSS contribution schedule for Business Employers
    and Employees (SSS Circular 2024-006, effective January 2025), keyed by
    range of monthly compensation.
    """

    range_from = models.FloatField(null=False, blank=False, verbose_name=_("Range From"))
    range_to = models.FloatField(null=True, blank=True, verbose_name=_("Range To"))

    msc_regular_ss = models.FloatField(
        default=0.0, verbose_name=_("MSC Regular SS")
    )
    msc_mpf = models.FloatField(default=0.0, verbose_name=_("MSC MPF"))

    employer_regular_ss = models.FloatField(
        default=0.0, verbose_name=_("Employer Regular SS")
    )
    employer_mpf = models.FloatField(default=0.0, verbose_name=_("Employer MPF"))
    employer_ec = models.FloatField(default=0.0, verbose_name=_("Employer EC"))

    employee_regular_ss = models.FloatField(
        default=0.0, verbose_name=_("Employee Regular SS")
    )
    employee_mpf = models.FloatField(default=0.0, verbose_name=_("Employee MPF"))

    objects = models.Manager()

    def __str__(self):
        return f"SSS Contribution {self.range_from} - {self.get_display_range_to()}"

    def get_display_range_to(self):
        """
        Retrieves the maximum range.
        Returns:
            float or None: The maximum range if it is a finite value, otherwise None.
        """
        if self.range_to != math.inf:
            return self.range_to
        return None

    @property
    def msc_total(self):
        return self.msc_regular_ss + self.msc_mpf

    @property
    def employer_total(self):
        return self.employer_regular_ss + self.employer_mpf + self.employer_ec

    @property
    def employee_total(self):
        return self.employee_regular_ss + self.employee_mpf

    @property
    def grand_total(self):
        return self.employer_total + self.employee_total

    def clean(self):
        super().clean()

        existing_bracket = SSSContribution.objects.filter(
            range_from=self.range_from,
            range_to=self.range_to,
        ).exclude(pk=self.pk)
        if existing_bracket.exists():
            raise ValidationError(_("This SSS contribution bracket already exists"))

        if self.range_to is None:
            self.range_to = math.inf

        if self.range_from >= self.range_to:
            raise ValidationError(
                {"range_to": _("Range To must be greater than Range From.")}
            )

        existing_brackets = SSSContribution.objects.exclude(pk=self.pk)
        if existing_brackets.filter(range_to__gte=self.range_from).exists():
            overlapping_bracket = existing_brackets.filter(
                range_to__gte=self.range_from
            ).first()
            if overlapping_bracket.range_from <= self.range_to:
                raise ValidationError(
                    {
                        "range_from": format_lazy(
                            "The Range From of this bracket must be \
                                greater than the Range To of {}.",
                            overlapping_bracket,
                        )
                    }
                )


class PhilHealthSettings(HorillaModel):
    """
    PhilHealthSettings model

    Stores the single Philippine PhilHealth premium rate configuration
    (rate frozen at 5% since 2024, split equally between employee and
    employer, with a salary floor of 10,000 and ceiling of 100,000).
    Verify these figures against the current PhilHealth Circular before
    relying on them in production.
    """

    floor_amount = models.FloatField(
        default=10000.0,
        verbose_name=_("Floor Amount"),
        help_text=_(
            "Minimum monthly basic salary used as the contribution base."
        ),
    )
    ceiling_amount = models.FloatField(
        default=100000.0,
        verbose_name=_("Ceiling Amount"),
        help_text=_(
            "Maximum monthly basic salary used as the contribution base."
        ),
    )
    total_rate = models.FloatField(
        default=5.0,
        verbose_name=_("Total Rate (%)"),
        help_text=_("Total premium rate as a percentage of the contribution base."),
    )
    employee_share_rate = models.FloatField(
        default=2.5,
        verbose_name=_("Employee Share Rate (%)"),
    )
    employer_share_rate = models.FloatField(
        default=2.5,
        verbose_name=_("Employer Share Rate (%)"),
    )

    objects = models.Manager()

    def __str__(self):
        return f"PhilHealth Settings ({self.total_rate}%)"

    def clean(self):
        super().clean()

        existing = PhilHealthSettings.objects.exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError(
                _("PhilHealth settings already exist. Only one is allowed.")
            )

        if self.floor_amount >= self.ceiling_amount:
            raise ValidationError(
                {
                    "ceiling_amount": _(
                        "Ceiling amount must be greater than floor amount."
                    )
                }
            )

        if abs(
            (self.employee_share_rate + self.employer_share_rate) - self.total_rate
        ) > 0.001:
            raise ValidationError(
                {
                    "total_rate": _(
                        "Total rate must equal the sum of employee and employer "
                        "share rates."
                    )
                }
            )


class PagibigSettings(HorillaModel):
    """
    PagibigSettings model

    Stores the single Philippine Pag-IBIG (HDMF) contribution rate
    configuration per Pag-IBIG Fund Circular No. 460-2024: employee rate
    is 1% of monthly compensation at or below the threshold, else 2%;
    employer rate is always 2%; contributions are computed against a
    capped monthly fund credit compensation base. Verify these figures
    against the current Pag-IBIG circular before relying on them in
    production.
    """

    threshold_amount = models.FloatField(
        default=1500.0,
        verbose_name=_("Threshold Amount"),
        help_text=_(
            "Monthly compensation at or below which the lower employee "
            "rate applies."
        ),
    )
    employee_rate_below_threshold = models.FloatField(
        default=1.0,
        verbose_name=_("Employee Rate At/Below Threshold (%)"),
    )
    employee_rate_above_threshold = models.FloatField(
        default=2.0,
        verbose_name=_("Employee Rate Above Threshold (%)"),
    )
    employer_rate = models.FloatField(
        default=2.0,
        verbose_name=_("Employer Rate (%)"),
    )
    contribution_cap = models.FloatField(
        default=10000.0,
        verbose_name=_("Contribution Cap"),
        help_text=_(
            "Maximum monthly fund credit compensation used as the "
            "contribution base."
        ),
    )

    objects = models.Manager()

    def __str__(self):
        return f"Pag-IBIG Settings (cap {self.contribution_cap})"

    def clean(self):
        super().clean()

        existing = PagibigSettings.objects.exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError(
                _("Pag-IBIG settings already exist. Only one is allowed.")
            )

        if self.threshold_amount >= self.contribution_cap:
            raise ValidationError(
                {
                    "threshold_amount": _(
                        "Threshold amount must be less than the contribution cap."
                    )
                }
            )


class PerfectAttendanceBonusSettings(HorillaModel):
    """
    PerfectAttendanceBonusSettings model

    Stores the single flat bonus amount awarded to an employee for a
    payroll period in which they have zero late-come/undertime occurrences
    and zero unpaid absence days.
    """

    bonus_amount = models.FloatField(
        default=0.0,
        verbose_name=_("Bonus Amount"),
        help_text=_(
            "Flat amount awarded when an employee has no late, undertime, "
            "or unpaid absence occurrences within the payroll period."
        ),
    )
    is_enabled = models.BooleanField(
        default=False,
        verbose_name=_("Enabled"),
        help_text=_(
            "Enable automatic Perfect Attendance Bonus eligibility checking "
            "during payslip generation."
        ),
    )

    objects = models.Manager()

    def __str__(self):
        return f"Perfect Attendance Bonus Settings ({self.bonus_amount})"

    def clean(self):
        super().clean()

        existing = PerfectAttendanceBonusSettings.objects.exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError(
                _(
                    "Perfect Attendance Bonus settings already exist. "
                    "Only one is allowed."
                )
            )


class HolidayPaySettings(HorillaModel):
    """
    HolidayPaySettings model

    Stores the configurable Philippine DOLE holiday and rest-day pay
    premium rates (as a percentage of the employee's daily rate), so they
    can be adjusted without a code change if the labor advisory changes.

    Regular and special holidays are excluded from the paid working-days
    count used for basic pay (see base.methods.get_working_days), so their
    rates below are the *full* percentage paid for that day. Ordinary rest
    days are not excluded from that count -- the day's regular pay is
    already included in basic pay -- so rest_day_worked_premium_rate is
    only the *additional* premium on top of it.
    """

    regular_holiday_worked_rate = models.FloatField(
        default=200.0,
        verbose_name=_("Regular Holiday, Worked (%)"),
        help_text=_(
            "Percentage of the daily rate paid when a regular holiday is worked."
        ),
    )
    regular_holiday_unworked_rate = models.FloatField(
        default=100.0,
        verbose_name=_("Regular Holiday, Unworked (%)"),
        help_text=_(
            "Percentage of the daily rate paid when a regular holiday is not "
            "worked but the employee is eligible (\"no work, still pay\")."
        ),
    )
    regular_holiday_rest_day_worked_rate = models.FloatField(
        default=260.0,
        verbose_name=_("Regular Holiday on Rest Day, Worked (%)"),
        help_text=_(
            "Percentage of the daily rate paid when a regular holiday that "
            "also falls on the employee's scheduled rest day is worked."
        ),
    )
    special_holiday_worked_rate = models.FloatField(
        default=130.0,
        verbose_name=_("Special Non-Working Holiday, Worked (%)"),
    )
    special_holiday_unworked_rate = models.FloatField(
        default=0.0,
        verbose_name=_("Special Non-Working Holiday, Unworked (%)"),
        help_text=_(
            "\"No work, no pay\" unless company policy states otherwise."
        ),
    )
    special_holiday_rest_day_worked_rate = models.FloatField(
        default=150.0,
        verbose_name=_("Special Holiday on Rest Day, Worked (%)"),
    )
    rest_day_worked_premium_rate = models.FloatField(
        default=30.0,
        verbose_name=_("Ordinary Rest Day, Worked - Premium Addition (%)"),
        help_text=_(
            "Additional percentage of the daily rate paid when an employee "
            "works on their scheduled rest day (non-holiday). This is added "
            "on top of the day's regular pay, which is already included in "
            "basic pay."
        ),
    )
    night_differential_rate = models.FloatField(
        default=10.0,
        verbose_name=_("Night Differential (%)"),
        help_text=_(
            "Additional percentage of the hourly rate for hours worked "
            "between 10:00 PM and 6:00 AM."
        ),
    )

    objects = models.Manager()

    def __str__(self):
        return "Holiday & Rest Day Pay Settings"

    def clean(self):
        super().clean()

        existing = HolidayPaySettings.objects.exclude(pk=self.pk)
        if existing.exists():
            raise ValidationError(
                _("Holiday pay settings already exist. Only one is allowed.")
            )


class BIRWithholdingTax(HorillaModel):
    """
    BIRWithholdingTax model

    Stores the Philippine BIR withholding tax table (per the TRAIN law
    revised schedule effective since January 1, 2023, RA 10963/RR 8-2018 as
    amended), keyed by payroll frequency (weekly/semi_monthly/monthly) since
    the bracket boundaries differ per frequency. Verify these bracket
    cutoffs against the current official BIR withholding tax table before
    trusting this in production.
    """

    FREQUENCY_CHOICES = [
        ("daily", _("Daily")),
        ("weekly", _("Weekly")),
        ("semi_monthly", _("Semi-Monthly")),
        ("monthly", _("Monthly")),
    ]

    frequency = models.CharField(
        max_length=20,
        choices=FREQUENCY_CHOICES,
        verbose_name=_("Frequency"),
    )
    min_income = models.FloatField(null=False, blank=False, verbose_name=_("Min. Income"))
    max_income = models.FloatField(null=True, blank=True, verbose_name=_("Max. Income"))
    base_tax = models.FloatField(default=0.0, verbose_name=_("Base Tax"))
    excess_rate = models.FloatField(
        default=0.0, verbose_name=_("Excess Rate (%)"),
        help_text=_("Percentage applied to the amount over Min. Income."),
    )

    objects = models.Manager()

    def __str__(self):
        return (
            f"{self.get_frequency_display()}: {self.min_income} - "
            f"{self.get_display_max_income()}"
        )

    def get_display_max_income(self):
        """
        Retrieves the maximum income.
        Returns:
            float or None: The maximum income if it is a finite value, otherwise None.
        """
        if self.max_income != math.inf:
            return self.max_income
        return None

    def clean(self):
        super().clean()

        existing_bracket = BIRWithholdingTax.objects.filter(
            frequency=self.frequency,
            min_income=self.min_income,
            max_income=self.max_income,
        ).exclude(pk=self.pk)
        if existing_bracket.exists():
            raise ValidationError(_("This BIR withholding tax bracket already exists"))

        if self.max_income is None:
            self.max_income = math.inf

        if self.min_income >= self.max_income:
            raise ValidationError(
                {"max_income": _("Maximum income must be greater than minimum income.")}
            )

        existing_brackets = BIRWithholdingTax.objects.filter(
            frequency=self.frequency
        ).exclude(pk=self.pk)
        if existing_brackets.filter(max_income__gte=self.min_income).exists():
            overlapping_bracket = existing_brackets.filter(
                max_income__gte=self.min_income
            ).first()
            if overlapping_bracket.min_income <= self.max_income:
                raise ValidationError(
                    {
                        "min_income": format_lazy(
                            "The Min. Income of this bracket must be \
                                greater than the Max. Income of {}.",
                            overlapping_bracket,
                        )
                    }
                )
