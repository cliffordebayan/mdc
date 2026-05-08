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

from .forms import GeoFencingSetupForm
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
            employee = Employee.objects.select_related("employee_work_info__branch_id").get(pk=employee_id)
        except Employee.DoesNotExist:
            return Response({"message": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        work_info = getattr(employee, "employee_work_info", None)
        branch = work_info.branch_id if work_info else None

        try:
            geo = GeoFencing.objects.get(branch_id=branch, start=True)
        except GeoFencing.DoesNotExist:
            return Response({"message": "No active geofence for this branch"}, status=status.HTTP_200_OK)

        if geo.excluded_employees.filter(pk=employee.pk).exists():
            return Response({"message": "Excluded from geofence"}, status=status.HTTP_200_OK)

        lat = serializer.validated_data["latitude"]
        lng = serializer.validated_data["longitude"]
        distance = geodesic((geo.latitude, geo.longitude), (lat, lng)).meters

        if distance <= geo.radius_in_meters:
            return Response({"message": "Inside the geofence"}, status=status.HTTP_200_OK)
        return Response({"message": "Outside the geofence"}, status=status.HTTP_400_BAD_REQUEST)


class GeoFencingSetUpPermissionCheck(APIView):
    permission_classes = [IsAuthenticated]

    @method_decorator(
        permission_required("geofencing.view_geofencing", raise_exception=True),
        name="dispatch",
    )
    def get(self, request):
        return Response(status=200)


def _geo_config_context():
    geofences = GeoFencing.objects.select_related("branch_id").prefetch_related("excluded_employees").all()
    branches_with_fence = {g.branch_id_id for g in geofences if g.branch_id_id}
    available_branches = Branch.objects.filter(is_active=True).exclude(id__in=branches_with_fence)
    add_form = GeoFencingSetupForm()
    add_form.fields["branch_id"].queryset = available_branches
    return {"geofences": geofences, "add_form": add_form}


@login_required
@permission_required("geofencing.add_geofencing")
def geo_location_config(request):
    if request.method == "POST":
        action = request.GET.get("action")
        if action == "delete":
            pk = request.GET.get("pk")
            geo = get_object_or_404(GeoFencing, pk=pk)
            geo.delete()
            messages.success(request, _("Geofence deleted successfully."))
        else:
            pk = request.POST.get("geo_id")
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
    taken = GeoFencing.objects.exclude(pk=pk).values_list("branch_id_id", flat=True)
    form.fields["branch_id"].queryset = Branch.objects.filter(is_active=True).exclude(id__in=taken)
    return render(request, "geo_edit_form.html", {"form": form, "geo": geo})
