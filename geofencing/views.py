from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from geopy.distance import geodesic
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from base.models import Branch
from employee.models import Employee

from .forms import GeoFencingSetupForm, EmployeeGeofenceForm, QuickGeoFenceForm
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
        location.delete()
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

        # Check assigned geofences
        assigned_geos = employee.assigned_geofences.filter(start=True)
        if assigned_geos.exists():
            inside = False
            for geo in assigned_geos:
                distance = geodesic((geo.latitude, geo.longitude), (lat, lng)).meters
                if distance <= geo.radius_in_meters:
                    inside = True
                    break
            if inside:
                return Response({"message": "Inside the geofence"}, status=status.HTTP_200_OK)
            return Response({"message": "Outside the geofence"}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"message": "No geofence assigned"}, status=status.HTTP_200_OK)


class GeoFencingSetUpPermissionCheck(APIView):
    permission_classes = [IsAuthenticated]

    @method_decorator(
        permission_required("geofencing.view_geofencing", raise_exception=True),
        name="dispatch",
    )
    def get(self, request):
        return Response(status=200)


def _geo_config_context():
    geofences = GeoFencing.objects.all()
    add_form = GeoFencingSetupForm()
    employees = Employee.objects.filter(is_active=True).prefetch_related(
        "assigned_geofences",
        "employee_work_info__department_id"
    )

    return {
        "geofences": geofences,
        "add_form": add_form,
        "employees": employees,
    }


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
            geo.delete()
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

    return render(request, "geo_config.html", _geo_config_context())


@login_required
@permission_required("geofencing.add_geofencing")
def geo_location_add_form(request):
    context = _geo_config_context()
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
        
        geofences = GeoFencing.objects.filter(id__in=geofence_ids)
        employee.assigned_geofences.set(geofences)
        messages.success(request, _("Employee geofences assigned successfully."))
    return render(request, "geo_config.html", _geo_config_context())


@login_required
@permission_required("geofencing.change_geofencing")
def geo_assign_edit(request, emp_id):
    employee = get_object_or_404(Employee, pk=emp_id)
    current_geos = employee.assigned_geofences.all()
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
        employee.assigned_geofences.clear()
        messages.success(request, _("Geofence assignments cleared successfully."))
    return render(request, "geo_config.html", _geo_config_context())


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
