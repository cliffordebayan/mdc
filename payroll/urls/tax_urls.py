"""
tax_urls.py

This module is used to bind url patterns with django views related to
payroll statutory deductions/withholding tax.
"""

from django.urls import path

from payroll.views import tax_views

urlpatterns = [
    path(
        "sss-contribution-view/",
        tax_views.view_sss_contribution,
        name="sss-contribution-view",
    ),
    path(
        "sss-contribution-create/",
        tax_views.create_sss_contribution,
        name="sss-contribution-create",
    ),
    path(
        "sss-contribution-update/<int:pk>/",
        tax_views.update_sss_contribution,
        name="sss-contribution-update",
    ),
    path(
        "sss-contribution-delete/<int:pk>/",
        tax_views.delete_sss_contribution,
        name="sss-contribution-delete",
    ),
    path(
        "philhealth-settings-view/",
        tax_views.view_philhealth_settings,
        name="philhealth-settings-view",
    ),
    path(
        "pagibig-settings-view/",
        tax_views.view_pagibig_settings,
        name="pagibig-settings-view",
    ),
    path(
        "perfect-attendance-bonus-settings-view/",
        tax_views.view_perfect_attendance_bonus_settings,
        name="perfect-attendance-bonus-settings-view",
    ),
    path(
        "bir-withholding-tax-view/",
        tax_views.view_bir_withholding_tax,
        name="bir-withholding-tax-view",
    ),
    path(
        "bir-withholding-tax-create/",
        tax_views.create_bir_withholding_tax,
        name="bir-withholding-tax-create",
    ),
    path(
        "bir-withholding-tax-update/<int:pk>/",
        tax_views.update_bir_withholding_tax,
        name="bir-withholding-tax-update",
    ),
    path(
        "bir-withholding-tax-delete/<int:pk>/",
        tax_views.delete_bir_withholding_tax,
        name="bir-withholding-tax-delete",
    ),
]
