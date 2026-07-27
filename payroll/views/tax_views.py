"""
tax_views.py

This module contains view functions for handling payroll statutory
deduction/withholding-tax related operations (SSS, PhilHealth, Pag-IBIG,
BIR withholding tax).

"""

import math

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import hx_request_required, login_required, permission_required
from horilla.http.response import HorillaRedirect
from payroll.forms.tax_forms import (
    BIRWithholdingTaxForm,
    HolidayPaySettingsForm,
    PagibigSettingsForm,
    PerfectAttendanceBonusSettingsForm,
    PhilHealthSettingsForm,
    SSSContributionForm,
)
from payroll.models.tax_models import (
    BIRWithholdingTax,
    HolidayPaySettings,
    PagibigSettings,
    PerfectAttendanceBonusSettings,
    PhilHealthSettings,
    SSSContribution,
)


@login_required
@permission_required("payroll.view_ssscontribution")
def view_sss_contribution(request):
    """
    Display a list of all SSS contribution brackets.
    """
    sss_contributions = SSSContribution.objects.all().order_by("range_from")
    context = {"sss_contributions": sss_contributions}
    return render(request, "payroll/sss_contribution/sss_contribution_view.html", context)


@login_required
@hx_request_required
@permission_required("payroll.add_ssscontribution")
def create_sss_contribution(request):
    """
    Create an SSS contribution bracket based on user input.
    """
    sss_contribution_form = SSSContributionForm()
    context = {"form": sss_contribution_form}
    if request.method == "POST":
        sss_contribution_form = SSSContributionForm(request.POST)
        if sss_contribution_form.is_valid():
            range_to = sss_contribution_form.cleaned_data.get("range_to")
            if not range_to:
                messages.info(request, _("The range will be unbounded (and over)."))
                sss_contribution_form.instance.range_to = math.inf
            sss_contribution_form.save()
            messages.success(
                request, _("The SSS contribution bracket was created successfully.")
            )
            return redirect(create_sss_contribution)

        context["form"] = sss_contribution_form

    return render(
        request, "payroll/sss_contribution/sss_contribution_creation.html", context
    )


@login_required
@hx_request_required
@permission_required("payroll.change_ssscontribution")
def update_sss_contribution(request, pk):
    """
    Update an existing SSS contribution bracket based on user input.
    """
    sss_contribution = SSSContribution.find(pk)
    if sss_contribution:
        sss_contribution_form = SSSContributionForm(instance=sss_contribution)
        if request.method == "POST":
            sss_contribution_form = SSSContributionForm(
                request.POST, instance=sss_contribution
            )
            if sss_contribution_form.is_valid():
                range_to = sss_contribution_form.cleaned_data.get("range_to")
                if not range_to:
                    messages.info(
                        request, _("The range will be unbounded (and over).")
                    )
                    sss_contribution_form.instance.range_to = math.inf
                sss_contribution_form.save()
                messages.success(
                    request,
                    _("The SSS contribution bracket has been updated successfully."),
                )

        context = {"form": sss_contribution_form}
        return render(
            request, "payroll/sss_contribution/sss_contribution_edit.html", context
        )
    messages.error(request, _("SSS contribution bracket not found"))
    return HorillaRedirect(request)


@login_required
@hx_request_required
@permission_required("payroll.delete_ssscontribution")
def delete_sss_contribution(request, pk):
    """
    Delete an existing SSS contribution bracket.
    """
    sss_contribution = SSSContribution.find(pk)
    if sss_contribution:
        sss_contribution.delete()
        messages.success(request, _("SSS contribution bracket successfully deleted."))
    else:
        messages.error(request, _("SSS contribution bracket not found"))
    return redirect(view_sss_contribution)


@login_required
@permission_required("payroll.view_philhealthsettings")
def view_philhealth_settings(request):
    """
    Display and edit the singleton PhilHealth settings.
    """
    instance = PhilHealthSettings.objects.first()
    form = PhilHealthSettingsForm(instance=instance)
    if request.method == "POST":
        form = PhilHealthSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, _("PhilHealth settings updated successfully."))
            return HorillaRedirect(request)
    return render(
        request, "payroll/philhealth_settings/philhealth_settings_view.html", {"form": form}
    )


@login_required
@permission_required("payroll.view_pagibigsettings")
def view_pagibig_settings(request):
    """
    Display and edit the singleton Pag-IBIG settings.
    """
    instance = PagibigSettings.objects.first()
    form = PagibigSettingsForm(instance=instance)
    if request.method == "POST":
        form = PagibigSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, _("Pag-IBIG settings updated successfully."))
            return HorillaRedirect(request)
    return render(
        request, "payroll/pagibig_settings/pagibig_settings_view.html", {"form": form}
    )


@login_required
@permission_required("payroll.view_perfectattendancebonussettings")
def view_perfect_attendance_bonus_settings(request):
    """
    Display and edit the singleton Perfect Attendance Bonus settings.
    """
    instance = PerfectAttendanceBonusSettings.objects.first()
    form = PerfectAttendanceBonusSettingsForm(instance=instance)
    if request.method == "POST":
        form = PerfectAttendanceBonusSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(
                request, _("Perfect Attendance Bonus settings updated successfully.")
            )
            return HorillaRedirect(request)
    return render(
        request,
        "payroll/perfect_attendance_bonus_settings/perfect_attendance_bonus_settings_view.html",
        {"form": form},
    )


@login_required
@permission_required("payroll.view_holidaypaysettings")
def view_holiday_pay_settings(request):
    """
    Display and edit the singleton Holiday & Rest Day Pay settings.
    """
    instance = HolidayPaySettings.objects.first()
    form = HolidayPaySettingsForm(instance=instance)
    if request.method == "POST":
        form = HolidayPaySettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(
                request, _("Holiday & Rest Day Pay settings updated successfully.")
            )
            return HorillaRedirect(request)
    return render(
        request,
        "payroll/holiday_pay_settings/holiday_pay_settings_view.html",
        {"form": form},
    )


@login_required
@permission_required("payroll.view_birwithholdingtax")
def view_bir_withholding_tax(request):
    """
    Display a list of all BIR withholding tax brackets, grouped by frequency.
    """
    bir_brackets = BIRWithholdingTax.objects.all().order_by("frequency", "min_income")
    context = {"bir_brackets": bir_brackets}
    return render(
        request, "payroll/bir_withholding_tax/bir_withholding_tax_view.html", context
    )


@login_required
@hx_request_required
@permission_required("payroll.add_birwithholdingtax")
def create_bir_withholding_tax(request):
    """
    Create a BIR withholding tax bracket based on user input.
    """
    bir_form = BIRWithholdingTaxForm()
    context = {"form": bir_form}
    if request.method == "POST":
        bir_form = BIRWithholdingTaxForm(request.POST)
        if bir_form.is_valid():
            max_income = bir_form.cleaned_data.get("max_income")
            if not max_income:
                messages.info(request, _("The range will be unbounded (and over)."))
                bir_form.instance.max_income = math.inf
            bir_form.save()
            messages.success(
                request, _("The BIR withholding tax bracket was created successfully.")
            )
            return redirect(create_bir_withholding_tax)

        context["form"] = bir_form

    return render(
        request, "payroll/bir_withholding_tax/bir_withholding_tax_creation.html", context
    )


@login_required
@hx_request_required
@permission_required("payroll.change_birwithholdingtax")
def update_bir_withholding_tax(request, pk):
    """
    Update an existing BIR withholding tax bracket based on user input.
    """
    bir_bracket = BIRWithholdingTax.find(pk)
    if bir_bracket:
        bir_form = BIRWithholdingTaxForm(instance=bir_bracket)
        if request.method == "POST":
            bir_form = BIRWithholdingTaxForm(request.POST, instance=bir_bracket)
            if bir_form.is_valid():
                max_income = bir_form.cleaned_data.get("max_income")
                if not max_income:
                    messages.info(
                        request, _("The range will be unbounded (and over).")
                    )
                    bir_form.instance.max_income = math.inf
                bir_form.save()
                messages.success(
                    request,
                    _("The BIR withholding tax bracket has been updated successfully."),
                )

        context = {"form": bir_form}
        return render(
            request, "payroll/bir_withholding_tax/bir_withholding_tax_edit.html", context
        )
    messages.error(request, _("BIR withholding tax bracket not found"))
    return HorillaRedirect(request)


@login_required
@hx_request_required
@permission_required("payroll.delete_birwithholdingtax")
def delete_bir_withholding_tax(request, pk):
    """
    Delete an existing BIR withholding tax bracket.
    """
    bir_bracket = BIRWithholdingTax.find(pk)
    if bir_bracket:
        bir_bracket.delete()
        messages.success(
            request, _("BIR withholding tax bracket successfully deleted.")
        )
    else:
        messages.error(request, _("BIR withholding tax bracket not found"))
    return redirect(view_bir_withholding_tax)
