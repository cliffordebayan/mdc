from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from base.methods import paginator_qry
from base.models import Branch
from attendance.models import AttendancePortalMultiPunchEmployee
from attendance.views.portal import _enforced_assigned_geofences, _geofence_check
from employee.filters import employee_name_number_search_query
from employee.models import Employee

from .forms import (
    GeoFencingSetupForm,
    EmployeeGeofenceForm,
    PortalMultiPunchEmployeeForm,
    QuickGeoFenceForm,
)
from .models import GeoFencing
from .serializers import EmployeeLocationSerializer, GeoFencingSetupSerializer


class GeoFencingSetupGetPostAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @method_decorator(
        permission_required("geofencing.view_geofencing", raise_exception=True),
        name="dispatch",
    )
    def get(self, request):
        branch_id = request.query_params.get("branch_id")
        if branch_id:
            location = get_object_or_404(GeoFencing, branch_id=branch_id)
        else:
            location = GeoFencing.objects.first()
        serializer = GeoFencingSetupSerializer(location)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @method_decorator(
        permission_required("geofencing.add_geofencing", raise_exception=True),
        name="dispatch",
    )
    def post(self, request):
        serializer = GeoFencingSetupSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class GeoFencingSetupPutDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @method_decorator(
        permission_required("geofencing.change_geofencing", raise_exception=True),
        name="dispatch",
    )
    def put(self, request, pk):
        location = get_object_or_404(GeoFencing, pk=pk)
        serializer = GeoFencingSetupSerializer(location, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @method_decorator(
        permission_required("geofencing.delete_geofencing", raise_exception=True),
        name="dispatch",
    )
    def delete(self, request, pk):
        location = get_object_or_404(GeoFencing, pk=pk)
        _delete_geofence(location)
        return Response(
            {"message": "GeoFencing location deleted successfully"},
            status=status.HTTP_200_OK,
        )


class GeoFencingEmployeeLocationCheckAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = EmployeeLocationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        employee_id = request.data.get("employee_id")
        try:
            employee = Employee.objects.get(pk=employee_id)
        except Employee.DoesNotExist:
            return Response({"message": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        lat = serializer.validated_data["latitude"]
        lng = serializer.validated_data["longitude"]

        enforced_geofences = _enforced_assigned_geofences(employee)
        if not enforced_geofences:
            return Response(
                {"message": "No active geofence restriction"},
                status=status.HTTP_200_OK,
            )

        geo_error = _geofence_check(employee, None, lat, lng)
        if geo_error:
            return Response(
                {**geo_error, "message": "Outside the geofence"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"message": "Inside the geofence"}, status=status.HTTP_200_OK)


class GeoFencingSetUpPermissionCheck(APIView):
    permission_classes = [IsAuthenticated]

    @method_decorator(
        permission_required("geofencing.view_geofencing", raise_exception=True),
        name="dispatch",
    )
    def get(self, request):
        return Response(status=200)


def _geo_assignment_context(request):
    """Build the paginated employee assignment list context."""
    search = request.GET.get("search", "").strip()
    employees = Employee.objects.filter(is_active=True)
    if search:
        search_query = Q()
        for token in search.split():
            search_query &= employee_name_number_search_query(token) | Q(
                employee_work_info__department_id__department__icontains=token
            )
        employees = employees.filter(search_query)

    employees = (
        employees.select_related(
            "employee_work_info",
            "employee_work_info__department_id",
        )
        .prefetch_related(
            "assigned_geofences",
            "assigned_geofences__excluded_employees",
        )
        .order_by(
            "employee_first_name",
            "employee_last_name",
            "employee_no",
            "pk",
        )
    )

    paginated_employees = paginator_qry(employees, request.GET.get("page"))
    for employee in paginated_employees.object_list:
        employee.enforced_geofences = _enforced_assigned_geofences(employee)

    query_params = request.GET.copy()
    query_params.pop("page", None)

    return {
        "employees": paginated_employees,
        "search": search,
        "pd": query_params.urlencode(),
    }


def _geo_config_context(request):
    geofences = GeoFencing.objects.all()
    add_form = GeoFencingSetupForm()
    assignment_context = _geo_assignment_context(request)

    multi_punch_assignments = (
        AttendancePortalMultiPunchEmployee.objects.filter(is_active=True)
        .select_related(
            "employee_id",
            "employee_id__employee_work_info",
            "employee_id__employee_work_info__department_id",
        )
        .order_by("employee_id__employee_first_name", "employee_id__employee_last_name")
    )

    return {
        "geofences": geofences,
        "add_form": add_form,
        "employees": assignment_context["employees"],
        "search": assignment_context["search"],
        "pd": assignment_context["pd"],
        "multi_punch_form": PortalMultiPunchEmployeeForm(),
        "multi_punch_assignments": multi_punch_assignments,
    }


def _active_geofences():
    return GeoFencing.objects.filter(start=True)


def _delete_geofence(geofence):
    """Delete one geofence and detach it from every employee first."""
    with transaction.atomic():
        geofence.assigned_employees.clear()
        geofence.excluded_employees.clear()
        geofence.delete()


def _sync_employee_geofence_coverage(employee, selected_geofences):
    """Replace an employee's explicit geofence assignments.

    A geofence is restrictive only when it is present in this relation, so a
    newly created geofence remains inactive for every employee until selected.
    """
    employee.assigned_geofences.set(selected_geofences)

    # Clear legacy exclusion rows for this employee. They are no longer used
    # to grant or revoke access now that assignments are explicit.
    for geofence in _active_geofences():
        geofence.excluded_employees.remove(employee)


def _clear_employee_geofence_coverage(employee):
    employee.assigned_geofences.clear()
    for geofence in _active_geofences():
        geofence.excluded_employees.remove(employee)


@login_required
@permission_required("geofencing.view_geofencing")
def geo_location_config(request):
    if request.method == "POST":
        from django.core.exceptions import PermissionDenied
        action = request.GET.get("action")
        if action == "delete":
            if not request.user.has_perm("geofencing.delete_geofencing"):
                raise PermissionDenied
            pk = request.GET.get("pk")
            geo = get_object_or_404(GeoFencing, pk=pk)
            _delete_geofence(geo)
            messages.success(request, _("Geofence deleted successfully."))
        else:
            pk = request.POST.get("geo_id")
            if pk:
                if not request.user.has_perm("geofencing.change_geofencing"):
                    raise PermissionDenied
            else:
                if not request.user.has_perm("geofencing.add_geofencing"):
                    raise PermissionDenied
            instance = GeoFencing.objects.filter(pk=pk).first() if pk else None
            form = GeoFencingSetupForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, _("Geofence saved successfully."))
            else:
                messages.error(request, str(form.errors))

    return render(request, "geo_config.html", _geo_config_context(request))


@login_required
@permission_required("geofencing.add_geofencing")
def geo_location_add_form(request):
    context = _geo_config_context(request)
    return render(request, "geo_add_form.html", {"form": context["add_form"]})


@login_required
@permission_required("geofencing.change_geofencing")
def geo_location_edit(request, pk):
    geo = get_object_or_404(GeoFencing, pk=pk)
    form = GeoFencingSetupForm(instance=geo)
    return render(request, "geo_edit_form.html", {"form": form, "geo": geo})


@login_required
@permission_required("geofencing.change_geofencing")
def geo_assign_add(request):
    form = EmployeeGeofenceForm()
    form.fields["employee"].queryset = Employee.objects.filter(is_active=True)
    return render(request, "geo_assign_form.html", {"form": form, "edit_mode": False})


@login_required
@permission_required("geofencing.change_geofencing")
def geo_assign_save(request):
    if request.method == "POST":
        emp_id = request.POST.get("employee")
        employee = get_object_or_404(Employee, pk=emp_id)
        geofence_ids = request.POST.getlist("geofences")

        geofences = list(_active_geofences().filter(id__in=geofence_ids))
        _sync_employee_geofence_coverage(employee, geofences)
        messages.success(request, _("Employee geofences assigned successfully."))
    return render(request, "geo_config.html", _geo_config_context(request))


@login_required
@permission_required("geofencing.view_geofencing")
@require_http_methods(["GET"])
def geo_assignments(request):
    """Return the HTMX employee assignment list and its pagination."""
    return render(
        request,
        "geo_assignment_list.html",
        _geo_assignment_context(request),
    )


@login_required
@permission_required("geofencing.change_geofencing")
def geo_assign_edit(request, emp_id):
    employee = get_object_or_404(Employee, pk=emp_id)
    current_geos = _enforced_assigned_geofences(employee)
    form = EmployeeGeofenceForm(initial={
        "employee": employee.id,
        "geofences": current_geos
    })
    return render(request, "geo_assign_form.html", {
        "form": form,
        "employee": employee,
        "edit_mode": True
    })


@login_required
@permission_required("geofencing.change_geofencing")
def geo_assign_delete(request, emp_id):
    if request.method == "POST":
        employee = get_object_or_404(Employee, pk=emp_id)
        _clear_employee_geofence_coverage(employee)
        messages.success(request, _("Geofence assignments cleared successfully."))
    return render(request, "geo_config.html", _geo_config_context(request))


@login_required
@permission_required("geofencing.change_geofencing")
def geo_multi_punch_add(request):
    form = PortalMultiPunchEmployeeForm()
    return render(request, "geo_multi_punch_form.html", {"form": form})


@login_required
@permission_required("geofencing.change_geofencing")
def geo_multi_punch_save(request):
    if request.method == "POST":
        form = PortalMultiPunchEmployeeForm(request.POST)
        if form.is_valid():
            employee = form.cleaned_data["employee"]
            assignment, _created = AttendancePortalMultiPunchEmployee.objects.get_or_create(
                employee_id=employee,
                defaults={"is_active": True},
            )
            if not assignment.is_active:
                assignment.is_active = True
                assignment.save(update_fields=["is_active"])
            messages.success(
                request,
                _("Employee enabled for multiple clock in/out portal mode."),
            )
        else:
            messages.error(request, str(form.errors))
    return render(request, "geo_config.html", _geo_config_context(request))


@login_required
@permission_required("geofencing.change_geofencing")
def geo_multi_punch_delete(request, pk):
    if request.method == "POST":
        assignment = get_object_or_404(AttendancePortalMultiPunchEmployee, pk=pk)
        assignment.delete()
        messages.success(
            request,
            _("Employee removed from multiple clock in/out portal mode."),
        )
    return render(request, "geo_config.html", _geo_config_context(request))


@login_required
@permission_required("geofencing.add_geofencing")
def geo_quick_add(request):
    if request.method == "POST":
        form = QuickGeoFenceForm(request.POST)
        if form.is_valid():
            geo = form.save()
            return JsonResponse({
                "success": True,
                "id": geo.id,
                "name": str(geo)
            })
        else:
            return JsonResponse({
                "success": False,
                "errors": form.errors
            })
    return JsonResponse({"success": False, "errors": "Invalid method"})
