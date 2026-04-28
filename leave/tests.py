import json
from unittest.mock import MagicMock, patch

from django.db.models import ProtectedError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from leave import views as leave_views


class LeaveRequestDeleteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _build_request(self, path, method="get", is_superuser=False, has_perm=True):
        if method == "post":
            request = self.factory.post(path)
        else:
            request = self.factory.get(path, HTTP_HX_REQUEST="true")

        employee = MagicMock()
        employee.is_active = True
        employee.id = 1

        user = MagicMock()
        user.is_authenticated = True
        user.is_active = True
        user.is_superuser = is_superuser
        user.employee_get = employee
        user.has_perm.return_value = has_perm

        request.user = user
        request.session = {}
        return request

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("leave.views.messages.error")
    @patch("leave.views.messages.success")
    @patch("leave.views.redirect", return_value=HttpResponse(status=302))
    @patch("leave.views._delete_leave_request_record", return_value=(True, None))
    @patch("leave.views.LeaveRequest.objects.get")
    def test_superuser_single_delete_all_statuses(
        self,
        leave_request_get_mock,
        delete_record_mock,
        _redirect_mock,
        messages_success_mock,
        messages_error_mock,
        reporting_manager_filter_mock,
    ):
        reporting_manager_filter_mock.return_value.exists.return_value = False

        for status in ["requested", "approved", "rejected", "cancelled"]:
            with self.subTest(status=status):
                request = self._build_request(
                    "/leave/request-delete/1/", is_superuser=True, has_perm=True
                )
                leave_request = MagicMock()
                leave_request.status = status
                leave_request_get_mock.return_value = leave_request
                delete_record_mock.reset_mock()
                messages_success_mock.reset_mock()
                messages_error_mock.reset_mock()

                response = leave_views.leave_request_delete(request, 1)

                self.assertEqual(response.status_code, 302)
                delete_record_mock.assert_called_once_with(
                    leave_request, force_delete=True
                )
                messages_success_mock.assert_called_once()
                messages_error_mock.assert_not_called()

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("leave.views.messages.error")
    @patch("leave.views.messages.success")
    @patch("leave.views.redirect", return_value=HttpResponse(status=302))
    @patch("leave.views._delete_leave_request_record", return_value=(True, None))
    @patch("leave.views.LeaveRequest.objects.get")
    def test_non_superuser_single_delete_blocks_non_requested_status(
        self,
        leave_request_get_mock,
        delete_record_mock,
        _redirect_mock,
        _messages_success_mock,
        messages_error_mock,
        reporting_manager_filter_mock,
    ):
        reporting_manager_filter_mock.return_value.exists.return_value = False
        request = self._build_request(
            "/leave/request-delete/1/", is_superuser=False, has_perm=True
        )
        leave_request = MagicMock()
        leave_request.status = "approved"
        leave_request_get_mock.return_value = leave_request

        response = leave_views.leave_request_delete(request, 1)

        self.assertEqual(response.status_code, 302)
        delete_record_mock.assert_not_called()
        messages_error_mock.assert_called_once()
        self.assertIn("cannot delete leave request with status", messages_error_mock.call_args[0][1].lower())

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("leave.views.messages.error")
    @patch("leave.views.messages.success")
    @patch("leave.views.redirect", return_value=HttpResponse(status=302))
    @patch("leave.views._delete_leave_request_record", return_value=(True, None))
    @patch("leave.views.LeaveRequest.objects.get")
    def test_non_superuser_single_delete_allows_requested_status(
        self,
        leave_request_get_mock,
        delete_record_mock,
        _redirect_mock,
        messages_success_mock,
        messages_error_mock,
        reporting_manager_filter_mock,
    ):
        reporting_manager_filter_mock.return_value.exists.return_value = False
        request = self._build_request(
            "/leave/request-delete/1/", is_superuser=False, has_perm=True
        )
        leave_request = MagicMock()
        leave_request.status = "requested"
        leave_request_get_mock.return_value = leave_request

        response = leave_views.leave_request_delete(request, 1)

        self.assertEqual(response.status_code, 302)
        delete_record_mock.assert_called_once_with(leave_request, force_delete=False)
        messages_success_mock.assert_called_once()
        messages_error_mock.assert_not_called()

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("leave.views.messages.error")
    @patch("leave.views.messages.success")
    @patch("leave.views._delete_leave_request_record", return_value=(True, None))
    @patch("leave.views.LeaveRequest.objects.get")
    def test_superuser_bulk_delete_all_statuses(
        self,
        leave_request_get_mock,
        delete_record_mock,
        _messages_success_mock,
        _messages_error_mock,
        reporting_manager_filter_mock,
    ):
        reporting_manager_filter_mock.return_value.exists.return_value = False
        request = self._build_request(
            "/leave/leave-request-bulk-delete", method="post", is_superuser=True
        )
        request.POST = request.POST.copy()
        request.POST["ids"] = json.dumps([1, 2, 3, 4])

        leave_requests = {}
        for request_id, status in zip(
            [1, 2, 3, 4], ["requested", "approved", "rejected", "cancelled"]
        ):
            leave_request = MagicMock()
            leave_request.status = status
            leave_request.employee_id = f"Emp-{request_id}"
            leave_requests[request_id] = leave_request

        leave_request_get_mock.side_effect = lambda id: leave_requests[id]

        response = leave_views.leave_request_bulk_delete(request)
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(payload["deleted_count"], 4)
        self.assertEqual(payload["blocked_count"], 0)
        self.assertEqual(delete_record_mock.call_count, 4)
        for leave_request in leave_requests.values():
            delete_record_mock.assert_any_call(leave_request, force_delete=True)

    @patch("employee.models.EmployeeWorkInformation.objects.filter")
    @patch("leave.views.messages.error")
    @patch("leave.views.messages.success")
    @patch("leave.views._delete_leave_request_record", return_value=(True, None))
    @patch("leave.views.LeaveRequest.objects.get")
    def test_non_superuser_bulk_delete_only_requested_status(
        self,
        leave_request_get_mock,
        delete_record_mock,
        _messages_success_mock,
        messages_error_mock,
        reporting_manager_filter_mock,
    ):
        reporting_manager_filter_mock.return_value.exists.return_value = False
        request = self._build_request(
            "/leave/leave-request-bulk-delete", method="post", is_superuser=False
        )
        request.POST = request.POST.copy()
        request.POST["ids"] = json.dumps([1, 2])

        requested_leave = MagicMock()
        requested_leave.status = "requested"
        requested_leave.employee_id = "Emp-1"

        approved_leave = MagicMock()
        approved_leave.status = "approved"
        approved_leave.employee_id = "Emp-2"

        leave_request_get_mock.side_effect = [requested_leave, approved_leave]

        response = leave_views.leave_request_bulk_delete(request)
        payload = json.loads(response.content.decode("utf-8"))

        self.assertEqual(payload["deleted_count"], 1)
        self.assertEqual(payload["blocked_count"], 1)
        delete_record_mock.assert_called_once_with(requested_leave, force_delete=False)
        messages_error_mock.assert_called_once()


class LeaveRequestForceCleanupTests(SimpleTestCase):
    @patch("leave.views.apps.is_installed", return_value=True)
    @patch("leave.views.get_horilla_model_class")
    @patch("leave.views._delete_orphan_leave_request_files")
    @patch("leave.views.LeaverequestComment.objects.filter")
    @patch("leave.views.LeaverequestFile.objects.filter")
    @patch("leave.views.LeaveRequestConditionApproval.objects.filter")
    def test_force_cleanup_removes_known_dependencies(
        self,
        approval_filter_mock,
        file_filter_mock,
        comment_filter_mock,
        orphan_file_cleanup_mock,
        get_model_class_mock,
        _is_installed_mock,
    ):
        leave_request = MagicMock()

        approval_qs = MagicMock()
        approval_filter_mock.return_value = approval_qs

        file_qs = MagicMock()
        file_filter_mock.return_value = file_qs
        file_qs.values_list.return_value = file_qs
        file_qs.distinct.return_value = [11, 12]

        comment_qs = MagicMock()
        comment_filter_mock.return_value = comment_qs

        work_records_model = MagicMock()
        get_model_class_mock.return_value = work_records_model

        leave_views._force_cleanup_leave_request_dependencies(leave_request)

        approval_filter_mock.assert_called_once_with(leave_request_id=leave_request)
        approval_qs.delete.assert_called_once()
        file_filter_mock.assert_called_once_with(
            leaverequestcomment__request_id=leave_request
        )
        file_qs.values_list.assert_called_once_with("id", flat=True)
        file_qs.distinct.assert_called_once()
        comment_filter_mock.assert_called_once_with(request_id=leave_request)
        comment_qs.delete.assert_called_once()
        orphan_file_cleanup_mock.assert_called_once_with([11, 12])
        get_model_class_mock.assert_called_once_with(
            app_label="attendance", model="workrecords"
        )
        work_records_model.objects.filter.assert_called_once_with(
            leave_request_id=leave_request
        )
        work_records_model.objects.filter.return_value.update.assert_called_once_with(
            leave_request_id=None
        )

    def test_delete_record_returns_blocked_message_on_protected_error(self):
        protected_one = MagicMock()
        protected_one._meta.verbose_name = "work record"
        protected_two = MagicMock()
        protected_two._meta.verbose_name = "penalty account"

        leave_request = MagicMock()
        leave_request.delete.side_effect = ProtectedError(
            "blocked", [protected_one, protected_two]
        )

        deleted, error_message = leave_views._delete_leave_request_record(
            leave_request, force_delete=False
        )

        self.assertFalse(deleted)
        self.assertIn("Deletion blocked by related records", error_message)
        self.assertIn("Work record", error_message)
        self.assertIn("Penalty account", error_message)
