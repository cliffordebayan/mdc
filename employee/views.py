"""
views.py

This module contains the view functions for handling HTTP requests and rendering
responses in your application.

Each view function corresponds to a specific URL route and performs the necessary
actions to handle the request, process data, and generate a response.

This module is part of the recruitment project and is intended to
provide the main entry points for interacting with the application's functionality.
"""

import ast
import calendar
import json
import operator
import os
import secrets
import threading
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pandas as pd
from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.db.models import Count, F, ProtectedError
from django.db.models.query import QuerySet
from django.forms import DateInput, Select
from django.core.mail import EmailMessage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext as __
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accessibility.decorators import enter_if_accessible
from accessibility.methods import update_employee_accessibility_cache
from accessibility.middlewares import ACCESSIBILITY_CACHE_USER_KEYS
from accessibility.models import DefaultAccessibility
from base.backends import ConfiguredEmailBackend
from base.forms import ModelForm
from base.methods import (
    choosesubordinates,
    filtersubordinates,
    filtersubordinatesemployeemodel,
    get_hq_company_logo_url,
    get_key_instances,
    get_pagination,
    sortby,
)
from base.models import (
    Branch,
    BusinessUnit,
    Company,
    CostCenter,
    Department,
    EmailLog,
    EmployeeShift,
    EmployeeType,
    JobPosition,
    JobRole,
    PayrollGroup,
    RotatingShiftAssign,
    RotatingWorkTypeAssign,
    ShiftRequest,
    WorkType,
    WorkTypeRequest,
)
from base.views import generate_error_report
from employee.filters import DocumentRequestFilter, EmployeeFilter, EmployeeReGroup
from employee.forms import (
    BankForm,
    BonusPointAddForm,
    BonusPointRedeemForm,
    BulkUpdateFieldForm,
    EmployeeBankDetailsForm,
    EmployeeBankDetailsUpdateForm,
    EmployeeExportExcelForm,
    EmployeeForm,
    EmployeeGeneralSettingPrefixForm,
    EmployeeInsuranceForm,
    EmployeeNoteForm,
    EmployeePortalPersonalForm,
    EmployeePortalPINForm,
    EmployeeTagForm,
    EmployeeWorkInformationForm,
    EmployeeWorkInformationUpdateForm,
    excel_columns,
)
from employee.methods.methods import (
    bulk_create_bank_details_import,
    bulk_create_department_import,
    bulk_create_employee_import,
    bulk_create_employee_types,
    bulk_create_insurance_import,
    bulk_create_job_position_import,
    bulk_create_job_role_import,
    bulk_create_shifts,
    bulk_create_user_import,
    bulk_create_work_info_import,
    bulk_create_work_types,
    bulk_set_tags_import,
    get_error_data_template,
    get_import_bank_names,
    get_import_insurance_names,
    get_ordered_employee_nos,
    process_employee_records,
    set_initial_password,
    valid_import_file_headers,
)
from employee.models import (
    Bank,
    BonusPoint,
    Employee,
    EmployeeBankDetails,
    EmployeeGeneralSetting,
    EmployeeInsurance,
    EmployeeNote,
    EmployeeOnboardingPortal,
    EmployeeTag,
    EmployeeWorkInformation,
    NoteFiles,
)
from horilla.decorators import (
    hx_request_required,
    logger,
    login_required,
    manager_can_enter,
    owner_can_enter,
    permission_required,
)
from horilla.filters import HorillaPaginator
from horilla.group_by import group_by_queryset
from horilla.horilla_settings import HORILLA_DATE_FORMATS
from horilla.http import HorillaRedirect
from horilla.methods import get_horilla_model_class
from horilla_audit.models import AccountBlockUnblock, HistoryTrackingFields
from horilla_documents.forms import (
    DocumentForm,
    DocumentRejectForm,
    DocumentRequestForm,
    DocumentUpdateForm,
    EmployeeDocumentFulfillForm,
    EmployeeDocumentRejectForm,
    EmployeeDocumentRequestForm,
)
from horilla_documents.models import Document, DocumentRequest, EmployeeDocumentRequest
from notifications.signals import notify


def return_none(a, b):
    return None


operator_mapping = {
    "equal": operator.eq,
    "notequal": operator.ne,
    "lt": operator.lt,
    "gt": operator.gt,
    "le": operator.le,
    "ge": operator.ge,
    "icontains": operator.contains,
    "range": return_none,
}
filter_mapping = {
    "work_type_id": {
        "filter": lambda employee, allowance: {
            "employee_id": employee,
            "work_type_id__id": allowance.work_type_id.id,
            "attendance_validated": True,
        }
    },
    "shift_id": {
        "filter": lambda employee, allowance,: {
            "employee_id": employee,
            "shift_id__id": allowance.shift_id.id,
            "attendance_validated": True,
        }
    },
    "overtime": {
        "filter": lambda employee, allowance: {
            "employee_id": employee,
            "attendance_overtime_approve": True,
            "attendance_validated": True,
        }
    },
    "attendance": {
        "filter": lambda employee, allowance: {
            "employee_id": employee,
            "attendance_validated": True,
        }
    },
}


BLOCKED_EXTENSIONS = {
    ".html",
    ".htm",
    ".js",
    ".svg",
    ".xml",
    ".php",
    ".py",
    ".sh",
    ".exe",
}


EMPLOYEE_LIST_SELECT_RELATED_FIELDS = (
    "employee_user_id",
    "employee_work_info",
    "employee_work_info__job_position_id",
    "employee_work_info__department_id",
    "employee_work_info__shift_id",
    "employee_work_info__work_type_id",
    "employee_work_info__job_role_id",
    "employee_work_info__reporting_manager_id",
    "employee_work_info__company_id",
)


def employee_list_queryset():
    """
    Shared queryset for employee list/card pages to avoid N+1 lookups.
    """
    return Employee.objects.select_related(*EMPLOYEE_LIST_SELECT_RELATED_FIELDS)


def _check_reporting_manager(request, *args, **kwargs):
    if kwargs.get("obj_id"):
        obj_id = kwargs["obj_id"]
        emp = Employee.objects.get(id=obj_id)
        re_manager = None
        if emp.employee_work_info.reporting_manager_id != None:
            re_manager = emp.employee_work_info.reporting_manager_id
        employee = request.user.employee_get
        if re_manager != None:
            return re_manager == employee
        else:
            return False
    return request.user.employee_get.reporting_manager.exists()


@login_required
def get_language_code(request):
    """
    Retrieve the language code for the current request.

    This view function extracts the LANGUAGE_CODE from the request object and
    returns it as a JSON response. This function requires the user to be logged in.
    """
    language_code = request.LANGUAGE_CODE
    return JsonResponse({"language_code": language_code})


@login_required
def employee_profile(request):
    """
    This method is used to view own profile of employee.
    """
    employee = request.user.employee_get
    selected_company = request.session.get("selected_company")
    if selected_company != "all":
        company_id = getattr(
            getattr(getattr(employee, "employee_work_info", None), "company_id", None),
            "id",
            None,
        )

        if str(company_id) != str(selected_company):
            messages.error(request, "Employee is not working in the selected company.")
            return redirect("employee-view")

    today = datetime.today()
    now = timezone.now()
    return render(
        request,
        "employee/profile/profile_view.html",
        {
            "employee": employee,
            "current_date": today,
            "now": now,
        },
    )


@login_required
@enter_if_accessible(
    feature="profile_edit",
    perm="employee.change_employee",
)
def self_info_update(request):
    """
    This method is used to update own profile of an employee.
    """
    user = request.user
    employee = Employee.objects.filter(employee_user_id=user).first()
    employee_no = employee.employee_no
    existing_bank = EmployeeBankDetails.objects.filter(employee_id=employee).first()
    bank_form = EmployeeBankDetailsForm(instance=existing_bank)
    form = EmployeeForm(instance=Employee.objects.filter(employee_user_id=user).first())
    if request.POST:
        if request.POST.get("employee_first_name") is not None:
            instance = Employee.objects.filter(employee_user_id=request.user).first()
            form = EmployeeForm(request.POST, instance=instance)
            if form.is_valid():
                instance = form.save(commit=False)
                instance.employee_user_id = user
                if instance.employee_no is None:
                    instance.employee_no = employee_no
                instance.save()
                messages.success(request, _("Profile updated."))
        elif request.POST.get("any_other_code1") is not None:
            existing_bank = EmployeeBankDetails.objects.filter(employee_id=employee).first()
            bank_form = EmployeeBankDetailsForm(request.POST, instance=existing_bank)
            if bank_form.is_valid():
                instance = bank_form.save(commit=False)
                instance.employee_id = employee
                instance.is_primary = True
                instance.save()
                messages.success(request, _("Bank details saved."))
                existing_bank = instance
                bank_form = EmployeeBankDetailsForm(instance=existing_bank)
    bank_account = EmployeeBankDetails.objects.filter(employee_id=employee).first()
    insurance_form = EmployeeInsuranceForm()
    insurance_accounts = EmployeeInsurance.objects.filter(employee_id=employee)
    return render(
        request,
        "employee/profile/profile.html",
        {
            "form": form,
            "bank_form": bank_form,
            "bank_account": bank_account,
            "insurance_form": insurance_form,
            "insurance_accounts": insurance_accounts,
        },
    )


def profile_edit_access(request, emp_id):
    feature = request.GET.get("feature", None)
    accessibility = DefaultAccessibility.objects.filter(feature=feature).first()
    if accessibility:
        employees = Employee.objects.filter(id=emp_id)

        if employee := employees.first():
            if employee in accessibility.employees.all():
                accessibility.employees.remove(employee)
            else:
                accessibility.employees.add(employee)

            user_cache_key = ACCESSIBILITY_CACHE_USER_KEYS.get(
                employees.first().employee_user_id.id, None
            )
            if user_cache_key:
                cache.delete(user_cache_key[-1])
                update_employee_accessibility_cache(user_cache_key[-1], employee)

    return HorillaRedirect(request)


@login_required
@enter_if_accessible(
    feature="employee_detailed_view",
    perm="employee.view_employee",
    method=_check_reporting_manager,
)
def employee_view_individual(request, obj_id, **kwargs):
    """
    This method is used to view profile of an employee.
    """
    try:
        employee = Employee.objects.get(id=obj_id)
    except ObjectDoesNotExist:
        try:
            employee = Employee.objects.entire().get(id=obj_id)
            company = getattr(
                getattr(employee, "employee_work_info", None), "company_id", None
            )
            company_id = getattr(company, "pk", None)
            if company_id != request.session["selected_company"]:
                messages.error(
                    request, "Employee is not working in the selected company."
                )
                return redirect("employee-view")
        except Exception as e:
            return render(request, "404.html", status=404)

    employee_leaves = (
        employee.available_leave.all() if apps.is_installed("leave") else None
    )
    enabled_block_unblock = (
        AccountBlockUnblock.objects.exists()
        and AccountBlockUnblock.objects.first().is_enabled
    )
    # Retrieve the filtered employees from the session
    filtered_employee_ids = request.session.get("filtered_employees", [])
    filtered_employees = Employee.objects.filter(id__in=filtered_employee_ids)

    request_ids_str = json.dumps(
        [
            instance.id
            for instance in paginator_qry(
                filtered_employees, request.GET.get("page")
            ).object_list
        ]
    )

    # Convert the string to an actual list of integers
    requests_ids = (
        ast.literal_eval(request_ids_str)
        if isinstance(request_ids_str, str)
        else request_ids_str
    )

    employee_id = employee.id
    previous_id = None
    next_id = None

    for index, req_id in enumerate(requests_ids):
        if req_id == employee_id:

            if index == len(requests_ids) - 1:
                next_id = None
            else:
                next_id = requests_ids[index + 1]
            if index == 0:
                previous_id = None
            else:
                previous_id = requests_ids[index - 1]
            break

    context = {
        "employee": employee,
        "previous": previous_id,
        "next": next_id,
        "requests_ids": requests_ids,
        "current_date": date.today(),
        "leave_request_ids": json.dumps([]),
        "enabled_block_unblock": enabled_block_unblock,
    }
    # if the requesting user opens own data
    if request.user.employee_get == employee:
        context["user_leaves"] = employee_leaves
    else:
        context["employee_leaves"] = employee_leaves

    return render(
        request,
        "employee/view/individual.html",
        context,
    )


@login_required
@hx_request_required
def about_tab(request, obj_id, **kwargs):
    """
    This method is used to view profile of an employee.
    """
    employee = Employee.objects.get(id=obj_id)
    contracts = employee.contract_set.all() if apps.is_installed("payroll") else None
    employee_leaves = (
        employee.available_leave.all() if apps.is_installed("leave") else None
    )
    return render(
        request,
        "tabs/personal_tab.html",
        {
            "employee": employee,
            "employee_leaves": employee_leaves,
            "contracts": contracts,
        },
    )


@login_required
@hx_request_required
@owner_can_enter("perms.employee.view_employee", Employee)
def shift_tab(request, emp_id):
    """
    This function is used to view shift tab of an employee in employee individual & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return shift-tab template
    """
    employee = Employee.objects.get(id=emp_id)
    work_type_requests = WorkTypeRequest.objects.filter(employee_id=emp_id)
    work_type_requests_ids = json.dumps(
        [instance.id for instance in work_type_requests]
    )
    rshift_assign = RotatingShiftAssign.objects.filter(employee_id=emp_id)
    rshift_assign_ids = json.dumps([instance.id for instance in rshift_assign])
    rwork_type_assign = RotatingWorkTypeAssign.objects.filter(employee_id=emp_id)
    rwork_type_assign_ids = json.dumps([instance.id for instance in rwork_type_assign])
    shift_requests = ShiftRequest.objects.filter(employee_id=emp_id)
    shift_requests_ids = json.dumps([instance.id for instance in shift_requests])

    context = {
        "work_data": work_type_requests,
        "work_type_requests_ids": work_type_requests_ids,
        "rshift_assign": rshift_assign,
        "rshift_assign_ids": rshift_assign_ids,
        "rwork_type_assign": rwork_type_assign,
        "rwork_type_assign_ids": rwork_type_assign_ids,
        "shift_data": shift_requests,
        "shift_requests_ids": shift_requests_ids,
        "emp_id": emp_id,
        "employee": employee,
    }
    return render(request, "tabs/shift-tab.html", context=context)


@login_required
def document_request_view(request):
    """
    This function is used to view and filter document requests of employees.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns:
    Render 'documents/document_requests.html' with documents and filters.
    """
    previous_data = request.GET.urlencode()
    filter_class = DocumentRequestFilter(request.GET or None)
    document_requests = DocumentRequest.objects.all()
    documents = Document.objects.filter(document_request_id__isnull=False)
    documents = filtersubordinates(
        request=request,
        perm="horilla_documents.view_documentrequest",
        queryset=documents,
    )

    if request.GET:
        filtered_docs = filter_class.qs
        filtered_docs = filtered_docs.filter(document_request_id__isnull=False)
        documents = filtered_docs

    documents = group_by_queryset(
        documents, "document_request_id", request.GET.get("page"), "page"
    )
    data_dict = parse_qs(previous_data)
    get_key_instances(Document, data_dict)

    try:
        emp_requests_qs = EmployeeDocumentRequest.objects.all().order_by("-created_at")
        emp_requests_qs = filtersubordinates(
            request=request,
            perm="horilla_documents.view_employeedocumentrequest",
            queryset=emp_requests_qs,
        )
        emp_page = request.GET.get("emp_page", 1)
        emp_requests_qs = HorillaPaginator(emp_requests_qs, get_pagination()).page(
            emp_page if str(emp_page).isdigit() else 1
        )
    except Exception as exc:
        logger.error("employee document requests fetch failed: %s", exc)
        emp_requests_qs = []

    context = {
        "document_requests": document_requests,
        "documents": documents,
        "f": filter_class,
        "pd": previous_data,
        "filter_dict": data_dict,
        "requests": emp_requests_qs,
    }
    return render(request, "documents/document_requests.html", context=context)


@login_required
@hx_request_required
def document_filter_view(request):
    """
    This method is used to filter employee.
    """
    document_requests = DocumentRequest.objects.all()
    previous_data = request.GET.urlencode()
    documents = DocumentRequestFilter(request.GET).qs
    documents = documents.exclude(document_request_id__isnull=True).order_by(
        "-document_request_id"
    )
    documents = group_by_queryset(
        documents, "document_request_id", request.GET.get("page"), "page"
    )
    # documents = paginator_qry(documents,request.GET.get("page"))
    data_dict = parse_qs(previous_data)
    get_key_instances(Document, data_dict)

    return render(
        request,
        "documents/requests.html",
        {
            "documents": documents,
            "f": EmployeeFilter(request.GET),
            "pd": previous_data,
            "filter_dict": data_dict,
            "document_requests": document_requests,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("horilla_documents.add_documentrequest")
def document_request_create(request):
    """
    This function is used to create document requests of an employee in employee requests view.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns: return document_request_create_form template
    """
    form = DocumentRequestForm()
    form = choosesubordinates(request, form, "horilla_documents.add_documentrequest")
    if request.method == "POST":
        form = DocumentRequestForm(request.POST)
        form = choosesubordinates(
            request, form, "horilla_documents.add_documentrequest"
        )
        if form.is_valid():
            form = form.save()
            messages.success(request, _("Document request created successfully"))
            employees = [user.employee_user_id for user in form.employee_id.all()]

            notify.send(
                request.user.employee_get,
                recipient=employees,
                verb=f"{request.user.employee_get} requested a document.",
                verb_ar=f"طلب {request.user.employee_get} مستنداً.",
                verb_de=f"{request.user.employee_get} hat ein Dokument angefordert.",
                verb_es=f"{request.user.employee_get} solicitó un documento.",
                verb_fr=f"{request.user.employee_get} a demandé un document.",
                redirect=reverse("employee-profile"),
                icon="chatbox-ellipses",
            )
            return HorillaRedirect(request)

    context = {
        "form": form,
    }
    return render(
        request, "documents/document_request_create_form.html", context=context
    )


@login_required
@hx_request_required
@manager_can_enter("horilla_documents.change_documentrequest")
def document_request_update(request, id):
    """
    This function is used to update document requests of an employee in employee requests view.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns: return document_request_create_form template
    """
    document_request = get_object_or_404(DocumentRequest, id=id)
    documents = Document.objects.filter(document_request_id=document_request.id)
    form = DocumentRequestForm(instance=document_request)
    if request.method == "POST":
        form = DocumentRequestForm(request.POST, instance=document_request)
        if form.is_valid():
            doc_obj = form.save()
            doc_obj.employee_id.set(
                Employee.objects.filter(id__in=form.data.getlist("employee_id"))
            )
            documents.exclude(employee_id__in=doc_obj.employee_id.all()).delete()
            return HorillaRedirect(request)

    context = {
        "form": form,
        "document_request": document_request,
    }
    return render(
        request, "documents/document_request_create_form.html", context=context
    )


@login_required
@hx_request_required
@owner_can_enter("horilla_documents.view_document", Employee)
def document_tab(request, emp_id):
    """
    This function is used to view documents tab of an employee in employee individual
    & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return document_tab template
    """

    form = DocumentUpdateForm(request.POST, request.FILES)
    documents = Document.objects.filter(employee_id=emp_id, document_request_id__isnull=True)
    admin_doc_requests = Document.objects.filter(employee_id=emp_id, document_request_id__isnull=False)
    employee_doc_requests = EmployeeDocumentRequest.objects.filter(employee_id=emp_id)
    admin_doc_requests_pending = admin_doc_requests.filter(status="requested").count()

    context = {
        "documents": documents,
        "admin_doc_requests": admin_doc_requests,
        "admin_doc_requests_pending": admin_doc_requests_pending,
        "form": form,
        "emp_id": emp_id,
        "employee_doc_requests": employee_doc_requests,
    }
    return render(request, "tabs/document_tab.html", context=context)


@login_required
@hx_request_required
@owner_can_enter("horilla_documents.add_document", Employee)
def document_create(request, emp_id):
    """
    This function is used to create documents from employee individual & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee

    Returns: return document_tab template
    """
    employee_id = Employee.objects.get(id=emp_id)
    form = DocumentForm(initial={"employee_id": employee_id, "expiry_date": None})
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, _("Document created successfully."))
            return HorillaRedirect(request)

    context = {
        "form": form,
        "emp_id": emp_id,
    }
    return render(request, "tabs/htmx/document_create_form.html", context=context)


@login_required
def update_document_title(request, id):
    """
    This function is used to create documents from employee individual & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.

    Returns: return document_tab template
    """
    document = get_object_or_404(Document, id=id)
    name = request.POST.get("title")
    if request.method == "POST":
        document.title = name
        document.save()
        messages.success(request, _("Document title updated successfully"))
    else:
        messages.error(request, _("Invalid request"))
    return HttpResponse("")


@login_required
@hx_request_required
def document_delete(request, id):
    """
    Handle the deletion of a document, with permissions and error handling.

    This view function attempts to delete a document specified by its ID.
    If the user does not have the "delete_document" permission, it restricts
    deletion to documents owned by the user. It provides appropriate success
    or error messages based on the outcome. If the document is protected and
    cannot be deleted, it handles the exception and informs the user.
    """
    try:
        document = Document.objects.filter(id=id)
        if not request.user.has_perm("horilla_documents.delete_document"):
            document = document.filter(
                employee_id__employee_user_id=request.user
            ).exclude(document_request_id__isnull=False)
        if document:
            document_first = document.first()
            document.delete()
            messages.success(
                request,
                _("Document request %(doc)s for %(employee)s deleted successfully")
                % {
                    "doc": document_first,
                    "employee": document_first.employee_id,
                },
            )
            referrer = request.META.get("HTTP_REFERER", "")
            path = urlparse(referrer).path or ""

            if path.startswith("/employee/employee-view/") or path.endswith(
                "/employee/employee-profile/"
            ):
                existing_documents = Document.objects.filter(
                    employee_id=document_first.employee_id
                )
                if not existing_documents:
                    url = reverse(
                        "employee-document-tab",
                        kwargs={"employee_id": document_first.employee_id.id},
                    )

                    html = format_html(
                        "<span hx-get='{}?employee_view=true' "
                        "hx-target='#document_target' "
                        "hx-trigger='load'></span>",
                        url,
                    )
                    return HttpResponse(html)

            return HttpResponse("<script>$('#reloadMessagesButton').click();</script>")
        else:
            messages.error(request, _("Document not found"))
    except ProtectedError:
        messages.error(request, _("You cannot delete this document."))
    return HorillaRedirect(request)


def can_access_document(request, document, perm):
    """
    Check if the current user is authorized to access the given document.
    """
    employee = request.user.employee_get
    return (
        document.employee_id == employee
        or document.employee_id.get_reporting_manager() == employee
        or request.user.has_perm(perm)
    )


@login_required
@hx_request_required
def file_upload(request, id):
    """
    This function is used to upload documents of an employee in employee individual & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns: return document_form template
    """

    document_item = Document.find(id)
    if document_item is None:
        return HorillaRedirect(
            request, message=_("No Document found matching the query.")
        )

    if not can_access_document(
        request, document_item, "horilla_documents.change_document"
    ):
        return HorillaRedirect(
            request, message=_("You do not have permission to update this document.")
        )

    form = DocumentUpdateForm(instance=document_item)
    if request.method == "POST":
        form = DocumentUpdateForm(request.POST, request.FILES, instance=document_item)
        if form.is_valid():
            form.save()
            messages.success(request, _("Document uploaded successfully"))
            try:
                notify.send(
                    request.user.employee_get,
                    recipient=request.user.employee_get.get_reporting_manager().employee_user_id,
                    verb=f"{request.user.employee_get} uploaded a document",
                    verb_ar=f"قام {request.user.employee_get} بتحميل مستند",
                    verb_de=f"{request.user.employee_get} hat ein Dokument hochgeladen",
                    verb_es=f"{request.user.employee_get} subió un documento",
                    verb_fr=f"{request.user.employee_get} a téléchargé un document",
                    redirect=reverse(
                        "employee-view-individual",
                        kwargs={"obj_id": request.user.employee_get.id},
                    ),
                    icon="chatbox-ellipses",
                )
            except:
                pass
            return HorillaRedirect(request)
        else:
            logger.error(f"Document upload form errors: {form.errors}")
    context = {"form": form, "document": document_item}
    return render(request, "tabs/htmx/document_form.html", context=context)


@login_required
@hx_request_required
def view_file(request, id):
    """
    This function used to view the uploaded document in the modal.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns: return view_file template
    """

    document_obj = Document.objects.filter(id=id).first()
    if document_obj is None:
        return HorillaRedirect(
            request, message=_("No Document found matching the query.")
        )

    if not can_access_document(
        request, document_obj, "horilla_documents.view_document"
    ):
        return HorillaRedirect(
            request, message=_("You do not have permission to view this document.")
        )

    context = {
        "document": document_obj,
    }
    if document_obj.document:
        file_path = document_obj.document.path
        file_extension = os.path.splitext(file_path)[1][
            1:
        ].lower()  # Get the lowercase file extension

        content_type = get_content_type(file_extension)

        try:
            with open(file_path, "rb") as file:
                file_content = file.read()  # Decode the binary content for display
        except:
            file_content = None

        context["file_content"] = file_content
        context["file_extension"] = file_extension
        context["content_type"] = content_type

    return render(request, "tabs/htmx/view_file.html", context)


def get_content_type(file_extension):
    """
    This function retuns the content type of a file
    parameters:

    file_extension: The file extension of the file
    """

    content_types = {
        "pdf": "application/pdf",
        "txt": "text/plain",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "jpg": "image/jpeg",
        "png": "image/png",
        "jpeg": "image/jpeg",
    }

    # Default to application/octet-stream if the file extension is not recognized
    return content_types.get(file_extension, "application/octet-stream")


@login_required
@hx_request_required
@manager_can_enter("horilla_documents.add_document")
def document_approve(request, id):
    """
    This function used to view the approve uploaded document.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns:
    """

    document_obj = get_object_or_404(Document, id=id)
    refresh_url = request.GET.get("refresh_url") or request.POST.get("refresh_url")
    hx_target = request.GET.get("hx_target") or request.POST.get("hx_target")
    hx_select = request.GET.get("hx_select") or request.POST.get("hx_select")
    hx_swap = request.GET.get("hx_swap") or request.POST.get("hx_swap")
    if document_obj.document:
        document_obj.status = "approved"
        document_obj.save()
        messages.success(request, _("Document request approved"))
    else:
        messages.error(request, _("No document uploaded"))
    # 918
    if refresh_url:
        attrs = []
        if hx_target:
            attrs.append(f'hx-target="{hx_target}"')
        if hx_select:
            attrs.append(f'hx-select="{hx_select}"')
        if hx_swap:
            attrs.append(f'hx-swap="{hx_swap}"')

        span = f"""
            <span
                hx-trigger="load"
                hx-get="{refresh_url}"
                {' '.join(attrs)}
                >
            </span>
        """
        return HttpResponse(span)

    return HorillaRedirect(request)


@login_required
@hx_request_required
@manager_can_enter("horilla_documents.add_document")
def document_reject(request, id):
    """
    This function used to view the reject uploaded document.
    Parameters:

    request (HttpRequest): The HTTP request object.
    id (int): The id of the document.

    Returns:
    """
    document_obj = get_object_or_404(Document, id=id)
    form = DocumentRejectForm()
    if document_obj.document:
        if request.method == "POST":
            form = DocumentRejectForm(request.POST, instance=document_obj)
            if form.is_valid():
                test = form.save()
                document_obj.status = "rejected"
                document_obj.save()
                messages.error(request, _("Document request rejected"))

                return HorillaRedirect(request)
    else:
        messages.error(request, _("No document uploaded"))
        return HorillaRedirect(request)

    return render(
        request,
        "tabs/htmx/reject_form.html",
        {"form": form, "document_obj": document_obj},
    )


@login_required
@manager_can_enter("horilla_documents.add_document")
def document_bulk_approve(request):
    """
    This function is used to bulk-approve uploaded documents.

    Parameters:
        request (HttpRequest): The HTTP request object.

    Returns:
        HttpResponse: A 204 No Content response with HX-Refresh header.
    """
    if request.method == "POST":
        ids = request.POST.getlist("ids")

        # Documents with uploaded files
        approved_docs = Document.objects.filter(id__in=ids).exclude(document="")
        count_approved = approved_docs.update(status="approved")

        # Documents without uploaded files
        not_uploaded_count = len(ids) - approved_docs.count()

        if count_approved:
            messages.success(
                request, _(f"{count_approved} document request(s) approved")
            )

        if not_uploaded_count:
            messages.info(
                request, _(f"{not_uploaded_count} document(s) skipped (not uploaded)")
            )

    return HorillaRedirect(request)


@login_required
@manager_can_enter("horilla_documents.add_document")
def document_bulk_reject(request):
    """
    Handle bulk rejection of documents.

    On GET request, display a form to enter the rejection reason for selected documents.
    On POST request, validate the rejection reason and update the status of documents
    (excluding those already rejected) to 'rejected' with the provided reason.
    """
    ids = (
        request.POST.getlist("ids")
        if request.method == "POST"
        else request.GET.getlist("ids")
    )
    form = DocumentRejectForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        reject_reason = form.cleaned_data["reject_reason"]
        updated_count = (
            Document.objects.filter(id__in=ids)
            .exclude(status="rejected")
            .update(status="rejected", reject_reason=reject_reason)
        )
        messages.success(
            request, _("{} Document request rejected").format(updated_count)
        )
        return HorillaRedirect(request)

    return render(
        request, "documents/document_reject_reason.html", {"ids": ids, "form": form}
    )


@login_required
def employee_document_request_view(request):
    previous_data = request.GET.urlencode()
    requests_qs = EmployeeDocumentRequest.objects.all().order_by("-created_at")
    requests_qs = filtersubordinates(
        request=request,
        perm="horilla_documents.view_employeedocumentrequest",
        queryset=requests_qs,
    )
    page = request.GET.get("page", 1)
    requests_qs = HorillaPaginator(requests_qs, get_pagination()).page(
        page if str(page).isdigit() else 1
    )
    data_dict = parse_qs(previous_data)
    context = {
        "requests": requests_qs,
        "pd": previous_data,
        "filter_dict": data_dict,
    }
    return render(
        request, "documents/employee_document_requests.html", context=context
    )


@login_required
@hx_request_required
def employee_document_request_filter_view(request):
    previous_data = request.GET.urlencode()
    requests_qs = EmployeeDocumentRequest.objects.all().order_by("-created_at")
    requests_qs = filtersubordinates(
        request=request,
        perm="horilla_documents.view_employeedocumentrequest",
        queryset=requests_qs,
    )
    if request.GET.get("search"):
        requests_qs = requests_qs.filter(
            title__icontains=request.GET.get("search")
        )
    if request.GET.get("status"):
        requests_qs = requests_qs.filter(status=request.GET.get("status"))
    page = request.GET.get("page", 1)
    requests_qs = HorillaPaginator(requests_qs, get_pagination()).page(
        page if str(page).isdigit() else 1
    )
    data_dict = parse_qs(previous_data)
    context = {
        "requests": requests_qs,
        "pd": previous_data,
        "filter_dict": data_dict,
    }
    return render(
        request,
        "documents/htmx/employee_document_requests_list.html",
        context=context,
    )


@login_required
@hx_request_required
def employee_document_request_create(request):
    employee = request.user.employee_get
    form = EmployeeDocumentRequestForm()
    if request.method == "POST":
        form = EmployeeDocumentRequestForm(request.POST, request.FILES)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.employee_id = employee
            instance.save()
            messages.success(request, _("Document request submitted successfully."))
            admins = User.objects.filter(is_superuser=True)
            notify.send(
                employee,
                recipient=list(admins),
                verb=f"{employee} submitted a document request: {instance.title}",
                redirect=reverse("employee-document-request-view"),
                icon="chatbox-ellipses",
            )
            return HorillaRedirect(request)
    context = {"form": form}
    return render(
        request, "tabs/htmx/employee_document_request_form.html", context=context
    )


@login_required
@hx_request_required
@manager_can_enter("horilla_documents.change_employeedocumentrequest")
def employee_document_request_fulfill(request, id):
    doc_request = get_object_or_404(EmployeeDocumentRequest, id=id)
    form = EmployeeDocumentFulfillForm(instance=doc_request)
    if request.method == "POST":
        form = EmployeeDocumentFulfillForm(
            request.POST, request.FILES, instance=doc_request
        )
        if form.is_valid():
            instance = form.save(commit=False)
            instance.status = "fulfilled"
            instance.reject_reason = None
            instance.save()
            messages.success(request, _("Document request fulfilled successfully."))
            notify.send(
                request.user.employee_get,
                recipient=doc_request.employee_id.employee_user_id,
                verb=f"Your document request '{doc_request.title}' has been fulfilled.",
                redirect=reverse("employee-profile"),
                icon="chatbox-ellipses",
            )
            return HorillaRedirect(request)
    context = {"form": form, "doc_request": doc_request}
    return render(
        request,
        "documents/htmx/employee_document_fulfill_form.html",
        context=context,
    )


@login_required
@hx_request_required
@manager_can_enter("horilla_documents.change_employeedocumentrequest")
def employee_document_request_reject(request, id):
    doc_request = get_object_or_404(EmployeeDocumentRequest, id=id)
    form = EmployeeDocumentRejectForm(instance=doc_request)
    if request.method == "POST":
        form = EmployeeDocumentRejectForm(request.POST, instance=doc_request)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.status = "rejected"
            instance.save()
            messages.success(request, _("Document request rejected."))
            notify.send(
                request.user.employee_get,
                recipient=doc_request.employee_id.employee_user_id,
                verb=f"Your document request '{doc_request.title}' was rejected.",
                redirect=reverse("employee-profile"),
                icon="alert-circle",
            )
            return HorillaRedirect(request)
    context = {"form": form, "doc_request": doc_request}
    return render(
        request,
        "documents/htmx/employee_document_reject_form.html",
        context=context,
    )


@login_required
@hx_request_required
def employee_document_request_delete(request, id):
    doc_request = get_object_or_404(EmployeeDocumentRequest, id=id)
    employee = request.user.employee_get
    if doc_request.employee_id != employee and not request.user.has_perm(
        "horilla_documents.delete_employeedocumentrequest"
    ):
        messages.error(
            request, _("You do not have permission to delete this request.")
        )
        return HorillaRedirect(request)
    if doc_request.status != "pending" and not request.user.has_perm(
        "horilla_documents.delete_employeedocumentrequest"
    ):
        messages.error(request, _("Only pending requests can be deleted."))
        return HorillaRedirect(request)
    doc_request.delete()
    messages.success(request, _("Document request deleted successfully."))
    return HttpResponse(
        f'<span id="emp-doc-req-{id}" hx-swap-oob="true"></span>'
        "<script>reloadMessage();</script>"
    )


@login_required
@hx_request_required
def employee_document_request_view_file(request, id):
    doc_request = get_object_or_404(EmployeeDocumentRequest, id=id)
    employee = request.user.employee_get
    if doc_request.employee_id != employee and not request.user.has_perm(
        "horilla_documents.view_employeedocumentrequest"
    ):
        return HorillaRedirect(
            request, message=_("You do not have permission to view this file.")
        )

    file_obj = doc_request.fulfilled_document or doc_request.attachment
    if not file_obj:
        return HttpResponse("")

    file_path = file_obj.path
    file_extension = os.path.splitext(file_path)[1][1:].lower()
    content_type = get_content_type(file_extension)
    try:
        with open(file_path, "rb") as f:
            file_content = f.read()
    except Exception:
        return HttpResponse("")

    context = {
        "req": doc_request,
        "file_content": file_content,
        "file_extension": file_extension,
        "content_type": content_type,
    }
    response = render(request, "documents/htmx/employee_doc_request_view_file.html", context)
    response["HX-Trigger"] = "openEmpDocModal"
    return response


@login_required
@require_http_methods(["POST"])
def employee_profile_bank_details(request):
    """
    This method is used to fill self bank details
    """
    employee = request.user.employee_get
    existing = EmployeeBankDetails.objects.filter(employee_id=employee).first()
    form = EmployeeBankDetailsUpdateForm(request.POST, instance=existing)
    if form.is_valid():
        bank_info = form.save(commit=False)
        bank_info.employee_id = employee
        bank_info.is_primary = True
        bank_info.save()
        messages.success(request, _("Bank details saved"))
    return HorillaRedirect(request)


@login_required
@permission_required("employee.view_profile")
def employee_profile_update(request):
    """
    This method is used update own profile of the requested employee
    """

    employee_user = request.user
    employee = Employee.objects.get(employee_user_id=employee_user)
    if employee_user.has_perm("employee.change_profile"):
        if request.method == "POST":
            form = EmployeeForm(request.POST, request.FILES, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, _("Profile updated."))
    return redirect("/employee/employee-profile")


@login_required
@permission_required("delete_group")
@require_http_methods(["POST"])
def employee_user_group_assign_delete(_, obj_id):
    """
    This method is used to delete user group assign
    """
    user = User.objects.get(id=obj_id)
    user.groups.clear()
    return redirect("/employee/employee-user-group-assign-view")


def paginator_qry(qryset, page_number):
    """
    This method is used to paginate query set
    """
    paginator = HorillaPaginator(qryset, get_pagination())
    qryset = paginator.get_page(page_number)
    return qryset


def _clear_reporting_manager_relations(employee):
    """
    Clear subordinate reporting-manager links before employee deletion.
    """
    EmployeeWorkInformation.objects.filter(reporting_manager_id=employee).update(
        reporting_manager_id=None
    )


@login_required
@enter_if_accessible(
    feature="employee_view",
    perm="employee.view_employee",
    method=_check_reporting_manager,
)
def employee_view(request):
    """
    This method is used to render template for view all employee
    """
    view_type = request.GET.get("view")
    previous_data = request.GET.urlencode()
    page_number = request.GET.get("page")
    error_message = request.session.pop("error_message", None)

    queryset = employee_list_queryset()
    filter_obj = EmployeeFilter(request.GET, queryset=queryset).qs
    if request.GET.get("is_active") != "False":
        filter_obj = filter_obj.filter(is_active=True)

    update_fields = BulkUpdateFieldForm()
    data_dict = parse_qs(previous_data)
    get_key_instances(Employee, data_dict)
    emp = queryset

    # Store the employees in the session
    request.session["filtered_employees"] = list(
        filter_obj.values_list("id", flat=True)
    )

    return render(
        request,
        "employee_personal_info/employee_view.html",
        {
            "data": paginator_qry(filter_obj, page_number),
            "pd": previous_data,
            "f": EmployeeFilter(),
            "update_fields_form": update_fields,
            "view_type": view_type,
            "filter_dict": data_dict,
            "emp": emp,
            "gp_fields": EmployeeReGroup.fields,
            "error_message": error_message,
        },
    )


@login_required
@permission_required("employee.change_employee")
def view_employee_bulk_update(request):
    if request.method == "POST":
        update_fields = request.POST.getlist("update_fields")
        bulk_employee_ids = request.POST.get("bulk_employee_ids")
        bulk_employee_ids_str = (
            json.dumps(bulk_employee_ids) if bulk_employee_ids else ""
        )
        if bulk_employee_ids_str:

            class EmployeeBulkUpdateForm(ModelForm):
                class Meta:
                    model = Employee
                    fields = []
                    widgets = {}
                    labels = {}
                    for field in update_fields:
                        try:
                            field_obj = Employee._meta.get_field(field)
                            if field_obj.name in ("country", "state"):
                                if not "country" in update_fields:
                                    fields.append("country")
                                    widgets["country"] = Select(
                                        attrs={"required": True}
                                    )
                                fields.append(field)
                                widgets[field] = Select(attrs={"required": True})
                            else:
                                fields.append(field)

                            if isinstance(field_obj, models.DateField):
                                widgets[field] = DateInput(
                                    attrs={
                                        "type": "date",
                                        "required": True,
                                        "data-pp": False,
                                    }
                                )
                        except:
                            continue

                def __init__(self, *args, **kwargs):
                    super(EmployeeBulkUpdateForm, self).__init__(*args, **kwargs)
                    for field_name, field in self.fields.items():
                        field.required = True

            class WorkInfoBulkUpdateForm(ModelForm):
                class Meta:
                    model = EmployeeWorkInformation
                    fields = []
                    widgets = {}
                    labels = {}
                    for field in update_fields:
                        try:
                            parts = str(field).split("__")
                            if parts[-1]:
                                if parts[0] == "employee_work_info":
                                    field_obj = EmployeeWorkInformation._meta.get_field(
                                        parts[-1]
                                    )

                                    if (
                                        parts[1] == "department_id"
                                        or parts[1] == "job_position_id"
                                        or parts[1] == "job_role_id"
                                    ):
                                        if (
                                            not "employee_work_info__department_id"
                                            in update_fields
                                        ):
                                            fields.append("department_id")
                                            widgets["department_id"] = Select(
                                                attrs={"required": True}
                                            )
                                        if (
                                            not "employee_work_info__job_position_id"
                                            in update_fields
                                        ):
                                            fields.append("job_position_id")
                                            widgets["job_position_id"] = Select(
                                                attrs={"required": True}
                                            )
                                        if (
                                            not "employee_work_info__job_role_id"
                                            in update_fields
                                        ):
                                            fields.append("job_role_id")
                                            widgets["job_role_id"] = Select(
                                                attrs={"required": True}
                                            )
                                        fields.append(parts[1])
                                        widgets[field] = Select(
                                            attrs={"required": True}
                                        )

                                    fields.append(parts[-1])

                                    # Remove inner lists
                                    fields = [
                                        item
                                        for item in fields
                                        if not isinstance(item, list)
                                    ]

                                    if isinstance(field_obj, models.DateField):
                                        widgets[parts[-1]] = DateInput(
                                            attrs={"type": "date"}
                                        )
                                    if parts[-1] in ("email", "mobile"):
                                        labels[parts[-1]] = (
                                            _("Work Email")
                                            if field_obj.name == "email"
                                            else _("Work Phone")
                                        )
                        except:
                            continue

                def __init__(self, *args, **kwargs):
                    super(WorkInfoBulkUpdateForm, self).__init__(*args, **kwargs)
                    if "department_id" in self.fields:
                        self.fields["department_id"].widget.attrs.update(
                            {
                                "onchange": "depChange($(this))",
                            }
                        )
                    if "job_position_id" in self.fields:
                        self.fields["job_position_id"].widget.attrs.update(
                            {
                                "onchange": "jobChange($(this))",
                            }
                        )
                    for field_name, field in self.fields.items():
                        field.required = True

            class BankInfoBulkUpdateForm(ModelForm):
                class Meta:
                    model = EmployeeBankDetails
                    fields = []
                    widgets = {}
                    labels = {}
                    for field in update_fields:
                        try:
                            parts = str(field).split("__")
                            if parts[-1]:
                                if parts[0] == "employee_bank_details":
                                    field_obj = EmployeeBankDetails._meta.get_field(
                                        parts[-1]
                                    )
                                    fields.append(parts[-1])
                                    if isinstance(field_obj, models.DateField):
                                        widgets[parts[-1]] = DateInput(
                                            attrs={"type": "date"}
                                        )

                                    if field_obj.name in ("country", "state"):
                                        if not "country" in update_fields:
                                            fields.append("country")
                                            widgets["country"] = Select(
                                                attrs={"required": True}
                                            )
                                        fields.append(parts[-1])
                                        widgets[parts[-1]] = Select(
                                            attrs={"required": True}
                                        )
                                        labels[parts[-1]] = (
                                            _("Bank Country")
                                            if field_obj.name == "country"
                                            else _("Bank State")
                                        )

                        except:
                            continue

                def __init__(self, *args, **kwargs):
                    super(BankInfoBulkUpdateForm, self).__init__(*args, **kwargs)
                    for field_name, field in self.fields.items():
                        field.required = True

            form = EmployeeBulkUpdateForm()
            form1 = WorkInfoBulkUpdateForm()
            form2 = BankInfoBulkUpdateForm()

            keys = form1.fields.keys()
            # Convert dict_keys object to a list
            keys_list = list(keys)

            fields_list = []
            for i in keys_list:
                i = "employee_work_info__" + i
                fields_list.append(i)

            for i in fields_list:
                if i not in update_fields:
                    update_fields.append(i)

            update_fields_str = json.dumps(update_fields)

            context = {
                "form": form,
                "form1": form1,
                "form2": form2,
                "update_fields": update_fields_str,
                "bulk_employee_ids": bulk_employee_ids_str,
            }
            return render(
                request,
                "employee_personal_info/bulk_update.html",
                context=context,
            )
        else:
            messages.warning(
                request, _("There are no employees selected for bulk update.")
            )
            return redirect(employee_view)


@login_required
@permission_required("employee.change_employee")
def save_employee_bulk_update(request):
    if request.method == "POST":
        update_fields_str = request.POST.get("update_fields", "")
        update_fields = json.loads(update_fields_str) if update_fields_str else []
        dict_value = request.__dict__["_post"]
        bulk_employee_ids_str = request.POST.get("bulk_employee_ids", "")
        bulk_employee_ids = (
            json.loads(bulk_employee_ids_str) if bulk_employee_ids_str else []
        )
        employee_list = ast.literal_eval(bulk_employee_ids)
        for id in employee_list:
            try:
                employee_instance = Employee.objects.get(id=int(id))
                employee_work_info, created = (
                    EmployeeWorkInformation.objects.get_or_create(
                        employee_id=employee_instance
                    )
                )
                employee_bank = EmployeeBankDetails.objects.filter(employee_id=employee_instance).first()
                if employee_bank is None:
                    employee_bank = EmployeeBankDetails.objects.create(employee_id=employee_instance)
            except (ValueError, OverflowError):
                employee_list.remove(id)

        for field in update_fields:
            parts = str(field).split("__")
            if parts[-1]:
                if parts[0] == "employee_work_info":
                    employee_queryset = EmployeeWorkInformation.objects.filter(
                        employee_id__in=employee_list
                    )
                    value = dict_value.get(parts[-1])
                    employee_queryset.update(**{parts[-1]: value})
                elif parts[0] == "employee_bank_details":
                    for id in employee_list:

                        employee_queryset = EmployeeBankDetails.objects.filter(
                            employee_id__in=employee_list
                        )
                        value = dict_value.get(parts[-1])
                        employee_queryset.update(**{parts[-1]: value})
                else:
                    employee_queryset = Employee.objects.filter(id__in=employee_list)
                    value = dict_value.get(field)
                    employee_queryset.update(**{field: value})
        if len(employee_list) > 0:
            messages.success(
                request,
                _(
                    "{} employees information updated successfully".format(
                        len(employee_list)
                    )
                ),
            )
    return redirect("/employee/employee-view/?view=list")


@login_required
@permission_required("employee.change_employee")
def employee_account_block_unblock(request, emp_id):
    employee = get_object_or_404(Employee, id=emp_id)
    if not employee:
        messages.info(request, _("Employee not found"))
        return redirect(employee_view)
    user = get_object_or_404(User, id=employee.employee_user_id.id)
    if not user:
        messages.info(request, _("Employee not found"))
        return redirect(employee_view)
    if not user.is_superuser:
        user.is_active = not user.is_active
        action_message = _("blocked") if not user.is_active else _("unblocked")
        user.save()
        messages.success(
            request,
            _("{employee}'s account {action_message} successfully!").format(
                employee=employee, action_message=action_message
            ),
        )
    else:
        messages.info(
            request,
            _("{employee} is a superuser and cannot be blocked.").format(
                employee=employee
            ),
        )
    return redirect(employee_view_individual, obj_id=emp_id)


@login_required
@permission_required("employee.add_employee")
def employee_view_new(request):
    """
    This method is used to render form to create a new employee.
    """
    form = EmployeeForm()
    work_form = EmployeeWorkInformationForm()
    bank_form = EmployeeBankDetailsForm()
    filter_obj = EmployeeFilter(queryset=Employee.objects.all())
    return render(
        request,
        "employee/create_form/form_view.html",
        {"form": form, "work_form": work_form, "bank_form": bank_form, "f": filter_obj},
    )


@login_required
@manager_can_enter("employee.change_employee")
def employee_view_update(request, obj_id, **kwargs):
    """
    This method is used to render update form for employee.
    """
    selected_company_id = request.session["selected_company"]
    user = Employee.objects.filter(employee_user_id=request.user).first()
    work_info_history = HistoryTrackingFields.objects.filter(
        work_info_track=True
    ).exists()

    employee = Employee.objects.filter(id=obj_id).first()
    emp = Employee.objects.entire().filter(id=obj_id).first()
    if not employee and emp and hasattr(emp, "employee_work_info"):
        if (
            emp.employee_work_info
            and emp.employee_work_info.company_id
            and emp.employee_work_info.company_id_id != selected_company_id
        ):

            messages.error(
                request, _("Employee is not working in the selected company.")
            )
            return redirect(employee_view)

    if employee is None:
        employee = emp
        cmpny = Company.objects.get(id=selected_company_id)

        work = (
            EmployeeWorkInformation.objects.entire()
            .filter(employee_id=employee)
            .first()
        )

        if work and selected_company_id != "all":
            work.company_id = cmpny
            work.save()

        employee.save()

    if (
        user
        and user.reporting_manager.filter(employee_id=employee).exists()
        or request.user.has_perm("employee.change_employee")
    ):
        form = EmployeeForm(instance=employee)
        work_form = EmployeeWorkInformationForm(
            instance=EmployeeWorkInformation.objects.filter(
                employee_id=employee
            ).first()
        )
        existing_bank = EmployeeBankDetails.objects.filter(employee_id=employee).first()
        bank_form = EmployeeBankDetailsUpdateForm(instance=existing_bank)
        if request.POST:
            if request.POST.get("form") == "personal":
                form = EmployeeForm(request.POST, instance=employee)
                if form.is_valid():
                    form.save()
                    messages.success(
                        request, _("Employee personal information updated.")
                    )
            elif request.POST.get("form") == "work":
                instance = EmployeeWorkInformation.objects.filter(
                    employee_id=employee
                ).first()
                work_form = EmployeeWorkInformationUpdateForm(
                    request.POST, instance=instance
                )
                if work_form.is_valid():
                    instance = work_form.save(commit=False)
                    instance.employee_id = employee
                    instance.save()
                    instance.tags.set(request.POST.getlist("tags"))
                    notify.send(
                        request.user.employee_get,
                        recipient=instance.employee_id.employee_user_id,
                        verb="Your work details has been updated.",
                        verb_ar="تم تحديث تفاصيل عملك.",
                        verb_de="Ihre Arbeitsdetails wurden aktualisiert.",
                        verb_es="Se han actualizado los detalles de su trabajo.",
                        verb_fr="Vos informations professionnelles ont été mises à jour.",
                        redirect=reverse("employee-profile"),
                        icon="briefcase",
                    )
                    messages.success(request, _("Employee work information updated."))
                work_form = EmployeeWorkInformationForm(
                    instance=EmployeeWorkInformation.objects.filter(
                        employee_id=employee
                    ).first()
                )
            elif request.POST.get("form") == "bank":
                existing_bank = EmployeeBankDetails.objects.filter(employee_id=employee).first()
                bank_form = EmployeeBankDetailsUpdateForm(request.POST, instance=existing_bank)
                if bank_form.is_valid():
                    instance = bank_form.save(commit=False)
                    instance.employee_id = employee
                    instance.is_primary = True
                    instance.save()
                    messages.success(request, _("Employee bank details updated."))
                    bank_form = EmployeeBankDetailsUpdateForm(instance=instance)
        bank_account = EmployeeBankDetails.objects.filter(employee_id=employee).first()
        insurance_form = EmployeeInsuranceForm()
        insurance_accounts = EmployeeInsurance.objects.filter(employee_id=employee)
        return render(
            request,
            "employee/update_form/form_view.html",
            {
                "obj_id": obj_id,
                "form": form,
                "work_form": work_form,
                "bank_form": bank_form,
                "bank_account": bank_account,
                "insurance_form": insurance_form,
                "insurance_accounts": insurance_accounts,
                "work_info_history": work_info_history,
            },
        )
    return HorillaRedirect(request, fallback_url="/employee/employee-view")


@login_required
@require_http_methods(["POST"])
@permission_required("employee.change_employee")
def update_profile_image(request, obj_id):
    """
    This method is used to upload a profile image
    """
    try:
        employee = Employee.objects.get(id=obj_id)
        img = request.FILES["employee_profile"]
        employee.employee_profile = img
        employee.save()
        messages.success(request, _("Profile image updated."))
    except Exception:
        messages.error(request, _("Upload a valid image."))
    response = render(
        request,
        "employee/profile/profile_modal.html",
    )
    return HttpResponse(
        response.content.decode("utf-8") + "<script>location.reload();</script>"
    )


@login_required
@require_http_methods(["POST"])
def update_own_profile_image(request):
    """
    This method is used to update own profile image from profile view form
    """
    try:
        employee = request.user.employee_get
        img = request.FILES.get("employee_profile")
        employee.employee_profile = img
        employee.save()
        messages.success(request, _("Profile image updated."))
    except Exception:
        messages.error(request, _("Upload a valid image."))
    response = render(
        request,
        "employee/profile/profile_modal.html",
    )
    return HttpResponse(
        response.content.decode("utf-8") + "<script>location.reload();</script>"
    )


@login_required
@require_http_methods(["DELETE"])
@permission_required("employee.change_employee")
def remove_profile_image(request, obj_id):
    """
    This method is used to remove uploaded image
    Args: obj_id : Employee model instance id
    """
    employee = Employee.objects.get(id=obj_id)
    if employee.employee_profile.name == "":
        messages.info(request, _("No profile image to remove."))
        response = render(
            request,
            "employee/profile/profile_modal.html",
        )
        return HttpResponse(
            response.content.decode("utf-8") + "<script>location.reload();</script>"
        )
    file_path = employee.employee_profile.path
    absolute_path = os.path.join(settings.MEDIA_ROOT, file_path)
    os.remove(absolute_path)
    employee.employee_profile = None
    employee.save()
    messages.success(request, _("Profile image removed."))
    response = render(
        request,
        "employee/profile/profile_modal.html",
    )
    return HttpResponse(
        response.content.decode("utf-8") + "<script>location.reload();</script>"
    )


@login_required
@require_http_methods(["DELETE"])
def remove_own_profile_image(request):
    """
    This method is used to remove own profile image
    """
    employee = request.user.employee_get
    if employee.employee_profile.name == "":
        messages.info(request, _("No profile image to remove."))
        response = render(
            request,
            "employee/profile/profile_modal.html",
        )
        return HttpResponse(
            response.content.decode("utf-8") + "<script>location.reload();</script>"
        )
    file_path = employee.employee_profile.path
    absolute_path = os.path.join(settings.MEDIA_ROOT, file_path)
    os.remove(absolute_path)
    employee.employee_profile = None
    employee.save()

    messages.success(request, _("Profile image removed."))
    response = render(
        request,
        "employee/profile/profile_modal.html",
    )
    return HttpResponse(
        response.content.decode("utf-8") + "<script>location.reload();</script>"
    )


@login_required
@manager_can_enter("employee.change_employee")
@require_http_methods(["POST"])
def employee_create_update_personal_info(request, obj_id=None):
    """
    This method is used to update employee's personal info.
    """
    employee = Employee.objects.filter(id=obj_id).first()
    form = EmployeeForm(request.POST, request.FILES, instance=employee)
    if form.is_valid():
        form.save()
        if obj_id is None:
            messages.success(request, _("New Employee Added."))
            form = EmployeeForm(request.POST, instance=form.instance)
            work_form = EmployeeWorkInformationForm(
                instance=EmployeeWorkInformation.objects.filter(
                    employee_id=employee
                ).first()
            )
            bank_form = EmployeeBankDetailsForm()
            return redirect(
                f"employee-view-update/{form.instance.id}/",
                data={"form": form, "work_form": work_form, "bank_form": bank_form},
            )
        return HttpResponse(
            """
                <div class="oh-alert-container">
                    <div class="oh-alert oh-alert--animated oh-alert--success">
                        Personal Info updated
                    </div>
                </div>

        """
        )
    if obj_id is None:
        return render(
            request,
            "employee/create_form/form_view.html",
            {
                "form": form,
            },
        )
    errors = "\n".join(
        [
            f"<li>{form.fields.get(field, field).label}: {', '.join(errors)}</li>"
            for field, errors in form.errors.items()
        ]
    )
    return HttpResponse(f'<ul class="alert alert-danger">{errors}</ul>')


@login_required
@manager_can_enter("employee.change_employeeworkinformation")
@require_http_methods(["POST"])
def employee_update_work_info(request, obj_id=None):
    """
    This method is used to update employee work info
    """
    employee = Employee.objects.filter(id=obj_id).first()
    form = EmployeeWorkInformationForm(
        request.POST,
        instance=EmployeeWorkInformation.objects.filter(employee_id=employee).first(),
    )
    form.fields["employee_id"].required = False
    form.employee_id = employee
    if form.is_valid() and employee is not None:
        work_info = form.save(commit=False)
        work_info.employee_id = employee
        work_info.save()
        return HttpResponse(
            """

                <div class="oh-alert-container">
                    <div class="oh-alert oh-alert--animated oh-alert--success">
                        Personal Info updated
                    </div>
                </div>

        """
        )
    errors = "\n".join(
        [
            f"<li>{form.fields.get(field, field).label}: {', '.join(errors)}</li>"
            for field, errors in form.errors.items()
        ]
    )
    return HttpResponse(f'<ul class="alert alert-danger">{errors}</ul>')


@login_required
@manager_can_enter("employee.change_employeebankdetails")
@require_http_methods(["POST"])
def employee_update_bank_details(request, obj_id=None):
    """
    This method is used to render form to create employee's bank information.
    """
    employee = Employee.objects.filter(id=obj_id).first()
    existing = EmployeeBankDetails.objects.filter(employee_id=employee).first() if employee else None
    form = EmployeeBankDetailsForm(request.POST, instance=existing)
    if form.is_valid() and employee is not None:
        bank_info = form.save(commit=False)
        bank_info.employee_id = employee
        bank_info.is_primary = True
        bank_info.save()
        return HttpResponse(
            """
            <div class="oh-alert-container">
                <div class="oh-alert oh-alert--animated oh-alert--success">
                    Bank details saved
                </div>
            </div>
        """
        )
    errors = "\n".join(
        [
            f"<li>{form.fields.get(field, field).label}: {', '.join(errors)}</li>"
            for field, errors in form.errors.items()
        ]
    )
    return HttpResponse(f'<ul class="alert alert-danger">{errors}</ul>')


@login_required
@hx_request_required
@enter_if_accessible(
    feature="employee_view",
    perm="employee.view_employee",
    method=_check_reporting_manager,
)
def employee_filter_view(request):
    """
    This method is used to filter employee.
    """
    previous_data = request.GET.urlencode()
    field = request.GET.get("field")
    queryset = employee_list_queryset()
    selected_company = request.session.get("selected_company")
    employees = EmployeeFilter(request.GET, queryset=queryset).qs
    if request.GET.get("is_active") != "False":
        employees = employees.filter(is_active=True)
    if (
        request.GET.get("employee_work_info__company_id") == None
        and selected_company != "all"
    ):
        employees = employees.filter(employee_work_info__company_id=selected_company)
    page_number = request.GET.get("page")
    view = request.GET.get("view")
    data_dict = parse_qs(previous_data)
    get_key_instances(Employee, data_dict)
    template = "employee_personal_info/employee_card.html"
    if view == "list":
        template = "employee_personal_info/employee_list.html"
    if field != "" and field is not None:
        employees = group_by_queryset(employees, field, page_number, "page")
        template = "employee_personal_info/group_by.html"
    else:
        employees = sortby(request, employees, "orderby")
        employees = paginator_qry(employees, page_number)

        # Store the employees in the session
        request.session["filtered_employees"] = list(
            employees.object_list.values_list("id", flat=True)
        )

    return render(
        request,
        template,
        {
            "data": employees,
            "f": EmployeeFilter(request.GET),
            "pd": previous_data,
            "field": field,
            "filter_dict": data_dict,
        },
    )


@login_required
@manager_can_enter("employee.view_employee")
@hx_request_required
def employee_card(request):
    """
    This method renders card template to view all employees.
    """
    previous_data = request.GET.urlencode()
    search = request.GET.get("search")
    if isinstance(search, type(None)):
        search = ""
    employees = filtersubordinatesemployeemodel(
        request, employee_list_queryset(), "employee.view_employee"
    )
    if request.GET.get("is_active") is None:
        filter_obj = EmployeeFilter(
            request.GET,
            queryset=employees.filter(
                employee_first_name__icontains=search, is_active=True
            ),
        )
    else:
        filter_obj = EmployeeFilter(
            request.GET,
            queryset=employees.filter(employee_first_name__icontains=search),
        )
    page_number = request.GET.get("page")
    employees = sortby(request, filter_obj.qs, "orderby")
    return render(
        request,
        "employee_personal_info/employee_card.html",
        {
            "data": paginator_qry(employees, page_number),
            "f": filter_obj,
            "pd": previous_data,
        },
    )


@login_required
@manager_can_enter("employee.view_employee")
@hx_request_required
def employee_list(request):
    """
    This method renders template to view all employees
    """
    previous_data = request.GET.urlencode()
    search = request.GET.get("search")
    if isinstance(search, type(None)):
        search = ""
    if request.GET.get("is_active") is None:
        filter_obj = EmployeeFilter(
            request.GET,
            queryset=employee_list_queryset().filter(
                employee_first_name__icontains=search, is_active=True
            ),
        )
    else:
        filter_obj = EmployeeFilter(
            request.GET,
            queryset=employee_list_queryset().filter(
                employee_first_name__icontains=search
            ),
        )
    employees = filtersubordinatesemployeemodel(
        request, filter_obj.qs, "employee.view_employee"
    )
    employees = sortby(request, employees, "orderby")
    page_number = request.GET.get("page")
    return render(
        request,
        "employee_personal_info/employee_list.html",
        {
            "data": paginator_qry(employees, page_number),
            "f": filter_obj,
            "pd": previous_data,
        },
    )


@login_required
@hx_request_required
@manager_can_enter("employee.view_employee")
def employee_update(request, obj_id):
    """
    This method is used to update employee if the form is valid
    args:
        obj_id : employee id
    """
    employee = Employee.objects.get(id=obj_id)
    form = EmployeeForm(instance=employee)
    work_info = EmployeeWorkInformation.objects.filter(employee_id=employee).first()
    work_form = EmployeeWorkInformationForm()
    bank_form = EmployeeBankDetailsUpdateForm()
    if work_info is not None:
        work_form = EmployeeWorkInformationForm(instance=work_info)
    if request.method == "POST":
        if request.user.has_perm("employee.change_employee"):
            form = EmployeeForm(request.POST, request.FILES, instance=employee)
            if form.is_valid():
                form.save()
                messages.success(request, _("Employee updated."))
    return render(
        request,
        "employee_personal_info/employee_update_form.html",
        {"form": form, "work_form": work_form, "bank_form": bank_form},
    )


@login_required
@permission_required("employee.delete_employee")
@require_http_methods(["POST"])
def employee_delete(request, obj_id):
    """
    This method is used to delete employee
    args:
        id  : employee id
    """

    try:
        view = request.POST.get("view")
        employee = Employee.objects.select_related("employee_user_id").get(id=obj_id)
        user = employee.employee_user_id

        if user and user.is_superuser:
            messages.error(
                request,
                _("%(employee)s is a superuser and cannot be deleted.")
                % {"employee": employee},
            )
            return HorillaRedirect(request, fallback_url=f"/view={view}")

        if apps.is_installed("payroll"):
            if employee.contract_set.all().exists():
                contracts = employee.contract_set.all()
                for contract in contracts:
                    if contract.contract_status != "active":
                        contract.delete()
        _clear_reporting_manager_relations(employee)
        # try:
        #     user.delete()
        # except AttributeError:
        #     employee.delete()
        # messages.success(request, _("Employee deleted"))

        # Delete employee FIRST
        employee.delete()

        # Delete auth user next (only if exists)
        if user:
            user.delete()

        messages.success(request, _("Employee deleted"))

    except Employee.DoesNotExist:
        messages.error(request, _("Employee not found."))
    except ProtectedError as e:
        model_verbose_names_set = set()
        for obj in e.protected_objects:
            model_verbose_names_set.add(__(obj._meta.verbose_name.capitalize()))
        model_names_str = ", - ".join(model_verbose_names_set)
        error_message = _("- {}.".format(model_names_str))
        error_message = str(error_message)
        request.session["error_message"] = error_message
        return redirect(employee_view)
    return HorillaRedirect(request, fallback_url=f"/view={view}")


@login_required
@permission_required("employee.delete_employee")
def employee_bulk_delete(request):
    """
    This method is used to delete set of Employee instances
    """
    ids = json.loads(request.POST.get("ids", "[]"))
    if not ids:
        messages.error(request, _("No IDs provided."))
    deleted_count = 0
    employees = Employee.objects.filter(id__in=ids).select_related("employee_user_id")
    for employee in employees:
        try:
            user = employee.employee_user_id
            if user and user.is_superuser:
                messages.error(
                    request,
                    _("%(employee)s is a superuser and cannot be deleted.")
                    % {"employee": employee},
                )
                continue

            if apps.is_installed("payroll"):
                if employee.contract_set.all().exists():
                    contracts = employee.contract_set.all()
                    for contract in contracts:
                        if contract.contract_status != "active":
                            contract.delete()
            _clear_reporting_manager_relations(employee)
            employee.delete()
            if user:
                user.delete()
            deleted_count += 1
        except Employee.DoesNotExist:
            messages.error(request, _("Employee not found."))
        except ProtectedError:
            messages.error(
                request, _("You cannot delete %(employee)s.") % {"employee": employee}
            )
    if deleted_count > 0:
        messages.success(
            request,
            _("%(deleted_count)s employees deleted.")
            % {"deleted_count": deleted_count},
        )
    return JsonResponse({"message": "Success"})


@login_required
@permission_required("employee.delete_employee")
@require_http_methods(["POST"])
def employee_bulk_archive(request):
    """
    This method is used to archive bulk of Employee instances
    """
    ids = request.POST["ids"]
    ids = json.loads(ids)
    is_active = False
    if request.GET.get("is_active") == "True":
        is_active = True
    for employee_id in ids:
        employee = Employee.objects.get(id=employee_id)

        emp = Employee.objects.get(id=employee_id)
        if emp.employee_user_id.is_superuser and emp.is_active:
            count = 0
            employees = Employee.objects.filter(is_active=True)
            for super_emp in employees:
                if super_emp.employee_user_id.is_superuser:
                    count = count + 1
            if count == 1:
                messages.error(request, _("You can't archive the last superuser."))
                return HttpResponse("<script>$('#filterEmployee').click();</script>")

        employee.is_active = is_active
        employee.employee_user_id.is_active = is_active
        if employee.get_archive_condition() is False:
            employee.save()
            message = _("archived")
            if is_active:
                message = _("un-archived")
            messages.success(request, f"{employee} is {message}")
        else:
            messages.warning(request, _("Related data found for {}.").format(employee))
    return JsonResponse({"message": "Success"})


@login_required
@hx_request_required
@permission_required("employee.delete_employee")
def employee_archive(request, obj_id):
    """
    This method is used to archive employee instance
    Args:
            obj_id : Employee instance id
    """
    employee = Employee.objects.get(id=obj_id)
    employee.is_active = not employee.is_active
    employee.employee_user_id.is_active = not employee.is_active
    save = True
    message = "Employee un-archived"
    if not employee.is_active:

        emp = Employee.objects.get(id=obj_id)
        if emp.employee_user_id.is_superuser:
            count = 0
            employees = Employee.objects.filter(is_active=True)
            for super_emp in employees:
                if super_emp.employee_user_id.is_superuser:
                    count = count + 1
            if count == 1:
                messages.error(request, _("You can't archive the last superuser."))
                return HttpResponse("<script>$('#filterEmployee').click();</script>")

        result = employee.get_archive_condition()
        if result:
            save = False
        else:
            message = _("Employee archived")
    if save:
        employee.save()
        messages.success(request, message)
        key = "HTTP_HX_REQUEST"
        if key not in request.META.keys():
            return HorillaRedirect(request)
        else:
            return HttpResponse("<script>$('#filterEmployee').click();</script>")
    else:
        return render(
            request,
            "related_models.html",
            {
                "employee": employee,
                "related_models": result.get("related_models"),
                "related_model_fields": result.get("related_model_fields"),
                "employee_choices": result.get("employee_choices"),
                "title": _("Can't Archive"),
            },
        )


@login_required
@permission_required("employee.change_employee")
def replace_employee(request, emp_id):
    title = request.GET.get("title")
    employee = Employee.objects.filter(id=emp_id).first()
    related_models = (
        employee.get_archive_condition().get("related_models", "") if employee else None
    )
    if related_models and employee:
        for models in related_models:
            field_name = models.get("field_name", "")
            if field_name:
                replace_emp_id = request.POST.get(field_name)
                replace_emp = Employee.objects.filter(id=replace_emp_id).first()
                if (
                    field_name == "reporting_manager_id"
                    and str(emp_id) != replace_emp_id
                ):
                    reporting_manager = EmployeeWorkInformation.objects.filter(
                        reporting_manager_id=emp_id
                    ).update(reporting_manager_id=replace_emp)
                elif (
                    apps.is_installed("recruitment")
                    and field_name == "recruitment_managers"
                    and str(emp_id) != replace_emp_id
                ):
                    Recruitment = get_horilla_model_class(
                        app_label="recruitment", model="recruitment"
                    )
                    recruitment_query = Recruitment.objects.filter(
                        recruitment_managers=emp_id
                    )
                    if recruitment_query:
                        for recruitment in recruitment_query:
                            recruitment.recruitment_managers.remove(emp_id)
                            recruitment.recruitment_managers.add(replace_emp)
                elif (
                    apps.is_installed("recruitment")
                    and field_name == "recruitment_stage_managers"
                    and str(emp_id) != replace_emp_id
                ):
                    Stage = get_horilla_model_class(
                        app_label="recruitment", model="stage"
                    )
                    recruitment_stage_query = Stage.objects.filter(
                        stage_managers=emp_id
                    )
                    if recruitment_stage_query:
                        for stage in recruitment_stage_query:
                            stage.stage_managers.remove(emp_id)
                            stage.stage_managers.add(replace_emp)
                elif (
                    apps.is_installed("onboarding")
                    and field_name == "onboarding_stage_manager"
                    and str(emp_id) != replace_emp_id
                ):
                    OnboardingStage = get_horilla_model_class(
                        app_label="onboarding", model="onboardingstage"
                    )
                    onboarding_stage_query = OnboardingStage.objects.filter(
                        employee_id=emp_id
                    )
                    if onboarding_stage_query:
                        for stage in onboarding_stage_query:
                            stage.employee_id.remove(emp_id)
                            stage.employee_id.add(replace_emp)
                elif (
                    apps.is_installed("onboarding")
                    and field_name == "onboarding_task_manager"
                    and str(emp_id) != replace_emp_id
                ):
                    OnboardingTask = get_horilla_model_class(
                        app_label="onboarding", model="onboardingtask"
                    )
                    onboarding_task_query = OnboardingTask.objects.filter(
                        employee_id=emp_id
                    )
                    if onboarding_task_query:
                        for task in onboarding_task_query:
                            task.employee_id.remove(emp_id)
                            task.employee_id.add(replace_emp)
                else:
                    pass
    related_models = employee.get_archive_condition()
    if title == "Change the Designations":
        messages.success(request, _("Designation changed."))
        return redirect("/offboarding/offboarding-pipeline")
    if related_models is False and title != "Change the Designations":
        employee.is_active = False
        employee.save()
        messages.success(request, _("{} archived successfully").format(employee))
    return redirect(employee_view)


@login_required
@permission_required("employee.view_employee")
def get_manager_in(request):
    """
    This method is used to get the manager in records model
    """
    employee_id = request.GET.get("employee_id")
    employee = Employee.objects.filter(id=employee_id).first()
    offboarding = request.GET.get("offboarding")
    if offboarding:
        title = _("Change the Designations")
    else:
        title = _("Can't Archive")
    employee.is_active = not employee.is_active
    employee.employee_user_id.is_active = not employee.is_active
    save = True
    message = "Employee un-archived"
    if not employee.is_active:
        result = employee.get_archive_condition()
        if result:
            save = False
        else:
            message = _("Employee archived")
    if save:
        employee.save()
        messages.success(request, message)
        return HorillaRedirect(request)
    else:
        return render(
            request,
            "related_models.html",
            {
                "employee": employee,
                "related_models": result.get("related_models"),
                "related_model_fields": result.get("related_model_fields"),
                "employee_choices": result.get("employee_choices"),
                "title": title,
            },
        )


@login_required
@enter_if_accessible(
    feature="employee_view",
    perm="employee.view_employee",
    method=_check_reporting_manager,
)
def employee_search(request):
    """
    This method is used to search employee
    """
    search = request.GET["search"]
    view = request.GET["view"]
    previous_data = request.GET.urlencode()
    employees = EmployeeFilter(request.GET, queryset=employee_list_queryset()).qs
    if search == "":
        employees = employees.filter(is_active=True)
    page_number = request.GET.get("page")
    template = "employee_personal_info/employee_card.html"
    if view == "list":
        template = "employee_personal_info/employee_list.html"
    employees = filtersubordinatesemployeemodel(
        request, employees, "employee.view_employee"
    )
    employees = sortby(request, employees, "orderby")
    data_dict = parse_qs(previous_data)
    get_key_instances(Employee, data_dict)
    return render(
        request,
        template,
        {
            "data": paginator_qry(employees, page_number),
            "pd": previous_data,
            "filter_dict": data_dict,
        },
    )


@login_required
@manager_can_enter("employee.add_employeeworkinformation")
@require_http_methods(["POST"])
def employee_work_info_view_create(request, obj_id):
    """
    This method is used to create employee work information from employee single view template
    args:
        obj_id : employee instance id
    """

    employee = Employee.objects.get(id=obj_id)
    form = EmployeeForm(instance=employee)

    work_form = EmployeeWorkInformationUpdateForm(request.POST)

    bank_form = EmployeeBankDetailsUpdateForm()
    bank_form_instance = EmployeeBankDetails.objects.filter(
        employee_id=employee
    ).first()
    if bank_form_instance is not None:
        bank_form = EmployeeBankDetailsUpdateForm(
            instance=employee.employee_bank_details
        )

    if work_form.is_valid():
        work_info = work_form.save(commit=False)
        work_info.employee_id = employee
        work_info.save()
        messages.success(request, _("Created work information"))
    return render(
        request,
        "employee_personal_info/employee_update_form.html",
        {"form": form, "work_form": work_form, "bank_form": bank_form},
    )


@login_required
@manager_can_enter("employee.change_employeeworkinformation")
@require_http_methods(["POST"])
def employee_work_info_view_update(request, obj_id):
    """
    This method is used to update employee work information from single view template
    args:
        obj_id  : employee work information id
    """

    work_information = EmployeeWorkInformation.objects.get(id=obj_id)
    form = EmployeeForm(instance=work_information.employee_id)
    bank_form = EmployeeBankDetailsUpdateForm(
        instance=work_information.employee_id.get_primary_bank()
    )
    work_form = EmployeeWorkInformationUpdateForm(
        request.POST,
        instance=work_information,
    )
    if work_form.is_valid():
        work_form.save()
        messages.success(request, _("Work Information Updated Successfully"))
    return render(
        request,
        "employee_personal_info/employee_update_form.html",
        {"form": form, "work_form": work_form, "bank_form": bank_form},
    )


@login_required
@manager_can_enter("employee.add_employeebankdetails")
@require_http_methods(["POST"])
def employee_bank_details_view_create(request, obj_id):
    """
    This method used to create bank details object from the view template
    args:
        obj_id : employee instance id
    """
    employee = Employee.objects.get(id=obj_id)
    form = EmployeeForm(instance=employee)
    existing_bank = EmployeeBankDetails.objects.filter(employee_id=employee).first()
    bank_form = EmployeeBankDetailsUpdateForm(request.POST, instance=existing_bank)
    work_form_instance = EmployeeWorkInformation.objects.filter(
        employee_id=employee
    ).first()
    work_form = EmployeeWorkInformationUpdateForm()
    if work_form_instance is not None:
        work_form = EmployeeWorkInformationUpdateForm(instance=work_form_instance)
    if bank_form.is_valid():
        bank_instance = bank_form.save(commit=False)
        bank_instance.employee_id = employee
        bank_instance.is_primary = True
        bank_instance.save()
        messages.success(request, _("Bank Details Saved Successfully"))
    return render(
        request,
        "employee_personal_info/employee_update_form.html",
        {"form": form, "work_form": work_form, "bank_form": bank_form},
    )


@login_required
@manager_can_enter("employee.change_employeebankdetails")
@require_http_methods(["POST"])
def employee_bank_details_view_update(request, obj_id):
    """
    This method is used to update employee bank details.
    """
    employee_bank_instance = EmployeeBankDetails.objects.get(id=obj_id)
    form = EmployeeForm(instance=employee_bank_instance.employee_id)
    work_form = EmployeeWorkInformationUpdateForm(
        instance=employee_bank_instance.employee_id.employee_work_info
    )
    bank_form = EmployeeBankDetailsUpdateForm(
        request.POST, instance=employee_bank_instance
    )
    if bank_form.is_valid():
        bank_instance = bank_form.save(commit=False)
        bank_instance.employee_id = employee_bank_instance.employee_id
        bank_instance.is_primary = True
        bank_instance.save()
        messages.success(request, _("Bank Details Updated Successfully"))
    return render(
        request,
        "employee_personal_info/employee_update_form.html",
        {"form": form, "work_form": work_form, "bank_form": bank_form},
    )


@login_required
@permission_required("employee.delete_employeeworkinformation")
@require_http_methods(["POST", "DELETE"])
def employee_work_information_delete(request, obj_id):
    """
    This method is used to delete employee work information
    args:
        obj_id : employee work information id
    """
    try:
        employee_work = EmployeeWorkInformation.objects.get(id=obj_id)
        employee_work.delete()
        messages.success(request, _("Employee work information deleted"))
    except EmployeeWorkInformation.DoesNotExist:
        messages.error(request, _("Employee work information not found."))
    except ProtectedError:
        messages.error(request, _("You cannot delete this Employee work information"))

    return redirect("/employee/employee-work-information-view")


@login_required
@require_http_methods(["POST", "DELETE"])
def employee_delete_bank_details(request, obj_id):
    """
    This method is used to delete a specific employee bank account.
    args:
        obj_id : EmployeeBankDetails instance id
    """
    user = request.user
    try:
        bank = EmployeeBankDetails.objects.get(id=obj_id)
        employee = bank.employee_id
        if (
            user == employee.employee_user_id
            or user.has_perm("employee.delete_employeebankdetails")
        ):
            bank.delete()
            messages.success(request, _("Bank account deleted"))
        else:
            messages.error(request, _("You do not have permission to delete this bank account"))
    except EmployeeBankDetails.DoesNotExist:
        messages.error(request, _("Bank account not found"))
    return HorillaRedirect(request)


@login_required
@require_http_methods(["POST"])
def add_bank(request):
    """Create a new Bank entry and return its id and name as JSON."""
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Bank name is required."}, status=400)
    bank, _ = Bank.objects.get_or_create(name=name)
    return JsonResponse({"id": bank.id, "name": bank.name})


@login_required
@require_http_methods(["POST"])
def employee_save_insurance(request, obj_id=None):
    """
    Create or update an employee insurance record.
    obj_id is the employee id when creating, or reused for employee lookup.
    """
    user = request.user
    insurance_id = request.POST.get("insurance_id")
    # Determine the employee
    if obj_id:
        employee = Employee.objects.filter(id=obj_id).first()
        if not (user.has_perm("employee.change_employeeinsurance") or (employee and user == employee.employee_user_id)):
            messages.error(request, _("Permission denied"))
            return HorillaRedirect(request)
    else:
        employee = getattr(user, "employee_get", None)
    if employee is None:
        messages.error(request, _("Employee not found"))
        return HorillaRedirect(request)
    existing = EmployeeInsurance.objects.filter(id=insurance_id, employee_id=employee).first() if insurance_id else None
    form = EmployeeInsuranceForm(request.POST, instance=existing)
    if form.is_valid():
        record = form.save(commit=False)
        record.employee_id = employee
        record.save()
        messages.success(request, _("Insurance record saved"))
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
    return HorillaRedirect(request)


@login_required
@require_http_methods(["POST", "DELETE"])
def employee_delete_insurance(request, obj_id):
    """
    Delete a specific employee insurance record.
    """
    user = request.user
    try:
        record = EmployeeInsurance.objects.get(id=obj_id)
        employee = record.employee_id
        if user == employee.employee_user_id or user.has_perm("employee.delete_employeeinsurance"):
            record.delete()
            messages.success(request, _("Insurance record deleted"))
        else:
            messages.error(request, _("Permission denied"))
    except EmployeeInsurance.DoesNotExist:
        messages.error(request, _("Insurance record not found"))
    return HorillaRedirect(request)


@login_required
@permission_required("employee.add_employee")
def employee_import(request):
    """
    This method is used to create employee and corresponding user.
    """
    if request.method == "POST":
        file = request.FILES["file"]
        # Read the Excel file into a Pandas DataFrame
        data_frame = pd.read_excel(file)
        # Convert the DataFrame to a list of dictionaries
        employee_dicts = data_frame.to_dict("records")
        # Create or update Employee objects from the list of dictionaries
        error_list = []
        for employee_dict in employee_dicts:
            try:
                phone = employee_dict["phone"]
                email = employee_dict["email"]
                employee_full_name = employee_dict["employee_full_name"]
                existing_user = User.objects.filter(username=email).first()
                if existing_user is None:
                    employee_first_name = employee_full_name
                    employee_last_name = ""
                    if " " in employee_full_name:
                        (
                            employee_first_name,
                            employee_last_name,
                        ) = employee_full_name.split(" ", 1)

                    user = User.objects.create_user(
                        username=email,
                        email=email,
                        password=str(phone).strip(),
                        is_superuser=False,
                    )
                    employee = Employee()
                    employee.employee_user_id = user
                    employee.employee_first_name = employee_first_name
                    employee.employee_last_name = employee_last_name
                    employee.email = email
                    employee.phone = phone
                    employee.save()
            except Exception:
                error_list.append(employee_dict)
        return HttpResponse(
            """
    <div class='alert-success p-3 border-rounded'>
        Employee data has been imported successfully.
    </div>

    """
        )
    data_frame = pd.DataFrame(columns=["employee_full_name", "email", "phone"])
    # Export the DataFrame to an Excel file
    response = HttpResponse(content_type="application/ms-excel")
    response["Content-Disposition"] = 'attachment; filename="employee_template.xlsx"'
    data_frame.to_excel(response, index=False)
    return response


@login_required
@permission_required("employee.add_employee")
def employee_export(_):
    """
    This method is used to export employee data to xlsx
    """
    # Get the list of field names for your model
    field_names = [f.name for f in Employee._meta.get_fields() if not f.auto_created]
    field_names.remove("employee_user_id")
    field_names.remove("employee_profile")
    field_names.remove("additional_info")
    field_names.remove("is_from_onboarding")
    field_names.remove("is_directly_converted")
    field_names.remove("is_active")

    # Get the existing employee data and convert it to a DataFrame
    employee_data = Employee.objects.values_list(*field_names)
    data_frame = pd.DataFrame(list(employee_data), columns=field_names)

    # Export the DataFrame to an Excel file

    response = HttpResponse(content_type="application/ms-excel")
    response["Content-Disposition"] = 'attachment; filename="employee_export.xlsx"'
    data_frame.to_excel(response, index=False)

    return response


def convert_nan(field, dicts):
    """
    This method is returns None or field value
    """
    field_value = dicts.get(field)
    try:
        float(field_value)
        return None
    except (ValueError, TypeError):
        return field_value


@login_required
@permission_required("employee.add_employee")
def work_info_import_file(request):
    """
    This method is used to return the excel file of import Employee instances
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    bank_names = get_import_bank_names()
    insurance_names = get_import_insurance_names()

    columns = [
        "Employee No",
        "First Name",
        "Middle Name",
        "Last Name",
        "Extension",
        "Date of Birth",
        "Gender",
        "Address",
        "Country",
        "Province",
        "City",
        "Qualification",
        "Experience",
        "Marital Status",
        "Children",
        "Emergency Contact",
        "Emergency Contact Name",
        "Emergency Contact Relation",
        "TIN Number",
        "SSS Number",
        "HDMF Number",
        "PhilHealth Number",
        "Phone",
        "Email",
        "Company",
        "Department",
        "Job Position",
        "Job Role",
        "Shift Information",
        "Employee Type",
        "Payroll Group",
        "Reporting Manager",
        "Work Location",
        "Branch",
        "Cost Center",
        "Business Unit",
        "Work Type",
        "Salary",
        "Salary Hour",
        "Joining Date",
        "End Date",
        "Employee Status",
        "Tags",
        "Is Active",
        "Work Email",
        "Work Phone",
        "PIN",
        "Bank",
        "Account Number",
        *[col for n in insurance_names for col in (n, f"{n} Start Date", f"{n} End Date")],
    ]
    example = {
        "Employee No": "EMP001",
        "First Name": "Clifford Allen",
        "Middle Name": "",
        "Last Name": "Ebayan",
        "Extension": "",
        "Date of Birth": "",
        "Gender": "male",
        "Address": "",
        "Country": "",
        "Province": "",
        "City": "",
        "Qualification": "",
        "Experience": "",
        "Marital Status": "single",
        "Children": "",
        "Emergency Contact": "",
        "Emergency Contact Name": "",
        "Emergency Contact Relation": "",
        "TIN Number": "",
        "SSS Number": "",
        "HDMF Number": "",
        "PhilHealth Number": "",
        "Phone": "09171234567",
        "Email": "clifford.ebayan@example.com",
        "Company": "Martin Development Corporation",
        "Department": "Information Technology",
        "Job Position": "",
        "Job Role": "",
        "Shift Information": "Morning Shift 8-5 (M-F)",
        "Employee Type": "",
        "Payroll Group": "",
        "Reporting Manager": "",
        "Work Location": "",
        "Branch": "MAIN",
        "Cost Center": "",
        "Business Unit": "",
        "Work Type": "",
        "Salary": "",
        "Salary Hour": "",
        "Joining Date": "2026-04-23",
        "End Date": "",
        "Employee Status": "active",
        "Tags": "",
        "Is Active": "true",
        "Work Email": "",
        "Work Phone": "",
        "PIN": "",
        "Bank": "",
        "Account Number": "",
        **{col: "" for n in insurance_names for col in (n, f"{n} Start Date", f"{n} End Date")},
    }

    # --- Query DB values for relational fields ---

    db_ref_fields = {
        "Company": list(Company.objects.values_list("company", flat=True)),
        "Department": list(Department.objects.values_list("department", flat=True)),
        "Job Position": list(JobPosition.objects.values_list("job_position", flat=True)),
        "Job Role": list(JobRole.objects.values_list("job_role", flat=True)),
        "Shift Information": list(EmployeeShift.objects.values_list("employee_shift", flat=True)),
        "Employee Type": list(EmployeeType.objects.values_list("employee_type", flat=True)),
        "Payroll Group": list(PayrollGroup.objects.values_list("name", flat=True)),
        "Work Type": list(WorkType.objects.values_list("work_type", flat=True)),
        "Branch": list(Branch.objects.values_list("branch", flat=True)),
        "Cost Center": list(CostCenter.objects.values_list("name", flat=True)),
        "Business Unit": list(BusinessUnit.objects.values_list("name", flat=True)),
        "Bank": list(bank_names),
    }

    philippine_provinces = [
        "Abra", "Agusan del Norte", "Agusan del Sur", "Aklan", "Albay",
        "Antique", "Apayao", "Aurora", "Basilan", "Bataan", "Batanes",
        "Batangas", "Benguet", "Biliran", "Bohol", "Bukidnon", "Bulacan",
        "Cagayan", "Camarines Norte", "Camarines Sur", "Camiguin", "Capiz",
        "Catanduanes", "Cavite", "Cebu", "Cotabato", "Davao de Oro",
        "Davao del Norte", "Davao del Sur", "Davao Occidental", "Davao Oriental",
        "Dinagat Islands", "Eastern Samar", "Guimaras", "Ifugao",
        "Ilocos Norte", "Ilocos Sur", "Iloilo", "Isabela", "Kalinga",
        "La Union", "Laguna", "Lanao del Norte", "Lanao del Sur", "Leyte",
        "Maguindanao del Norte", "Maguindanao del Sur", "Marinduque", "Masbate",
        "Metro Manila", "Misamis Occidental", "Misamis Oriental",
        "Mountain Province", "Negros Occidental", "Negros Oriental",
        "Northern Samar", "Nueva Ecija", "Nueva Vizcaya",
        "Occidental Mindoro", "Oriental Mindoro", "Palawan", "Pampanga",
        "Pangasinan", "Quezon", "Quirino", "Rizal", "Romblon", "Samar",
        "Sarangani", "Siquijor", "Sorsogon", "South Cotabato",
        "Southern Leyte", "Sultan Kudarat", "Sulu", "Surigao del Norte",
        "Surigao del Sur", "Tarlac", "Tawi-Tawi", "Zambales",
        "Zamboanga del Norte", "Zamboanga del Sur", "Zamboanga Sibugay",
    ]

    philippine_cities = [
        # NCR
        "Caloocan", "Las Pinas", "Makati", "Malabon", "Mandaluyong", "Manila",
        "Marikina", "Muntinlupa", "Navotas", "Paranaque", "Pasay", "Pasig",
        "Quezon City", "San Juan", "Taguig", "Valenzuela",
        # Ilocos Region
        "Batac", "Laoag", "Candon", "Vigan", "San Fernando (La Union)",
        "Dagupan", "San Carlos (Pangasinan)", "Urdaneta",
        # Cagayan Valley
        "Cauayan", "Ilagan", "Santiago", "Tuguegarao",
        # Central Luzon
        "Balanga", "Angeles", "Mabalacat", "San Fernando (Pampanga)",
        "Cabanatuan", "Gapan", "Munoz", "Palayan", "San Jose (Nueva Ecija)",
        "Olongapo", "Tarlac City",
        # CALABARZON
        "Antipolo", "Bacoor", "Cavite City", "Dasmarinas", "General Trias",
        "Imus", "Tagaytay", "Trece Martires", "Binan", "Calamba",
        "San Pablo", "Santa Rosa", "Batangas City", "Lipa", "Tanauan",
        "Lucena",
        # MIMAROPA
        "Puerto Princesa",
        # Bicol
        "Iriga", "Naga", "Legazpi", "Ligao", "Tabaco", "Masbate City",
        "Sorsogon City",
        # Western Visayas
        "Bacolod", "Bago", "Cadiz", "Escalante", "Himamaylan", "Kabankalan",
        "La Carlota", "Sagay", "San Carlos (Negros Occidental)", "Silay",
        "Sipalay", "Talisay (Negros Occidental)", "Victorias",
        "Iloilo City", "Passi", "Roxas",
        # Central Visayas
        "Carcar", "Cebu City", "Danao", "Lapu-Lapu", "Mandaue",
        "Naga (Cebu)", "Talisay (Cebu)", "Toledo", "Tagbilaran",
        "Bais", "Bayawan", "Canlaon", "Dumaguete", "Guihulngan", "Tanjay",
        # Eastern Visayas
        "Baybay", "Ormoc", "Tacloban", "Calbayog", "Catbalogan", "Borongan",
        # Zamboanga Peninsula
        "Dapitan", "Dipolog", "Isabela City", "Pagadian", "Zamboanga City",
        # Northern Mindanao
        "Cagayan de Oro", "Gingoog", "Oroquieta", "Ozamiz", "Tangub",
        "Iligan", "Malaybalay", "Valencia",
        # Davao Region
        "Davao City", "Digos", "Island Garden City of Samal", "Mati",
        "Panabo", "Tagum",
        # SOCCSKSARGEN
        "General Santos", "Kidapawan", "Koronadal", "Tacurong",
        # Caraga
        "Bayugan", "Bislig", "Butuan", "Cabadbaran", "Surigao City", "Tandag",
        # BARMM
        "Cotabato City", "Marawi",
        # CAR
        "Baguio", "Tabuk",
    ]

    geo_ref_fields = {
        "Country": ["Philippines"],
        "Province": philippine_provinces,
        "City": philippine_cities,
    }

    static_choices = {
        "Gender": ["male", "female", "other"],
        "Marital Status": ["single", "married", "divorced"],
        "Employee Status": ["active", "resigned", "awol", "terminated", "retired"],
        "Is Active": ["true", "false"],
    }

    wb = Workbook()

    # --- Build Reference sheet with DB values and geo lists ---
    ref_ws = wb.active
    ref_ws.title = "Reference"
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="D9E1F2")

    all_ref_fields = {**db_ref_fields, **geo_ref_fields}
    ref_col_ranges = {}
    for col_idx, (field_name, values) in enumerate(all_ref_fields.items(), start=1):
        col_letter = get_column_letter(col_idx)
        header_cell = ref_ws.cell(row=1, column=col_idx, value=field_name)
        header_cell.font = header_font
        header_cell.fill = header_fill
        for row_idx, val in enumerate(values, start=2):
            ref_ws.cell(row=row_idx, column=col_idx, value=val)
        max_row = len(values) + 1
        ref_col_ranges[field_name] = f"Reference!${col_letter}$2:${col_letter}${max_row}" if values else None
        ref_ws.column_dimensions[col_letter].width = max(len(field_name), *(len(str(v)) for v in values) if values else [10]) + 2

    # --- Build Import Template sheet ---
    ws = wb.create_sheet("Import Template", 0)
    wb.active = ws

    col_map = {col: idx for idx, col in enumerate(columns, start=1)}

    header_fill_blue = PatternFill("solid", fgColor="1F4E79")
    header_font_white = Font(bold=True, color="FFFFFF")

    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font_white
        cell.fill = header_fill_blue
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col_idx)].width = max(len(col_name), 15)

    for col_idx, col_name in enumerate(columns, start=1):
        ws.cell(row=2, column=col_idx, value=example.get(col_name, ""))

    ws.row_dimensions[1].height = 30

    geo_field_names = set(geo_ref_fields.keys())

    # --- Add data validations for DB-backed fields (stop on invalid) ---
    for field_name, formula_range in ref_col_ranges.items():
        if field_name not in col_map or not formula_range:
            continue
        col_letter = get_column_letter(col_map[field_name])
        is_geo = field_name in geo_field_names
        dv = DataValidation(
            type="list",
            formula1=formula_range,
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            errorStyle="warning" if is_geo else "stop",
            error=(
                "This value is not in the list. You may still proceed if entering a municipality."
                if is_geo
                else "Value not found in database. See the Reference sheet for valid values."
            ),
            errorTitle="Not in List" if is_geo else "Invalid Value",
        )
        dv.sqref = f"{col_letter}2:{col_letter}1000"
        ws.add_data_validation(dv)

    # --- Add data validations for static choice fields ---
    for field_name, choices in static_choices.items():
        if field_name not in col_map:
            continue
        col_letter = get_column_letter(col_map[field_name])
        formula = '"' + ",".join(choices) + '"'
        dv = DataValidation(
            type="list",
            formula1=formula,
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            error=f"Valid values: {', '.join(choices)}",
            errorTitle="Invalid Value",
        )
        dv.sqref = f"{col_letter}2:{col_letter}1000"
        ws.add_data_validation(dv)

    # --- Add yes/no dropdown and date validations for insurance columns ---
    for insurance_name in insurance_names:
        enroll_col = insurance_name
        start_col = f"{insurance_name} Start Date"
        end_col = f"{insurance_name} End Date"

        if enroll_col in col_map:
            col_letter = get_column_letter(col_map[enroll_col])
            dv = DataValidation(
                type="list",
                formula1='"yes,no"',
                allow_blank=True,
                showDropDown=False,
                showErrorMessage=True,
                error='Enter "yes" to enroll or "no" to skip.',
                errorTitle="Invalid Value",
            )
            dv.sqref = f"{col_letter}2:{col_letter}1000"
            ws.add_data_validation(dv)

        for date_col in (start_col, end_col):
            if date_col in col_map:
                col_letter = get_column_letter(col_map[date_col])
                dv = DataValidation(
                    type="date",
                    allow_blank=True,
                    showErrorMessage=True,
                    error="Enter a valid date (YYYY-MM-DD).",
                    errorTitle="Invalid Date",
                )
                dv.sqref = f"{col_letter}2:{col_letter}1000"
                ws.add_data_validation(dv)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="work_info_template.xlsx"'
    return response


@login_required
@hx_request_required
@permission_required("employee.add_employee")
def work_info_import(request):
    if request.method == "GET":
        return render(request, "employee/employee_import.html")

    if request.method == "POST":
        file = request.FILES.get("file")
        if not file:
            error_message = _("No file uploaded.")
            return render(
                request,
                "employee/employee_import.html",
                {"error_message": error_message},
            )

        file_extension = file.name.split(".")[-1].lower()

        try:
            if file_extension == "csv":
                data_frame = pd.read_csv(file)
            elif file_extension in ["xls", "xlsx"]:
                data_frame = pd.read_excel(file)
            else:

                error_message = _(
                    "Unsupported file format. Please upload a CSV or Excel file."
                )
                return render(
                    request,
                    "employee/employee_import.html",
                    {"error_message": error_message},
                )

            valid, error_message = valid_import_file_headers(data_frame)
            if not valid:
                return render(
                    request,
                    "employee/employee_import.html",
                    {"error_message": error_message},
                )
            success_list, error_list, _validated_count = process_employee_records(
                data_frame
            )
            created_count = 0
            total_count = len(data_frame.index)

            # If user is scoped to a selected company and the import row does not
            # include a Company value, default it to the selected company so newly
            # imported employees remain visible in company-filtered views.
            selected_company = request.session.get("selected_company")
            if selected_company and selected_company != "all":
                selected_company_obj = Company.objects.filter(
                    id=selected_company
                ).only("company").first()
                if selected_company_obj:
                    for row in success_list:
                        company_value = row.get("Company")
                        if (
                            company_value is None
                            or pd.isna(company_value)
                            or str(company_value).strip().lower()
                            in {"", "nan", "none", "null"}
                        ):
                            row["Company"] = selected_company_obj.company
            employees = []
            if success_list:
                try:
                    with transaction.atomic():
                        bulk_create_user_import(success_list)
                        employees = bulk_create_employee_import(success_list)
                        bulk_create_department_import(success_list)
                        bulk_create_job_position_import(success_list)
                        bulk_create_job_role_import(success_list)
                        bulk_create_work_types(success_list)
                        bulk_create_shifts(success_list)
                        bulk_create_employee_types(success_list)
                        bulk_create_work_info_import(success_list)
                        bulk_create_bank_details_import(success_list)
                        bulk_set_tags_import(success_list)
                        bulk_create_insurance_import(success_list)
                except Exception as e:
                    messages.error(request, _("Error Occured {}").format(e))
                    logger.error(e)
                    for row in success_list:
                        failed_row = dict(row)
                        failed_row["Import Error"] = str(e)
                        error_list.append(failed_row)
                else:
                    created_count = len(employees)
                    if created_count < len(success_list):
                        missing_count = len(success_list) - created_count
                        for row in success_list[:missing_count]:
                            failed_row = dict(row)
                            failed_row["Import Error"] = _(
                                "Row validated but was not created due to a database write issue."
                            )
                            error_list.append(failed_row)
                    if employees:
                        threading.Thread(
                            target=set_initial_password, args=(employees,)
                        ).start()
            error_count = max(total_count - created_count, 0)
            if error_count and not error_list:
                error_list.append(
                    {
                        "Import Error": _(
                            "Some rows could not be created. Please review and re-upload."
                        )
                    }
                )

            path_info = (
                generate_error_report(
                    error_list, get_error_data_template(), "EmployeesImportError.xlsx"
                )
                if error_count
                else None
            )

            context = {
                "created_count": created_count,
                "total_count": total_count,
                "error_count": error_count,
                "model": _("Employees"),
                "path_info": path_info,
            }
            result = render_to_string("import_popup.html", context)
            result += """
                        <script>
                            $('#objectCreateModalTarget').css('max-width', '410px');
                        </script>
                    """
            return HttpResponse(result)
        except Exception as e:
            messages.error(
                request,
                _(
                    "Failed to read file. Please ensure it is a valid CSV or Excel file. : {}"
                ).format(e),
            )
            logger.error(f"File import error: {e}")
            error_message = f"File import error: {e}"
    return render(
        request, "employee/employee_import.html", {"error_message": error_message}
    )


@login_required
@manager_can_enter("employee.view_employee")
def work_info_export(request):
    """
    This method is used to export employee data to xlsx
    """
    if request.META.get("HTTP_HX_REQUEST"):
        context = {
            "export_filter": EmployeeFilter(),
            "export_form": EmployeeExportExcelForm(),
        }
        return render(request, "employee_export_filter.html", context)

    employees_data = {}
    selected_columns = []
    form = EmployeeExportExcelForm()
    field_overrides = {
        "employee_work_info__company_id": "employee_work_info__company_id__company",
        "employee_work_info__department_id": "employee_work_info__department_id__department",
        "employee_work_info__job_position_id": "employee_work_info__job_position_id__job_position",
        "employee_work_info__job_role_id": "employee_work_info__job_role_id__job_role",
        "employee_work_info__shift_id": "employee_work_info__shift_id__employee_shift",
        "employee_work_info__work_type_id": "employee_work_info__work_type_id__work_type",
        "employee_work_info__reporting_manager_id": "employee_work_info__reporting_manager_id__get_full_name",
        "employee_work_info__employee_type_id": "employee_work_info__employee_type_id__employee_type",
        "employee_work_info__payroll_group_id": "employee_work_info__payroll_group_id__name",
        "employee_work_info__branch_id": "employee_work_info__branch_id__branch",
        "employee_work_info__cost_center_id": "employee_work_info__cost_center_id__name",
        "employee_work_info__business_unit_id": "employee_work_info__business_unit_id__name",
    }
    employees = EmployeeFilter(request.GET).qs
    employees = filtersubordinatesemployeemodel(
        request, employees, "employee.view_employee"
    )
    selected_fields = request.GET.getlist("selected_fields")
    if not selected_fields:
        selected_fields = form.fields["selected_fields"].initial
        ids = request.GET.get("ids")
        id_list = json.loads(ids)
        employees = Employee.objects.filter(id__in=id_list)

    reverse_fk_relations = {"employee_bank_details"}
    all_relation_fields = list(set(f.split("__")[0] for f in selected_fields if "__" in f))
    select_fields = [f for f in all_relation_fields if f not in reverse_fk_relations]
    prefetch_fields = [f for f in all_relation_fields if f in reverse_fk_relations]
    if select_fields:
        employees = employees.select_related(*select_fields)
    if prefetch_fields:
        employees = employees.prefetch_related(*prefetch_fields)

    for value, key in excel_columns:
        if value in selected_fields:
            selected_columns.append((value, key))

    date_format = "YYYY-MM-DD"
    user = request.user
    emp = getattr(user, "employee_get", None)
    if emp:
        info = EmployeeWorkInformation.objects.filter(employee_id=emp).first()
        if info:
            company = Company.objects.filter(company=info.company_id).first()
            if company and company.date_format:
                date_format = company.date_format

    bank_detail_fields = [
        (f.split("__", 1)[1], lbl)
        for f, lbl in selected_columns
        if f.startswith("employee_bank_details__")
    ]
    has_insurance = any(f == "employee_insurance" for f, _ in selected_columns)

    employee_ids = list(employees.values_list("id", flat=True))
    all_insurance_names = []

    if has_insurance and employee_ids:
        _seen_ins = {}
        for _name in (
            EmployeeInsurance.objects.filter(employee_id__in=employee_ids)
            .values_list("name", flat=True)
            .order_by("name")
        ):
            _key = _name.strip().lower()
            if _key not in _seen_ins:
                _seen_ins[_key] = _name.strip()
        all_insurance_names = sorted(_seen_ins.values(), key=str.lower)

    employees_data = {}
    bank_cols_added = False
    for col_value, col_name in selected_columns:
        if col_value.startswith("employee_bank_details__"):
            if not bank_cols_added:
                employees_data["Bank Name"] = []
                employees_data["Account Number"] = []
                bank_cols_added = True
        elif col_value == "employee_insurance":
            for ins_name in all_insurance_names:
                employees_data[f"{ins_name} - Start Date"] = []
                employees_data[f"{ins_name} - End Date"] = []
        else:
            employees_data[col_name] = []

    today = date.today()
    date_fmt = HORILLA_DATE_FORMATS.get(date_format, "%Y-%m-%d")
    for employee in employees:
        # Cache bank accounts and insurances per employee (primary first).
        emp_banks = list(employee.employee_bank_details.select_related("bank").order_by("-is_primary", "id")) if bank_detail_fields else []
        emp_insurances = list(employee.employee_insurance.all()) if has_insurance else []
        bank_written = False

        for column_value, column_name in selected_columns:
            if column_value == "age":
                if employee.dob:
                    age_val = today.year - employee.dob.year - (
                        (today.month, today.day) < (employee.dob.month, employee.dob.day)
                    )
                    data = str(age_val)
                else:
                    data = ""
                employees_data[column_name].append(data)
                continue

            if column_value == "years_of_service":
                work_info = getattr(employee, "employee_work_info", None)
                if work_info and work_info.date_joining:
                    delta = today - work_info.date_joining
                    data = str(delta.days // 365)
                else:
                    data = ""
                employees_data[column_name].append(data)
                continue

            if column_value == "employee_work_info__tags":
                work_info = getattr(employee, "employee_work_info", None)
                if work_info:
                    data = ", ".join(str(tag) for tag in work_info.tags.all())
                else:
                    data = ""
                employees_data[column_name].append(data)
                continue

            if column_value == "employee_insurance":
                for ins_name in all_insurance_names:
                    ins = next((i for i in emp_insurances if i.name.strip().lower() == ins_name.lower()), None)
                    employees_data[f"{ins_name} - Start Date"].append(
                        ins.start_date.strftime(date_fmt) if ins and ins.start_date else ""
                    )
                    employees_data[f"{ins_name} - End Date"].append(
                        ins.end_date.strftime(date_fmt) if ins and ins.end_date else ""
                    )
                continue

            if column_value.startswith("employee_bank_details__"):
                if not bank_written:
                    primary_bank = emp_banks[0] if emp_banks else None
                    employees_data["Bank Name"].append(primary_bank.bank_name if primary_bank else "")
                    employees_data["Account Number"].append(str(primary_bank.account_number or "") if primary_bank else "")
                    bank_written = True
                continue

            if column_value in field_overrides:
                column_value = field_overrides[column_value]

            nested_attrs = column_value.split("__")
            value = employee
            for attr in nested_attrs:
                value = getattr(value, attr, None)
                if value is None:
                    break

            # Call the value if it's employee_work_info__reporting_manager_id__get_full_name
            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = ""

            data = str(value) if value is not None else ""

            if isinstance(value, date):
                try:
                    data = value.strftime(date_fmt)
                except Exception:
                    data = str(value)

            if data == "True":
                data = _("Yes")
            elif data == "False":
                data = _("No")

            employees_data[column_name].append(data)
    data_frame = pd.DataFrame(data=employees_data)
    response = HttpResponse(content_type="application/ms-excel")
    response["Content-Disposition"] = 'attachment; filename="employee_export.xlsx"'
    data_frame.to_excel(response, index=False)

    return response


def birthday():
    """
    This method is used to find upcoming birthday and returns the queryset
    """
    today = datetime.now().date()
    last_day_of_month = calendar.monthrange(today.year, today.month)[1]
    employees = Employee.objects.filter(
        is_active=True,
        dob__day__gte=today.day,
        dob__month=today.month,
        dob__day__lte=last_day_of_month,
    ).select_related(
        "employee_work_info__department_id",
        "employee_work_info__job_position_id",
    ).order_by(F("dob__day").asc(nulls_last=True))

    for employee in employees:
        employee.days_until_birthday = employee.dob.day - today.day
    return employees


@login_required
@enter_if_accessible(feature="birthday_view", perm="employee.view_employee")
def get_employees_birthday(request):
    """
    Render all upcoming birthday employee details for the dashboard.
    """
    employees = birthday()
    default_avatar_url = "https://ui-avatars.com/api/?background=random&name="
    birthdays = [
        {
            "profile": (
                emp.get_avatar()
                if hasattr(emp, "get_avatar")
                else f"{default_avatar_url}{emp.employee_first_name}+{emp.employee_last_name}"
            ),
            "name": f"{emp.employee_first_name} {emp.employee_last_name}",
            "dob": emp.dob.strftime("%d %b"),
            "daysUntilBirthday": (
                _("Today")
                if emp.days_until_birthday == 0
                else (
                    _("Tomorrow")
                    if emp.days_until_birthday == 1
                    else f"In {emp.days_until_birthday} Days"
                )
            ),
            "department": (
                emp.get_department().department if emp.get_department() else ""
            ),
            "job_position": (
                emp.get_job_position().job_position if emp.get_job_position() else ""
            ),
        }
        for emp in employees
    ]
    return render(
        request, "dashboard/birthdays_container.html", {"birthdays": birthdays}
    )


@login_required
@manager_can_enter("employee.view_employee")
def dashboard(request):
    """
    This method is used to render individual dashboard for employee module
    """
    upcoming_birthdays = birthday()
    employees = Employee.objects.all()
    employees = filtersubordinates(request, employees, "employee.view_employee")
    active_employees = employees.filter(is_active=True)
    inactive_employees = employees.filter(is_active=False)
    active_ratio = 0
    inactive_ratio = 0
    if employees.exists():
        active_ratio = f"{(len(active_employees) / len(employees)) * 100:.1f}"
        inactive_ratio = f"{(len(inactive_employees) / len(employees)) * 100:.1f}"

    return render(
        request,
        "employee/dashboard/dashboard_employee.html",
        {
            "birthdays": upcoming_birthdays,
            "active_employees": len(active_employees),
            "inactive_employees": len(inactive_employees),
            "total_employees": len(employees),
            "active_ratio": active_ratio,
            "inactive_ratio": inactive_ratio,
        },
    )


@login_required
def total_employees_count(request):
    employees = Employee.objects.all().count()
    return HttpResponse(employees)


@login_required
def joining_today_count(request):
    newbies_today = 0
    if apps.is_installed("recruitment"):
        Candidate = get_horilla_model_class(app_label="recruitment", model="candidate")
        newbies_today = Candidate.objects.filter(
            joining_date__range=[date.today(), date.today() + timedelta(days=1)],
            is_active=True,
        ).count()
    return HttpResponse(newbies_today)


@login_required
def joining_week_count(request):
    newbies_week = 0
    if apps.is_installed("recruitment"):
        Candidate = get_horilla_model_class(app_label="recruitment", model="candidate")
        newbies_week = Candidate.objects.filter(
            joining_date__range=[
                date.today() - timedelta(days=date.today().weekday()),
                date.today() + timedelta(days=6 - date.today().weekday()),
            ],
            is_active=True,
            hired=True,
        ).count()
    return HttpResponse(newbies_week)


@login_required
def dashboard_employee(request):
    """
    Active and in-active employee dashboard
    """
    labels = [
        _("Active"),
        _("In-Active"),
    ]
    employee_counts = {
        item["is_active"]: item["total"]
        for item in Employee.objects.values("is_active").annotate(total=Count("id"))
    }
    response = {
        "dataSet": [
            {
                "label": _("Employees"),
                "data": [
                    employee_counts.get(True, 0),
                    employee_counts.get(False, 0),
                ],
            },
        ],
        "labels": labels,
    }
    return JsonResponse(response)


@login_required
def dashboard_employee_gender(request):
    """
    This method is used to filter out gender vise employees
    """
    labels = [_("Male"), _("Female"), _("Other")]
    gender_counts = {
        item["gender"]: item["total"]
        for item in Employee.objects.filter(is_active=True)
        .values("gender")
        .annotate(total=Count("id"))
    }

    response = {
        "dataSet": [
            {
                "label": _("Employees"),
                "data": [
                    gender_counts.get("male", 0),
                    gender_counts.get("female", 0),
                    gender_counts.get("other", 0),
                ],
            },
        ],
        "labels": labels,
    }
    return JsonResponse(response)


@login_required
def dashboard_employee_department(request):
    """
    This method is used to find the count of employees corresponding to the departments
    """
    labels = []
    count = []
    departments = (
        EmployeeWorkInformation.objects.filter(
            employee_id__is_active=True,
            department_id__isnull=False,
        )
        .values("department_id__department")
        .annotate(total=Count("employee_id"))
        .order_by("department_id__department")
    )
    for dept in departments:
        labels.append(dept["department_id__department"])
        count.append(dept["total"])
    response = {
        "dataSet": [{"label": "Department", "data": count}],
        "labels": labels,
        "message": _("No Data Found..."),
    }
    return JsonResponse(response)


@login_required
def widget_filter(request):
    """
    This method is used to return all the ids of the employees
    """
    ids = EmployeeFilter(request.GET).qs.values_list("id", flat=True)
    return JsonResponse({"ids": list(ids)})


@login_required
def employee_select(request):
    """
    This method is used to return all the id of the employees to select the employee row
    """
    page_number = request.GET.get("page")
    employees = Employee.objects.filter()
    if page_number == "all":
        employees = Employee.objects.filter(is_active=True)

    employee_ids = list(employees.values_list("id", flat=True))
    total_count = employees.count()

    context = {"employee_ids": employee_ids, "total_count": total_count}

    return JsonResponse(context, safe=False)


@login_required
@manager_can_enter("employee.view_employee")
def employee_select_filter(request):
    """
    This method is used to return all the ids of the filtered employees
    """
    page_number = request.GET.get("page")
    if page_number == "all":
        employee_filter = EmployeeFilter(
            request.GET, queryset=Employee.objects.filter()
        )

        filtered_employees = filtersubordinatesemployeemodel(
            request=request, queryset=employee_filter.qs, perm="employee.view_employee"
        )
        employee_ids = list(filtered_employees.values_list("id", flat=True))
        total_count = filtered_employees.count()

        context = {"employee_ids": employee_ids, "total_count": total_count}

        return JsonResponse(context)


@login_required
@hx_request_required
@manager_can_enter(perm="employee.view_employeenote")
def note_tab(request, emp_id):
    """
    This function is used to view note tab of an employee in employee individual
    & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return note-tab template

    """
    employee_obj = Employee.objects.get(id=emp_id)
    notes = EmployeeNote.objects.filter(employee_id=emp_id).order_by("-id")

    return render(
        request,
        "tabs/note_tab.html",
        {"employee": employee_obj, "notes": notes},
    )


@login_required
@hx_request_required
@manager_can_enter(perm="employee.add_employeenote")
def add_note(request, emp_id=None):
    """
    This method renders template component to add candidate remark
    """

    form = EmployeeNoteForm(initial={"employee_id": emp_id})
    if request.method == "POST":
        form = EmployeeNoteForm(
            request.POST,
            request.FILES,
        )

        if form.is_valid():
            note, attachment_ids = form.save(commit=False)
            employee = Employee.objects.get(id=emp_id)
            note.employee_id = employee
            note.updated_by = request.user.employee_get
            note.save()
            note.note_files.set(attachment_ids)
            messages.success(request, _("Note added successfully.."))
            return redirect(f"/employee/note-tab/{emp_id}")

    employee_obj = Employee.objects.get(id=emp_id)
    return render(
        request,
        "tabs/add_note.html",
        {
            "employee": employee_obj,
            "form": form,
        },
    )


@login_required
@manager_can_enter(perm="employee.change_employeenote")
def employee_note_update(request, note_id):
    """
    This method is used to update the note
    Args:
        id : stage note instance id
    """

    note = EmployeeNote.objects.get(id=note_id)

    form = EmployeeNoteForm(instance=note)
    if request.POST:
        form = EmployeeNoteForm(request.POST, instance=note)
        if form.is_valid():
            form.save()
            messages.success(request, _("Note updated successfully..."))
            response = render(
                request,
                "tabs/update_note.html",
                {"form": form},
            )
            return HttpResponse(
                response.content.decode("utf-8") + "<script>location.reload();</script>"
            )
    return render(
        request,
        "tabs/update_note.html",
        {
            "form": form,
        },
    )


@login_required
@manager_can_enter(perm="employee.delete_employeenote")
def employee_note_delete(request, note_id):
    """
    This method is used to delete the note
    Args:
        id : stage note instance id
    """

    note = EmployeeNote.objects.get(id=note_id)
    note.delete()
    messages.success(request, _("Note deleted successfully."))
    return HttpResponse()


@login_required
@hx_request_required
@manager_can_enter(perm="employee.add_notefiles")
def add_more_employee_files(request, note_id):
    """
    This method is used to Add more files to the Employee note.
    Args:
        id : stage note instance id
    """
    note = EmployeeNote.objects.get(id=note_id)
    employee_id = note.employee_id.id

    if request.method == "POST":
        files = request.FILES.getlist("files")

        for file in files:
            ext = os.path.splitext(file.name)[1].lower()

            # Block dangerous file types
            if ext in BLOCKED_EXTENSIONS:
                messages.error(
                    request, f"File type {ext} is not allowed for security reasons."
                )
                continue  # skip this file

            instance = NoteFiles.objects.create(files=file)
            note.note_files.add(instance.id)
    return redirect(f"/employee/note-tab/{employee_id}")


@login_required
@hx_request_required
@manager_can_enter(perm="employee.delete_notefiles")
def delete_employee_note_file(request, note_file_id):
    """
    This method is used to delete the stage note file
    Args:
        id : stage file instance id
    """
    file = NoteFiles.objects.get(id=note_file_id)
    file.delete()
    return HttpResponse()


@login_required
@hx_request_required
@owner_can_enter("employee.view_bonuspoint", Employee)
def bonus_points_tab(request, emp_id):
    """
    This function is used to view Bonus Points tab of an employee in employee individual
    & profile view.

    Parameters:
    request (HttpRequest): The HTTP request object.
    emp_id (int): The id of the employee.

    Returns: return bonus_points template

    """
    employee_obj = Employee.objects.get(id=emp_id)
    try:
        points = BonusPoint.objects.get(employee_id=emp_id)
        if apps.is_installed("payroll"):
            Reimbursement = get_horilla_model_class(
                app_label="payroll", model="reimbursement"
            )
            requested_bonus_points = Reimbursement.objects.filter(
                employee_id=emp_id, type="bonus_encashment", status="requested"
            )
        else:
            requested_bonus_points = QuerySet().none()
        trackings = points.tracking()
        activity_list = []
        for history in trackings:
            activity_list.append(
                {
                    "type": history["type"],
                    "date": history["pair"][0].history_date,
                    "points": history["pair"][0].points - history["pair"][1].points,
                    "user": getattr(
                        User.objects.filter(
                            id=history["pair"][0].history_user_id
                        ).first(),
                        "employee_get",
                        None,
                    ),
                    "reason": history["pair"][0].reason,
                }
            )
        for requested in requested_bonus_points:
            activity_list.append(
                {
                    "type": "requested",
                    "date": requested.created_at,
                    "points": requested.bonus_to_encash,
                    "user": employee_obj.employee_user_id,
                    "reason": "Redeemed points",
                }
            )
        activity_list = sorted(activity_list, key=lambda x: x["date"], reverse=True)
        context = {
            "employee": employee_obj,
            "points": points,
            "activity_list": activity_list,
        }
    except ObjectDoesNotExist:
        context = {
            "employee": employee_obj,
            "points": None,
            "activity_list": [],
        }
    return render(
        request,
        "tabs/bonus_points.html",
        context,
    )


@login_required
@manager_can_enter(perm="employee.add_bonuspoint")
def add_bonus_points(request, emp_id):
    """
    This function is used to add bonus points to an employee

    Args:
        request (HttpRequest): The HTTP request object.
        emp_id (int): The id of the employee.

    Returns: returns add_points form
    """

    bonus_point = BonusPoint.objects.get(employee_id=emp_id)
    form = BonusPointAddForm()
    if request.method == "POST":
        form = BonusPointAddForm(
            request.POST,
            request.FILES,
        )
        if form.is_valid():
            form.save(commit=False)
            bonus_point.points += form.cleaned_data["points"]
            bonus_point.reason = form.cleaned_data["reason"]
            bonus_point.save()
            messages.success(
                request,
                _("Added {} points to the bonus account").format(
                    form.cleaned_data["points"]
                ),
            )
            return HorillaRedirect(request)

    return render(
        request,
        "tabs/forms/add_points.html",
        {
            "form": form,
            "emp_id": emp_id,
        },
    )


@login_required
@owner_can_enter("employee.view_bonuspoint", Employee)
def redeem_points(request, emp_id):
    """
    This function is used to redeem bonus points for an employee

    Args:
        request (HttpRequest): The HTTP request object.
        emp_id (int): The id of the employee.

    Returns: returns redeem_points_form form
    """
    employee = Employee.objects.get(id=emp_id)
    avialable_points = 0
    if BonusPoint.objects.filter(employee_id=employee).exists():
        avialable_points = (
            BonusPoint.objects.filter(employee_id=employee).first().points
        )
    form = BonusPointRedeemForm(initial={"points": avialable_points})
    form.instance.employee_id = employee

    amount_for_bonus_point = 0
    if apps.is_installed("payroll"):
        EncashmentGeneralSettings = get_horilla_model_class(
            app_label="payroll", model="encashmentgeneralsettings"
        )
        amount_for_bonus_point = (
            EncashmentGeneralSettings.objects.first().bonus_amount
            if EncashmentGeneralSettings.objects.first()
            else 1
        )
    if request.method == "POST":
        form = BonusPointRedeemForm(request.POST)
        form.instance.employee_id = employee
        if form.is_valid():
            form.save(commit=False)
            points = form.cleaned_data["points"]
            amount = amount_for_bonus_point * points
            if apps.is_installed("payroll"):
                Reimbursement = get_horilla_model_class(
                    app_label="payroll", model="reimbursement"
                )
                Reimbursement.objects.create(
                    title=f"Bonus point Redeem for {employee}",
                    type="bonus_encashment",
                    employee_id=employee,
                    bonus_to_encash=points,
                    amount=amount,
                    description=f"{employee} want to redeem {points} points",
                    allowance_on=date.today(),
                )
            return HorillaRedirect(request)
    return render(
        request,
        "tabs/forms/redeem_points_form.html",
        {
            "form": form,
            "employee": employee,
        },
    )


@login_required
def organisation_chart(request):
    """
    This method is used to view oganisation chart
    """
    selected_company = request.session.get("selected_company")
    if (
        request.GET.get("employee_work_info__company_id") == None
        and selected_company != "all"
    ):
        reporting_managers = Employee.objects.filter(
            is_active=True,
            reporting_manager__isnull=False,
            employee_work_info__company_id=selected_company,
        ).distinct()
    else:
        reporting_managers = Employee.objects.filter(
            is_active=True,
            reporting_manager__isnull=False,
        ).distinct()

    # Iterate through the queryset and add reporting manager id and name to the dictionary
    result_dict = {item.id: item.get_full_name() for item in reporting_managers}

    entered_req_managers = []

    # Helper function to recursively create the hierarchy structure
    def create_hierarchy(manager):
        """
        Hierarchy generator method
        """
        nodes = []
        # check the manager is a reporting manager if yes, store it into entered_req_managers
        if manager.id in result_dict.keys():
            entered_req_managers.append(manager)
        # filter the subordinates
        subordinates = Employee.objects.filter(
            is_active=True, employee_work_info__reporting_manager_id=manager
        ).exclude(id=manager.id)

        # itrating through subordinates
        for employee in subordinates:
            if employee in entered_req_managers:
                continue
            # check the employee is a reporting manager if yes,remove className store
            # it into entered_req_managers
            if employee.id in result_dict.keys():
                nodes.append(
                    {
                        "name": employee.get_full_name(),
                        "title": getattr(
                            employee.get_job_position(), "job_position", _("Not set")
                        ),
                        "children": create_hierarchy(employee),
                    }
                )
                entered_req_managers.append(employee)

            else:
                nodes.append(
                    {
                        "name": employee.get_full_name(),
                        "title": getattr(
                            employee.get_job_position(), "job_position", _("Not set")
                        ),
                        "className": "middle-level",
                        "children": create_hierarchy(employee),
                    }
                )
        return nodes

    selected_company = request.session.get("selected_company")
    if (
        request.GET.get("employee_work_info__company_id") == None
        and selected_company != "all"
    ):
        reporting_managers = Employee.objects.filter(
            is_active=True,
            reporting_manager__isnull=False,
            employee_work_info__company_id=selected_company,
        ).distinct()
    else:
        reporting_managers = Employee.objects.filter(
            is_active=True, reporting_manager__isnull=False
        ).distinct()

    manager = request.user.employee_get

    if len(reporting_managers) == 0:
        new_dict = {}
    else:
        new_dict = {reporting_managers[0].id: _("My view"), **result_dict}
    # POST method is used to change the reporting manager
    if request.method == "POST":
        if request.POST.get("manager_id"):
            manager_id = int(request.POST.get("manager_id"))
            manager = Employee.objects.get(id=manager_id)
        node = {
            "name": manager.get_full_name(),
            "title": getattr(manager.get_job_position(), "job_position", _("Not set")),
            "children": create_hierarchy(manager),
        }
        context = {"act_datasource": node}
        return render(request, "organisation_chart/chart.html", context=context)

    node = {
        "name": manager.get_full_name(),
        "title": getattr(manager.get_job_position(), "job_position", _("Not set")),
        "children": create_hierarchy(manager),
    }

    context = {
        "act_datasource": node,
        "reporting_manager_dict": new_dict,
        "act_manager_id": manager.id,
    }
    return render(request, "organisation_chart/org_chart.html", context=context)


@login_required
@permission_required("payroll.add_encashmentgeneralsettings")
def encashment_condition_create(request):
    """
    Handle the creation and updating of encashment general settings.
    """
    if apps.is_installed("payroll"):
        from payroll.forms.forms import EncashmentGeneralSettingsForm

        EncashmentGeneralSettings = get_horilla_model_class(
            app_label="payroll", model="encashmentgeneralsettings"
        )
        instance = (
            EncashmentGeneralSettings.objects.first()
            if apps.is_installed("payroll")
            else QuerySet().none()
        )

        if request.method == "POST":
            encashment_form = EncashmentGeneralSettingsForm(
                request.POST, instance=instance
            )
            if encashment_form.is_valid():
                encashment_form.save()
                messages.success(request, _("Settings updated."))
                return HorillaRedirect(request)
        else:
            encashment_form = EncashmentGeneralSettingsForm(instance=instance)

        return render(
            request,
            "settings/encashment_settings.html",
            {"encashment_form": encashment_form},
        )

    messages.warning(request, _("Payroll app not installed"))
    return HorillaRedirect(request)


@login_required
@permission_required("employee.add_employeegeneralsetting")
def initial_prefix(request):
    """
    This method is used to set the initial prefix using a form.
    """
    instance = EmployeeGeneralSetting.objects.first()  # Get the first instance or None
    if not instance:
        instance = EmployeeGeneralSetting()  # Create a new instance if none exists

    if request.method == "POST":
        form = EmployeeGeneralSettingPrefixForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, _("Initial prefix updated successfully."))
            return HorillaRedirect(request)
        else:
            messages.error(request, "There was an error updating the prefix.")
    else:
        form = EmployeeGeneralSettingPrefixForm(instance=instance)

    return render(request, "settings/settings.html", {"prefix_form": form})


@login_required
@manager_can_enter("employee.view_employee")
def first_last_employee_no(request):
    """
    This method is used to return the first last employee nos in grouped and ordered
    """
    employee_nos = get_ordered_employee_nos()

    return render(
        request,
        "employee_personal_info/first_last_badge.html",
        {"employee_nos": employee_nos},
    )


@login_required
@hx_request_required
@manager_can_enter("employee.view_employee")
def employee_get_mail_log(request):
    """
    This method is used to track mails sent along with the status
    """
    employee_id = request.GET["emp_id"]
    employee = Employee.objects.get(id=employee_id)
    tracked_mails = EmailLog.objects.filter(to__icontains=employee.email)
    try:
        if employee.employee_work_info and employee.employee_work_info.email:
            tracked_mails = tracked_mails | EmailLog.objects.filter(
                to__icontains=employee.employee_work_info.email
            )
        tracked_mails = tracked_mails.order_by("-created_at")

        return render(request, "tabs/mail_log.html", {"tracked_mails": tracked_mails})
    except ObjectDoesNotExist:
        return render(request, "tabs/mail_log.html", {"tracked_mails": []})


@login_required
def get_job_positions(request):
    department_id = request.GET.get("department_id")
    job_positions = (
        JobPosition.objects.filter(department_id=department_id).values_list(
            "id", "job_position"
        )
        if department_id
        else []
    )
    return JsonResponse({"job_positions": dict(job_positions)})


@login_required
def get_job_roles(request):
    """
    Retrieve job roles associated with a specific job position.

    This view function extracts the job_id from the GET request, queries the
    JobRole model for job roles that match the provided job_position_id, and
    returns the results as a JSON response.
    """
    job_id = request.GET.get("job_id")
    job_roles = JobRole.objects.filter(job_position_id=job_id).values_list(
        "id", "job_role"
    )
    return JsonResponse({"job_roles": dict(job_roles)})


@login_required
@permission_required("employee.view_employeetag")
def employee_tag_view(request):
    """
    This method is used to Employee tags
    """
    employeetags = EmployeeTag.objects.all()
    return render(
        request,
        "base/tags/employee_tags.html",
        {"employeetags": employeetags},
    )


@login_required
@hx_request_required
@permission_required("employee.add_employeetag")
def employee_tag_create(request):
    """
    This method renders form and template to create Ticket type
    """
    form = EmployeeTagForm()
    if request.method == "POST":
        form = EmployeeTagForm(request.POST)
        if form.is_valid():
            form.save()
            form = EmployeeTagForm()
            messages.success(request, _("Tag has been created successfully!"))
    return render(
        request,
        "base/employee_tag/employee_tag_form.html",
        {
            "form": form,
        },
    )


@login_required
@hx_request_required
@permission_required("employee.add_employeetag")
def employee_tag_update(request, tag_id):
    """
    This method renders form and template to create Ticket type
    """
    tag = EmployeeTag.objects.get(id=tag_id)
    form = EmployeeTagForm(instance=tag)
    if request.method == "POST":
        form = EmployeeTagForm(request.POST, instance=tag)
        if form.is_valid():
            form.save()
            form = EmployeeTagForm()
            messages.success(request, _("Tag has been updated successfully!"))
            return HorillaRedirect(request)
    return render(
        request,
        "base/employee_tag/employee_tag_form.html",
        {"form": form, "tag_id": tag_id},
    )


@login_required
@require_http_methods(["POST"])
def send_pin_to_email(request, obj_id):
    """
    Send the employee's work info PIN to their email address.
    """
    employee = get_object_or_404(Employee, id=obj_id)
    if not (
        request.user.has_perm("employee.add_employee")
        or getattr(employee.employee_work_info, "reporting_manager_id", None)
        == request.user.employee_get
    ):
        messages.error(request, _("You do not have permission to perform this action."))
        return redirect(request.META.get("HTTP_REFERER", "employee-view"))

    work_info = getattr(employee, "employee_work_info", None)
    pin = getattr(work_info, "pin", None) if work_info else None

    if not pin:
        messages.warning(request, _("No PIN has been set for this employee."))
        return redirect(request.META.get("HTTP_REFERER", "employee-view"))

    send_to_mail = (
        work_info.email
        if work_info and work_info.email
        else employee.email
    )

    if not send_to_mail:
        messages.error(request, _("No email address found for this employee."))
        return redirect(request.META.get("HTTP_REFERER", "employee-view"))

    portal_url = request.build_absolute_uri(reverse("public-portal"))
    subject = _("MDC ATTENDANCE PIN")
    body = f"""
    <p>Hello {employee.get_full_name()},</p>
    <p>Your 6-digit PIN is: <strong>{pin}</strong></p>
    <p>Attendance Portal: <a href="{portal_url}">{portal_url}</a></p>
    <p>Please keep this PIN confidential.</p>
    """
    email = EmailMessage(subject=str(subject), body=body, to=[send_to_mail])
    email.content_subtype = "html"
    try:
        email.send()
        messages.success(
            request,
            _("PIN sent to %(email)s") % {"email": send_to_mail},
        )
    except Exception:
        messages.error(request, _("Failed to send PIN email. Please check email configuration."))

    return redirect(request.META.get("HTTP_REFERER", "employee-view"))


# ---------------------------------------------------------------------------
# Employee Self-Service Onboarding Portal
# ---------------------------------------------------------------------------


def _employee_portal_step_redirect(portal):
    """Return a redirect to the next incomplete portal step, or None if at step 0."""
    token = portal.token
    if portal.count >= 3:
        return redirect("employee-portal-pin", token)
    if portal.count == 2:
        return redirect("employee-portal-personal", token)
    if portal.count == 1:
        return redirect("employee-portal-profile", token)
    return None


@login_required
def send_employee_portal_link(request, obj_id):
    """Send a self-service portal link to an employee so they can set their password
    and fill in their own profile data."""
    employee = get_object_or_404(Employee, pk=obj_id)
    if not (
        request.user.has_perm("employee.add_employee")
        or getattr(employee.employee_work_info, "reporting_manager_id", None)
        == request.user.employee_get
    ):
        messages.error(request, _("You do not have permission to perform this action."))
        return HorillaRedirect(request)

    if request.method == "POST":
        token = secrets.token_hex(15)
        portal, created = EmployeeOnboardingPortal.objects.get_or_create(
            employee_id=employee,
            defaults={"token": token},
        )
        if not created:
            portal.token = token
            portal.used = False
            portal.count = 0
            portal.save()

        protocol = "https" if request.is_secure() else "http"
        host = request.get_host()
        portal_url = f"{protocol}://{host}/employee/employee-portal/set-password/{token}"
        attendance_portal_url = request.build_absolute_uri(reverse("public-portal"))

        send_to = (
            getattr(getattr(employee, "employee_work_info", None), "email", None)
            or employee.email
        )

        work_info = getattr(employee, "employee_work_info", None)
        company_obj = getattr(work_info, "company_id", None)
        company_name = getattr(company_obj, "company", None) or "HR Portal"
        logo_url = None
        if company_obj and company_obj.icon:
            raw = company_obj.icon.url
            logo_url = raw if raw.startswith(("http://", "https://")) else f"{protocol}://{host}{raw}"
        if not logo_url:
            logo_url = get_hq_company_logo_url(host=host, protocol=protocol)

        html_message = render_to_string(
            "employee/portal/email_template.html",
            {
                "employee": employee,
                "portal_url": portal_url,
                "attendance_portal_url": attendance_portal_url,
                "host": host,
                "protocol": protocol,
                "logo_url": logo_url,
                "company_name": company_name,
            },
            request=request,
        )
        email_msg = EmailMessage(
            subject=_("Complete Your Employee Profile"),
            body=html_message,
            to=[send_to],
        )
        email_msg.content_subtype = "html"
        try:
            email_msg.send()
            portal.sent_at = timezone.now()
            portal.save()
            messages.success(request, _("Portal link sent to %(email)s") % {"email": send_to})
        except Exception as e:
            logger.error(e)
            messages.error(request, _("Failed to send portal link. Please check email configuration."))

    return HorillaRedirect(request)


@login_required
@permission_required("employee.add_employee")
def send_bulk_portal_link(request):
    """Send the self-service onboarding portal link to multiple selected employees."""
    if request.method != "POST":
        return HttpResponse(status=405)

    ids = json.loads(request.POST.get("ids", "[]"))
    success_count = 0
    error_count = 0

    for emp_id in ids:
        employee = Employee.objects.filter(id=emp_id).first()
        if not employee:
            continue

        token = secrets.token_hex(15)
        portal, created = EmployeeOnboardingPortal.objects.get_or_create(
            employee_id=employee,
            defaults={"token": token},
        )
        if not created:
            portal.token = token
            portal.used = False
            portal.count = 0
            portal.save()

        protocol = "https" if request.is_secure() else "http"
        host = request.get_host()
        portal_url = f"{protocol}://{host}/employee/employee-portal/set-password/{token}"
        attendance_portal_url = request.build_absolute_uri(reverse("public-portal"))

        send_to = (
            getattr(getattr(employee, "employee_work_info", None), "email", None)
            or employee.email
        )
        if not send_to:
            error_count += 1
            continue

        work_info = getattr(employee, "employee_work_info", None)
        company_obj = getattr(work_info, "company_id", None)
        company_name = getattr(company_obj, "company", None) or "HR Portal"
        logo_url = None
        if company_obj and company_obj.icon:
            raw = company_obj.icon.url
            logo_url = raw if raw.startswith(("http://", "https://")) else f"{protocol}://{host}{raw}"
        if not logo_url:
            logo_url = get_hq_company_logo_url(host=host, protocol=protocol)

        html_message = render_to_string(
            "employee/portal/email_template.html",
            {
                "employee": employee,
                "portal_url": portal_url,
                "attendance_portal_url": attendance_portal_url,
                "host": host,
                "protocol": protocol,
                "logo_url": logo_url,
                "company_name": company_name,
            },
            request=request,
        )
        email_msg = EmailMessage(
            subject=_("Complete Your Employee Profile"),
            body=html_message,
            to=[send_to],
        )
        email_msg.content_subtype = "html"
        try:
            email_msg.send()
            portal.sent_at = timezone.now()
            portal.save(update_fields=["sent_at"])
            success_count += 1
        except Exception as e:
            logger.error(e)
            error_count += 1

    if success_count:
        messages.success(
            request,
            _("Portal link sent to %(count)s employee(s).") % {"count": success_count},
        )
    if error_count:
        messages.warning(
            request,
            _("Failed to send portal link to %(count)s employee(s).") % {"count": error_count},
        )
    return HttpResponse(status=200)


@login_required
@permission_required("employee.add_employee")
def send_bulk_password_reset(request):
    """Send a password reset link to multiple selected employees."""
    if request.method != "POST":
        return HttpResponse(status=405)

    from base.backends import ConfiguredEmailBackend
    from django.contrib.auth.forms import PasswordResetForm

    ids = json.loads(request.POST.get("ids", "[]"))
    success_count = 0
    error_count = 0

    email_backend = ConfiguredEmailBackend()
    from_email = getattr(email_backend, "dynamic_from_email_with_display_name", None)

    for emp_id in ids:
        employee = Employee.objects.filter(id=emp_id).first()
        if not employee:
            continue
        user = getattr(employee, "employee_user_id", None)
        if not user:
            error_count += 1
            continue

        form = PasswordResetForm({"email": user.username})
        if form.is_valid():
            opts = {
                "use_https": request.is_secure(),
                "request": request,
            }
            if from_email:
                opts["from_email"] = from_email
            try:
                form.save(**opts)
                success_count += 1
            except Exception as e:
                logger.error(e)
                error_count += 1
        else:
            error_count += 1

    if success_count:
        messages.success(
            request,
            _("Password reset link sent to %(count)s employee(s).") % {"count": success_count},
        )
    if error_count:
        messages.warning(
            request,
            _("Failed to send password reset to %(count)s employee(s).") % {"count": error_count},
        )
    return HttpResponse(status=200)


@login_required
@permission_required("employee.add_employee")
def send_bulk_pin_to_email(request):
    """Send the attendance PIN to multiple selected employees."""
    if request.method != "POST":
        return HttpResponse(status=405)

    ids = json.loads(request.POST.get("ids", "[]"))
    success_count = 0
    error_count = 0

    portal_url = request.build_absolute_uri(reverse("public-portal"))

    for emp_id in ids:
        employee = Employee.objects.filter(id=emp_id).first()
        if not employee:
            continue

        work_info = getattr(employee, "employee_work_info", None)
        pin = getattr(work_info, "pin", None) if work_info else None
        if not pin:
            error_count += 1
            continue

        send_to_mail = (
            work_info.email
            if work_info and work_info.email
            else employee.email
        )
        if not send_to_mail:
            error_count += 1
            continue

        subject = _("MDC ATTENDANCE PIN")
        body = f"""
        <p>Hello {employee.get_full_name()},</p>
        <p>Your 6-digit PIN is: <strong>{pin}</strong></p>
        <p>Attendance Portal: <a href="{portal_url}">{portal_url}</a></p>
        <p>Please keep this PIN confidential.</p>
        """
        email = EmailMessage(subject=str(subject), body=body, to=[send_to_mail])
        email.content_subtype = "html"
        try:
            email.send()
            success_count += 1
        except Exception as e:
            logger.error(e)
            error_count += 1

    if success_count:
        messages.success(
            request,
            _("PIN sent to %(count)s employee(s).") % {"count": success_count},
        )
    if error_count:
        messages.warning(
            request,
            _("Failed to send PIN to %(count)s employee(s).") % {"count": error_count},
        )
    return HttpResponse(status=200)


@login_required
@permission_required("employee.add_employee")
def check_bulk_email_status(request):
    """Return which employees have already received a given email type."""
    if request.method != "POST":
        return HttpResponse(status=405)

    ids = json.loads(request.POST.get("ids", "[]"))
    email_type = request.POST.get("type", "")

    already_sent = []
    not_sent = []
    one_hour_ago = timezone.now() - timedelta(hours=1)

    for emp_id in ids:
        employee = Employee.objects.filter(id=emp_id).first()
        if not employee:
            continue

        sent = False
        if email_type == "portal":
            sent = EmployeeOnboardingPortal.objects.filter(
                employee_id_id=emp_id, sent_at__gte=one_hour_ago
            ).exists()
        elif email_type == "password":
            email_addr = (
                getattr(getattr(employee, "employee_work_info", None), "email", None)
                or employee.email
            )
            if email_addr:
                sent = EmailLog.objects.filter(
                    to__iexact=email_addr,
                    subject__icontains="password",
                    created_at__gte=one_hour_ago,
                ).exists()
        elif email_type == "pin":
            work_info = getattr(employee, "employee_work_info", None)
            email_addr = (
                work_info.email if work_info and work_info.email else employee.email
            )
            if email_addr:
                sent = EmailLog.objects.filter(
                    to__iexact=email_addr,
                    subject__icontains="ATTENDANCE PIN",
                    created_at__gte=one_hour_ago,
                ).exists()
        elif email_type == "website_url":
            email_addr = (
                getattr(getattr(employee, "employee_work_info", None), "email", None)
                or employee.email
            )
            if email_addr:
                sent = EmailLog.objects.filter(
                    to__iexact=email_addr,
                    subject__icontains="HRIS Portal Link",
                    created_at__gte=one_hour_ago,
                ).exists()

        entry = {"id": emp_id, "name": employee.get_full_name()}
        if sent:
            already_sent.append(entry)
        else:
            not_sent.append(entry)

    return JsonResponse({"already_sent": already_sent, "not_sent": not_sent})


@login_required
@permission_required("employee.add_employee")
def send_single_bulk_email(request):
    """Send one bulk email (portal / password-reset / pin) to a single employee."""
    if request.method != "POST":
        return HttpResponse(status=405)

    emp_id = request.POST.get("emp_id")
    email_type = request.POST.get("type", "")

    employee = Employee.objects.filter(id=emp_id).first()
    if not employee:
        return JsonResponse({"success": False, "message": "Employee not found."})

    employee_name = employee.get_full_name()

    try:
        if email_type == "portal":
            token = secrets.token_hex(15)
            portal, created = EmployeeOnboardingPortal.objects.get_or_create(
                employee_id=employee,
                defaults={"token": token},
            )
            if not created:
                portal.token = token
                portal.used = False
                portal.count = 0
                portal.save()

            protocol = "https" if request.is_secure() else "http"
            host = request.get_host()
            portal_url = (
                f"{protocol}://{host}/employee/employee-portal/set-password/{token}"
            )
            attendance_portal_url = request.build_absolute_uri(reverse("public-portal"))

            send_to = (
                getattr(
                    getattr(employee, "employee_work_info", None), "email", None
                )
                or employee.email
            )
            if not send_to:
                return JsonResponse(
                    {"success": False, "employee_name": employee_name,
                     "message": "No email address."}
                )

            work_info = getattr(employee, "employee_work_info", None)
            company_obj = getattr(work_info, "company_id", None)
            company_name = getattr(company_obj, "company", None) or "HR Portal"
            logo_url = None
            if company_obj and company_obj.icon:
                raw = company_obj.icon.url
                logo_url = raw if raw.startswith(("http://", "https://")) else f"{protocol}://{host}{raw}"
            if not logo_url:
                logo_url = get_hq_company_logo_url(host=host, protocol=protocol)

            html_message = render_to_string(
                "employee/portal/email_template.html",
                {
                    "employee": employee,
                    "portal_url": portal_url,
                    "attendance_portal_url": attendance_portal_url,
                    "host": host,
                    "protocol": protocol,
                    "logo_url": logo_url,
                    "company_name": company_name,
                },
                request=request,
            )
            subject = str(_("Complete Your Employee Profile"))
            email_msg = EmailMessage(subject=subject, body=html_message, to=[send_to])
            email_msg.content_subtype = "html"
            email_msg.send()
            portal.sent_at = timezone.now()
            portal.save(update_fields=["sent_at"])
            EmailLog.objects.create(
                subject=subject,
                body=html_message[:255],
                from_email="",
                to=send_to,
                status="sent",
            )
            return JsonResponse(
                {"success": True, "employee_name": employee_name,
                 "message": "Portal link sent."}
            )

        elif email_type == "password":
            from base.backends import ConfiguredEmailBackend
            from django.contrib.auth.forms import PasswordResetForm

            user = getattr(employee, "employee_user_id", None)
            if not user:
                return JsonResponse(
                    {"success": False, "employee_name": employee_name,
                     "message": "No user account."}
                )

            email_backend = ConfiguredEmailBackend()
            from_email = getattr(
                email_backend, "dynamic_from_email_with_display_name", None
            )
            form = PasswordResetForm({"email": user.username})
            if form.is_valid():
                opts = {"use_https": request.is_secure(), "request": request}
                if from_email:
                    opts["from_email"] = from_email
                form.save(**opts)
                send_to = user.username
                subject = "Password Reset"
                EmailLog.objects.create(
                    subject=subject,
                    body="",
                    from_email="",
                    to=send_to,
                    status="sent",
                )
                return JsonResponse(
                    {"success": True, "employee_name": employee_name,
                     "message": "Password reset link sent."}
                )
            return JsonResponse(
                {"success": False, "employee_name": employee_name,
                 "message": "Invalid email."}
            )

        elif email_type == "pin":
            portal_url = request.build_absolute_uri(reverse("public-portal"))
            work_info = getattr(employee, "employee_work_info", None)
            pin = getattr(work_info, "pin", None) if work_info else None
            if not pin:
                return JsonResponse(
                    {"success": False, "employee_name": employee_name,
                     "message": "No PIN set."}
                )

            send_to_mail = (
                work_info.email
                if work_info and work_info.email
                else employee.email
            )
            if not send_to_mail:
                return JsonResponse(
                    {"success": False, "employee_name": employee_name,
                     "message": "No email address."}
                )

            subject = str(_("MDC ATTENDANCE PIN"))
            body = (
                f"<p>Hello {employee.get_full_name()},</p>"
                f"<p>Your 6-digit PIN is: <strong>{pin}</strong></p>"
                f"<p>Attendance Portal: <a href='{portal_url}'>{portal_url}</a></p>"
                f"<p>Please keep this PIN confidential.</p>"
            )
            email_msg = EmailMessage(
                subject=subject, body=body, to=[send_to_mail]
            )
            email_msg.content_subtype = "html"
            email_msg.send()
            EmailLog.objects.create(
                subject=subject,
                body=body[:255],
                from_email="",
                to=send_to_mail,
                status="sent",
            )
            return JsonResponse(
                {"success": True, "employee_name": employee_name,
                 "message": "PIN sent."}
            )

        elif email_type == "website_url":
            protocol = "https" if request.is_secure() else "http"
            host = request.get_host()
            website_url = f"{protocol}://{host}/"

            send_to = (
                getattr(
                    getattr(employee, "employee_work_info", None), "email", None
                )
                or employee.email
            )
            if not send_to:
                return JsonResponse(
                    {"success": False, "employee_name": employee_name,
                     "message": "No email address."}
                )

            work_info = getattr(employee, "employee_work_info", None)
            company_obj = getattr(work_info, "company_id", None)
            company_name = getattr(company_obj, "company", None) or "HR Portal"
            logo_url = None
            if company_obj and company_obj.icon:
                raw = company_obj.icon.url
                logo_url = raw if raw.startswith(("http://", "https://")) else f"{protocol}://{host}{raw}"
            if not logo_url:
                logo_url = get_hq_company_logo_url(host=host, protocol=protocol)

            subject = str(_("HRIS Portal Link"))
            body = render_to_string(
                "employee/portal/website_url_email_template.html",
                {
                    "employee": employee,
                    "website_url": website_url,
                    "company_name": company_name,
                    "logo_url": logo_url,
                    "host": host,
                    "protocol": protocol,
                },
                request=request,
            )
            email_msg = EmailMessage(subject=subject, body=body, to=[send_to])
            email_msg.content_subtype = "html"
            email_msg.send()
            EmailLog.objects.create(
                subject=subject,
                body=body[:255],
                from_email="",
                to=send_to,
                status="sent",
            )
            return JsonResponse(
                {"success": True, "employee_name": employee_name,
                 "message": "Portal link sent."}
            )

        return JsonResponse({"success": False, "message": "Unknown email type."})

    except Exception as exc:
        logger.error(exc)
        return JsonResponse(
            {"success": False, "employee_name": employee_name,
             "message": str(exc)}
        )


@csrf_exempt
def employee_portal_set_password(request, token):
    """Step 1 — Employee sets their own password via the portal link."""
    portal = EmployeeOnboardingPortal.objects.filter(token=token).first()
    if portal is None or portal.used:
        return render(request, "404.html")

    employee = portal.employee_id
    user = employee.employee_user_id
    form = SetPasswordForm(user)

    if request.method == "POST":
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            portal.count = 1
            portal.save()
            messages.success(request, _("Password set successfully."))
            return redirect("employee-portal-profile", token)

    company = None
    if hasattr(employee, "employee_work_info") and employee.employee_work_info:
        company = employee.employee_work_info.company_id

    return render(
        request,
        "employee/portal/set_password.html",
        {"form": form, "employee": employee, "company": company, "token": token},
    )


@csrf_exempt
def employee_portal_profile(request, token):
    """Step 2 — Employee uploads a profile picture."""
    portal = EmployeeOnboardingPortal.objects.filter(token=token).first()
    if portal is None or portal.used:
        return render(request, "404.html")

    if portal.count < 1:
        return redirect("employee-portal-set-password", token)

    employee = portal.employee_id

    if portal.count < 2 and employee.employee_profile:
        portal.count = 2
        portal.save()

    if request.method == "POST":
        profile = request.FILES.get("profile")
        captured = request.POST.get("captured_photo", "")

        if profile is not None:
            employee.employee_profile = profile
            employee.save()
            portal.count = 2
            portal.save()
            messages.success(request, _("Profile picture updated successfully."))
            return redirect("employee-portal-personal", token)

        elif captured and captured.startswith("data:image/"):
            import base64, uuid
            from django.core.files.base import ContentFile
            header, b64data = captured.split(",", 1)
            image_data = base64.b64decode(b64data)
            filename = f"capture_{uuid.uuid4().hex}.jpg"
            employee.employee_profile.save(filename, ContentFile(image_data), save=True)
            portal.count = 2
            portal.save()
            messages.success(request, _("Profile picture updated successfully."))
            return redirect("employee-portal-personal", token)

    company = None
    if hasattr(employee, "employee_work_info") and employee.employee_work_info:
        company = employee.employee_work_info.company_id

    return render(
        request,
        "employee/portal/profile.html",
        {
            "employee": employee,
            "token": token,
            "company": company,
        },
    )


@csrf_exempt
def employee_portal_remove_photo(request, token):
    """Remove the employee's profile photo from the portal."""
    portal = EmployeeOnboardingPortal.objects.filter(token=token).first()
    if portal is None or portal.used:
        return render(request, "404.html")

    if request.method == "POST":
        employee = portal.employee_id
        if employee.employee_profile:
            employee.employee_profile.delete(save=False)
            employee.employee_profile = None
            employee.save()
        if portal.count >= 2:
            portal.count = 1
            portal.save()

    return redirect("employee-portal-profile", token)


@csrf_exempt
def employee_portal_personal(request, token):
    """Step 3 — Employee fills in personal details."""
    portal = EmployeeOnboardingPortal.objects.filter(token=token).first()
    if portal is None or portal.used:
        return render(request, "404.html")

    if portal.count < 2:
        return redirect("employee-portal-profile", token)

    employee = portal.employee_id
    work_info, _created = EmployeeWorkInformation.objects.get_or_create(employee_id=employee)
    form = EmployeePortalPersonalForm(instance=employee)

    if request.method == "POST":
        form = EmployeePortalPersonalForm(request.POST, instance=employee)
        if form.is_valid():
            form.save()
            portal.count = 3
            portal.save()
            messages.success(request, _("Personal details saved successfully."))
            return redirect("employee-portal-bank", token)

    company = work_info.company_id

    return render(
        request,
        "employee/portal/personal_details.html",
        {"form": form, "employee": employee, "company": company, "token": token},
    )


@csrf_exempt
def employee_portal_bank(request, token):
    """Step 4 — Employee fills in bank details."""
    portal = EmployeeOnboardingPortal.objects.filter(token=token).first()
    if portal is None or portal.used:
        return render(request, "404.html")

    if portal.count < 3:
        return redirect("employee-portal-personal", token)

    employee = portal.employee_id
    work_info, _created = EmployeeWorkInformation.objects.get_or_create(employee_id=employee)
    existing_bank = EmployeeBankDetails.objects.filter(employee_id=employee).first()
    form = EmployeeBankDetailsForm(instance=existing_bank)

    if request.method == "POST":
        form = EmployeeBankDetailsForm(request.POST, instance=existing_bank)
        if form.is_valid():
            instance = form.save(commit=False)
            instance.employee_id = employee
            instance.is_primary = True
            instance.save()
            portal.count = 4
            portal.save()
            messages.success(request, _("Bank details saved successfully."))
            return redirect("employee-portal-pin", token)

    return render(
        request,
        "employee/portal/bank_details.html",
        {"form": form, "employee": employee, "company": work_info.company_id, "token": token},
    )


@csrf_exempt
def employee_portal_pin(request, token):
    """Step 5 — Employee sets their attendance portal PIN."""
    portal = EmployeeOnboardingPortal.objects.filter(token=token).first()
    if portal is None or portal.used:
        return render(request, "404.html")

    if portal.count < 4:
        return redirect("employee-portal-bank", token)

    employee = portal.employee_id
    work_info, _created = EmployeeWorkInformation.objects.entire().get_or_create(employee_id=employee)
    form = EmployeePortalPINForm(instance=work_info)

    if request.method == "POST":
        form = EmployeePortalPINForm(request.POST, instance=work_info)
        if form.is_valid():
            form.save()
            portal.count = 5
            portal.used = True
            portal.save()
            user = employee.employee_user_id
            if user is not None:
                if hasattr(user, "is_new_employee"):
                    user.is_new_employee = False
                    user.save(update_fields=["is_new_employee"])
            request.session["portal_completed_employee_id"] = employee.pk
            messages.success(request, _("Portal PIN set successfully."))
            return redirect("employee-portal-done")

    company = work_info.company_id

    return render(
        request,
        "employee/portal/portal_pin.html",
        {"form": form, "employee": employee, "company": company, "token": token},
    )


def employee_portal_done(request):
    """Final page — shown after the employee completes all portal steps."""
    employee_id = request.session.get("portal_completed_employee_id")
    employee = Employee.objects.filter(pk=employee_id).first() if employee_id else None
    work_info = getattr(employee, "employee_work_info", None) if employee else None
    return render(
        request,
        "employee/portal/done.html",
        {"employee": employee, "work_info": work_info},
    )


def employee_portal_download_card(request):
    """Generate and download a PDF ID card for the employee who just completed the portal."""
    import base64
    import io as _io

    import qrcode
    from xhtml2pdf import pisa

    employee_id = request.session.get("portal_completed_employee_id")
    if not employee_id:
        return HttpResponse("Session expired. Please complete the portal again.", status=400)

    employee = Employee.objects.filter(pk=employee_id).first()
    if not employee:
        return HttpResponse("Employee not found.", status=404)

    work_info = getattr(employee, "employee_work_info", None)

    # QR code → base64 PNG
    qr_buf = _io.BytesIO()
    qrcode.make("https://hrms.martindevcorp.com").save(qr_buf, format="PNG")
    qr_b64 = base64.b64encode(qr_buf.getvalue()).decode()

    # Employee photo → base64 (needed for xhtml2pdf inline images)
    photo_b64 = None
    photo_mime = "image/png"
    if employee.employee_profile:
        try:
            from PIL import Image, ImageOps

            with Image.open(employee.employee_profile.path) as photo:
                photo = ImageOps.exif_transpose(photo)
                photo = photo.convert("RGB")

                target_width, target_height = 370, 440
                source_width, source_height = photo.size
                target_ratio = target_width / target_height
                source_ratio = source_width / source_height

                if source_ratio > target_ratio:
                    crop_width = int(source_height * target_ratio)
                    left = (source_width - crop_width) // 2
                    photo = photo.crop((left, 0, left + crop_width, source_height))
                else:
                    crop_height = int(source_width / target_ratio)
                    top = (source_height - crop_height) // 2
                    photo = photo.crop((0, top, source_width, top + crop_height))

                photo = photo.resize((target_width, target_height), Image.Resampling.LANCZOS)
                photo_buf = _io.BytesIO()
                photo.save(photo_buf, format="PNG")
                photo_b64 = base64.b64encode(photo_buf.getvalue()).decode()
        except Exception:
            pass

    html = render_to_string(
        "employee/portal/id_card_pdf.html",
        {
            "employee": employee,
            "work_info": work_info,
            "qr_b64": qr_b64,
            "photo_b64": photo_b64,
            "photo_mime": photo_mime,
            "site_url": "hrms.martindevcorp.com",
        },
    )

    result = _io.BytesIO()
    pisa.CreatePDF(src=html, dest=result)
    response = HttpResponse(result.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="employee_card.pdf"'
    return response


@login_required
@permission_required("employee.view_bank")
def bank_settings_view(request):
    banks = Bank.objects.all()
    return render(request, "employee/bank/bank.html", {"banks": banks})


@login_required
@hx_request_required
@permission_required("employee.add_bank")
def bank_create(request):
    form = BankForm()
    if request.method == "POST":
        form = BankForm(request.POST)
        if form.is_valid():
            form.save()
            form = BankForm()
            messages.success(request, _("Bank has been created successfully!"))
            return HorillaRedirect(request)
    return render(request, "employee/bank/bank_form.html", {"form": form})


@login_required
@hx_request_required
@permission_required("employee.change_bank")
def bank_update(request, obj_id):
    bank = Bank.objects.get(id=obj_id)
    form = BankForm(instance=bank)
    if request.method == "POST":
        form = BankForm(request.POST, instance=bank)
        if form.is_valid():
            form.save()
            messages.success(request, _("Bank updated successfully."))
            return HorillaRedirect(request)
    return render(request, "employee/bank/bank_form.html", {"form": form, "bank": bank})
