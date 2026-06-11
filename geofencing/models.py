from django.db import models
from django.db.models import Q


class GeoFencing(models.Model):
    name = models.CharField(max_length=255, blank=True, null=True)
    branch_id = models.OneToOneField(
        "base.Branch",
        related_name="geo_fencing",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    radius_in_meters = models.IntegerField()
    start = models.BooleanField(default=False)
    excluded_employees = models.ManyToManyField(
        "employee.Employee",
        blank=True,
        related_name="geofence_excluded",
    )
    assigned_employees = models.ManyToManyField(
        "employee.Employee",
        blank=True,
        related_name="assigned_geofences",
    )

    def __str__(self):
        if self.name:
            return self.name
        branch_name = self.branch_id.branch if self.branch_id else "No Branch"
        return f"GeoFence – {branch_name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["branch_id"],
                name="unique_branch_id_when_not_null_geofencing",
                condition=~Q(branch_id=None),
            )
        ]
