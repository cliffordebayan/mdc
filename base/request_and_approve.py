"""
views.py

This module is used to map url patterns with request and approve methods in Dashboard.
"""

import json

from django.apps import apps
from django.shortcuts import render

from base.methods import filtersubordinates, paginator_qry
from base.models import ShiftRequest, WorkTypeRequest
from horilla.decorators import login_required


def _page_ids(page_or_queryset):
    object_list = getattr(page_or_queryset, "object_list", page_or_queryset)
    return [instance.id for instance in object_list]


@login_required
def dashboard_shift_request(request):
    page_number = request.GET.get("page")
    previous_data = request.GET.urlencode()
    requests = ShiftRequest.objects.select_related(
        "employee_id",
        "employee_id__employee_work_info",
        "shift_id",
        "previous_shift_id",
    ).filter(
        approved=False, canceled=False, employee_id__is_active=True
    )
    requests = filtersubordinates(request, requests, "base.add_shiftrequest")
    requests = paginator_qry(requests, page_number)
    requests_ids = json.dumps(_page_ids(requests))
    return render(
        request,
        "request_and_approve/shift_request.html",
        {
            "requests": requests,
            "requests_ids": requests_ids,
            "pd": previous_data,
        },
    )


@login_required
def dashboard_work_type_request(request):
    page_number = request.GET.get("page")
    previous_data = request.GET.urlencode()
    requests = WorkTypeRequest.objects.select_related(
        "employee_id",
        "employee_id__employee_work_info",
        "work_type_id",
        "previous_work_type_id",
    ).filter(
        approved=False, canceled=False, employee_id__is_active=True
    )
    requests = filtersubordinates(request, requests, "base.add_worktyperequest")
    requests = paginator_qry(requests, page_number)
    requests_ids = json.dumps(_page_ids(requests))
    return render(
        request,
        "request_and_approve/work_type_request.html",
        {
            "requests": requests,
            "requests_ids": requests_ids,
            "pd": previous_data,
        },
    )
