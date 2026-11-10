"""Compatibility helpers for legacy administrator shift updates.

New assignments are employee-owned and do not need portal copy records. This
module remains as a compatibility boundary for callers that still invoke the
old synchronization function while data is being migrated.
"""

import logging

from employee.models import EmployeeWorkInformation

logger = logging.getLogger(__name__)


def synchronize_portal_shift_change(source_shift, schedules=None):
    """Convert legacy shared assignments to private employee shifts.

    Employees already migrated to private shifts are intentionally not changed
    when a reusable template is edited.
    """
    if not source_shift or not getattr(source_shift, "pk", None):
        return {"employees": 0, "synced": 0, "failed": 0}

    work_infos = list(
        EmployeeWorkInformation._base_manager.filter(shift_id=source_shift).select_related(
            "employee_id"
        )
    )

    from employee.methods.shift_ownership import assign_employee_shift
    from attendance.views.portal import synchronize_portal_attendance_shift

    synced = 0
    failed = 0
    for work_info in work_infos:
        employee = work_info.employee_id
        try:
            assign_employee_shift(employee, source_shift)
            synchronize_portal_attendance_shift(employee)
            synced += 1
        except Exception:
            failed += 1
            logger.exception(
                "Attendance portal shift synchronization failed for employee %s",
                work_info.employee_id_id,
            )

    return {"employees": len(work_infos), "synced": synced, "failed": failed}
