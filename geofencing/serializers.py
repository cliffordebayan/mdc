from rest_framework import serializers

from .models import GeoFencing


class GeoFencingSetupSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeoFencing
        fields = ["id", "branch_id", "latitude", "longitude", "radius_in_meters", "start", "excluded_employees"]

    def validate(self, data):
        start = data.get("start")
        if start:
            latitude = data.get("latitude")
            longitude = data.get("longitude")
            if latitude is None or longitude is None:
                raise serializers.ValidationError("Latitude and longitude are required when geofence is active.")
        return data


class EmployeeLocationSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
