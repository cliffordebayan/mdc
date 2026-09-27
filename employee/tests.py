from io import BytesIO
from datetime import date, time
import uuid
from unittest.mock import patch

import pandas as pd
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from attendance.models import Attendance, AttendanceActivity, AttendanceOverTime
from base.models import Branch, BusinessUnit, Company, PayrollGroup
from employee.filters import EmployeeFilter
from employee.models import (
    Employee,
    EmployeeBankDetails,
    EmployeeOnboardingPortal,
    EmployeeWorkInformation,
)
from employee.views import _delete_employee_record, employee_delete
from horilla.horilla_middlewares import _thread_locals


class EmployeeDeleteSuperuserTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin = User.objects.create_superuser(
            username="employee-delete-admin",
            email="employee-delete-admin@example.com",
            password="password",
        )
        self.admin_employee = Employee.objects.create(
            employee_user_id=self.admin,
            employee_first_name="Admin",
            employee_last_name="Employee",
            email="employee-delete-admin-employee@example.com",
            phone="09170000004",
            gender="male",
            is_active=True,
        )
        self.target_user = User.objects.create_superuser(
            username="employee-delete-target",
            email="employee-delete-target@example.com",
            password="password",
        )
        self.target = Employee.objects.create(
            employee_user_id=self.target_user,
            employee_first_name="Target",
            employee_last_name="Superuser",
            email="employee-delete-target-employee@example.com",
            phone="09170000003",
            gender="male",
            is_active=True,
        )

    def tearDown(self):
        _thread_locals.request = None
        super().tearDown()

    def test_superuser_can_delete_another_superuser_employee(self):
        request = self.factory.post(
            reverse("employee-delete", args=[self.target.pk]),
            {"view": "list"},
        )
        request.user = self.admin
        SessionMiddleware(lambda _request: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        with patch(
            "employee.views.HorillaRedirect",
            return_value=HttpResponse(status=302),
        ):
            response = employee_delete(request, self.target.pk)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Employee.objects.filter(pk=self.target.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.target_user.pk).exists())

    def test_superuser_cannot_delete_their_own_employee_account(self):
        request = self.factory.post(
            reverse("employee-delete", args=[self.admin_employee.pk]),
            {"view": "list"},
        )
        request.user = self.admin
        SessionMiddleware(lambda _request: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)

        with patch(
            "employee.views.HorillaRedirect",
            return_value=HttpResponse(status=302),
        ):
            response = employee_delete(request, self.admin_employee.pk)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Employee.objects.filter(pk=self.admin_employee.pk).exists()
        )


class EmployeeDeleteProtectedDataTests(TestCase):
    def setUp(self):
        _thread_locals.request = None
        self.client = Client(enforce_csrf_checks=True)
        self.admin = User.objects.create_superuser(
            username="protected-delete-admin",
            email="protected-delete-admin@example.com",
            password="password",
        )
        self.admin_employee = Employee.objects.create(
            employee_user_id=self.admin,
            employee_first_name="Protected Delete Admin",
            email="protected-delete-admin-employee@example.com",
            phone="09170000005",
            gender="male",
            is_active=True,
        )
        self.target_user = User.objects.create_user(
            username="protected-delete-target",
            email="protected-delete-target@example.com",
            password="password",
        )
        self.target = Employee.objects.create(
            employee_user_id=self.target_user,
            employee_first_name="Protected Delete Target",
            email="protected-delete-target-employee@example.com",
            phone="09170000006",
            gender="male",
            is_active=True,
        )
        self.other_user = User.objects.create_user(
            username="protected-delete-other",
            email="protected-delete-other@example.com",
            password="password",
        )
        self.other = Employee.objects.create(
            employee_user_id=self.other_user,
            employee_first_name="Protected Delete Other",
            email="protected-delete-other-employee@example.com",
            phone="09170000007",
            gender="male",
            is_active=True,
        )

        self.target_activity = AttendanceActivity.objects.create(
            employee_id=self.target,
            attendance_date=date(2026, 1, 1),
            clock_in=time(9, 0),
        )
        self.target_attendance = Attendance.objects.create(
            employee_id=self.target,
            attendance_date=date(2026, 1, 1),
        )
        self.target_hour_account = AttendanceOverTime.objects.get(
            employee_id=self.target,
            month="january",
            year="2026",
        )
        self.other_attendance = Attendance.objects.create(
            employee_id=self.other,
            attendance_date=date(2026, 1, 1),
        )
        self.other_attendance_approved_by_target = Attendance.objects.create(
            employee_id=self.other,
            attendance_date=date(2026, 1, 2),
            approved_by=self.target,
        )

        self.client.force_login(self.admin)

    def tearDown(self):
        _thread_locals.request = None
        super().tearDown()

    def _csrf_token(self):
        token = "a" * 64
        self.client.cookies["csrftoken"] = token
        return token

    def test_delete_route_removes_protected_employee_records(self):
        response = self.client.post(
            reverse("employee-delete", args=[self.target.pk]),
            {"view": "list", "csrfmiddlewaretoken": self._csrf_token()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Employee.objects.filter(pk=self.target.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.target_user.pk).exists())
        self.assertFalse(
            AttendanceActivity.objects.filter(pk=self.target_activity.pk).exists()
        )
        self.assertFalse(
            Attendance.objects.filter(pk=self.target_attendance.pk).exists()
        )
        self.assertFalse(
            AttendanceOverTime.objects.filter(pk=self.target_hour_account.pk).exists()
        )
        self.assertFalse(
            Attendance.objects.filter(
                pk=self.other_attendance_approved_by_target.pk
            ).exists()
        )
        self.assertTrue(Attendance.objects.filter(pk=self.other_attendance.pk).exists())
        self.assertTrue(Employee.objects.filter(pk=self.other.pk).exists())

    def test_bulk_delete_uses_protected_record_cleanup(self):
        response = self.client.post(
            reverse("employee-bulk-delete"),
            {
                "ids": "[%d]" % self.target.pk,
                "csrfmiddlewaretoken": self._csrf_token(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(pk=self.target.pk).exists())
        self.assertFalse(AttendanceActivity.objects.filter(pk=self.target_activity.pk).exists())
        self.assertFalse(AttendanceOverTime.objects.filter(pk=self.target_hour_account.pk).exists())

    def test_delete_rolls_back_when_employee_delete_fails(self):
        with patch(
            "employee.views.Employee.delete",
            side_effect=RuntimeError("simulated delete failure"),
        ):
            with self.assertRaises(RuntimeError):
                _delete_employee_record(self.target)

        self.assertTrue(Employee.objects.filter(pk=self.target.pk).exists())
        self.assertTrue(
            AttendanceActivity.objects.filter(pk=self.target_activity.pk).exists()
        )
        self.assertTrue(Attendance.objects.filter(pk=self.target_attendance.pk).exists())
        self.assertTrue(
            AttendanceOverTime.objects.filter(pk=self.target_hour_account.pk).exists()
        )


class EmployeeWorkInfoExportTests(TestCase):
    def setUp(self):
        _thread_locals.request = None
        self.user = User.objects.create_superuser(
            username="export-admin@example.com",
            email="export-admin@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        self.business_unit = BusinessUnit.objects.create(
            name="Operations",
            code="OPS-001",
        )
        self.employee = Employee.objects.create(
            employee_user_id=self.user,
            employee_first_name="Export",
            employee_last_name="Employee",
            email="export-employee@example.com",
            phone="09170000001",
            is_active=True,
        )
        EmployeeWorkInformation.objects.filter(employee_id=self.employee).update(
            business_unit_id=self.business_unit,
        )
        self.employee_without_business_unit = Employee.objects.create(
            employee_first_name="No Unit",
            email="export-no-unit@example.com",
            phone="09170000002",
            is_active=True,
        )

    def test_export_includes_business_unit_name_and_code_as_separate_columns(self):
        response = self.client.get(
            reverse("work-info-export"),
            {
                "selected_fields": [
                    "get_full_name",
                    "employee_work_info__business_unit_id",
                    "employee_work_info__business_unit_id__code",
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        data_frame = pd.read_excel(BytesIO(response.content))

        self.assertEqual(
            data_frame.columns.tolist(),
            ["Complete Name", "Business Unit", "Business Unit Code"],
        )
        employee_row = data_frame.loc[
            data_frame["Complete Name"] == "Export Employee"
        ].iloc[0]
        self.assertEqual(employee_row["Business Unit"], "Operations")
        self.assertEqual(employee_row["Business Unit Code"], "OPS-001")

    def test_export_leaves_business_unit_code_blank_when_not_set(self):
        response = self.client.get(
            reverse("work-info-export"),
            {
                "selected_fields": [
                    "get_full_name",
                    "employee_work_info__business_unit_id__code",
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        data_frame = pd.read_excel(BytesIO(response.content))

        self.assertIn("Business Unit Code", data_frame.columns)
        employee_row = data_frame.loc[
            data_frame["Complete Name"] == "No Unit"
        ].iloc[0]
        self.assertTrue(pd.isna(employee_row["Business Unit Code"]))


class EmployeeSearchFilterTests(TestCase):
    def setUp(self):
        _thread_locals.request = None
        unique_id = uuid.uuid4().hex[:8]
        self.target = self._employee(
            unique_id,
            "target",
            employee_no="MDC-001",
            employee_first_name="Michelle",
            employee_middle_name="Anne",
            employee_last_name="Reyes",
        )
        self.email_only = self._employee(
            unique_id,
            "email",
            employee_no="OPS-222",
            employee_first_name="Rina",
            employee_middle_name="Mae",
            employee_last_name="Cruz",
            email_prefix="michelle.lookup",
        )
        self.branch_only = self._employee(
            unique_id,
            "branch",
            employee_no="OPS-333",
            employee_first_name="Paolo",
            employee_middle_name="Luis",
            employee_last_name="Garcia",
        )
        branch = Branch.objects.create(
            branch="North Hub",
            branch_code=f"NH{unique_id[:6]}",
            address="Sample Address",
            country="PH",
            state="NCR",
            city="Manila",
            zip="1000",
        )
        EmployeeWorkInformation.objects.filter(employee_id=self.branch_only).update(
            branch_id=branch
        )

    def _employee(self, unique_id, label, email_prefix=None, **kwargs):
        email_prefix = email_prefix or label
        return Employee.objects.create(
            email=f"{email_prefix}-{unique_id}@example.com",
            phone=f"0917{unique_id[:4]}{len(label):03}",
            gender="male",
            is_active=True,
            **kwargs,
        )

    def _search_ids(self, value):
        return set(
            EmployeeFilter(
                {"search": value},
                queryset=Employee.objects.all(),
            ).qs.values_list("id", flat=True)
        )

    def test_search_finds_by_employee_number_and_name_parts(self):
        for value in ["MDC-00", "Michelle", "Anne", "Reyes"]:
            with self.subTest(value=value):
                self.assertIn(self.target.id, self._search_ids(value))

    def test_search_matches_all_tokens_against_number_or_name(self):
        self.assertIn(self.target.id, self._search_ids("MDC Michelle"))
        self.assertIn(self.target.id, self._search_ids("mic rey"))
        self.assertNotIn(self.target.id, self._search_ids("Michelle Cruz"))

    def test_search_ignores_email_and_branch(self):
        self.assertNotIn(self.email_only.id, self._search_ids("lookup"))
        self.assertNotIn(self.branch_only.id, self._search_ids("North"))


class EmployeePortalFlowTests(TestCase):
    def setUp(self):
        _thread_locals.request = None
        unique_id = uuid.uuid4().hex[:8]
        self.user = User.objects.create_user(
            username=f"portal-{unique_id}@example.com",
            email=f"portal-{unique_id}@example.com",
            password="password123",
        )
        self.employee = Employee.objects.create(
            employee_user_id=self.user,
            employee_first_name="Portal",
            employee_last_name="Employee",
            email=f"portal-employee-{unique_id}@example.com",
            phone=f"0917{unique_id[:4]}001",
            gender="male",
            is_active=True,
        )
        self.work_info = getattr(self.employee, "employee_work_info", None)
        if not self.work_info:
            self.work_info = EmployeeWorkInformation.objects.create(
                employee_id=self.employee
            )
        self.token = uuid.uuid4().hex
        self.portal = EmployeeOnboardingPortal.objects.create(
            employee_id=self.employee,
            token=self.token,
            count=2,
        )

    def _valid_personal_payload(self):
        return {
            "employee_first_name": "Portal",
            "employee_middle_name": "",
            "employee_last_name": "Employee",
            "employee_extension": "",
            "phone": "09171234567",
            "dob": "1990-01-01",
            "gender": "male",
            "address": "123 Test Street",
            "country": "Philippines",
            "state": "Metro Manila",
            "city": "Manila",
            "zip": "1000",
            "emergency_contact": "09176543210",
            "emergency_contact_name": "Emergency Contact",
            "emergency_contact_relation": "Sibling",
        }

    def test_profile_page_shows_explicit_upload_photo_button(self):
        response = self.client.get(
            reverse("employee-portal-profile", args=[self.token])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-controls="uploadPhotoModal"')

    def test_personal_details_redirects_directly_to_pin_step(self):
        response = self.client.post(
            reverse("employee-portal-personal", args=[self.token]),
            self._valid_personal_payload(),
        )

        self.assertRedirects(
            response,
            reverse("employee-portal-pin", args=[self.token]),
            fetch_redirect_response=False,
        )
        self.portal.refresh_from_db()
        self.assertEqual(self.portal.count, 3)

    def test_pin_page_is_accessible_after_personal_details_step(self):
        self.portal.count = 3
        self.portal.save(update_fields=["count"])

        response = self.client.get(reverse("employee-portal-pin", args=[self.token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Portal PIN")

    def test_old_bank_step_url_redirects_to_pin_step(self):
        self.portal.count = 3
        self.portal.save(update_fields=["count"])

        response = self.client.get(reverse("employee-portal-bank", args=[self.token]))

        self.assertRedirects(
            response,
            reverse("employee-portal-pin", args=[self.token]),
            fetch_redirect_response=False,
        )

    def test_portal_steps_no_longer_show_bank_details(self):
        response = self.client.get(
            reverse("employee-portal-personal", args=[self.token])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Bank Details")
        self.assertContains(response, "Portal PIN")


class EmployeeImportFlowTests(TransactionTestCase):
    def setUp(self):
        _thread_locals.request = None
        self.user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password123",
        )
        Employee.objects.create(
            employee_user_id=self.user,
            employee_first_name="Admin",
            employee_last_name="User",
            email="admin@example.com",
            phone="09170000000",
            gender="male",
            is_active=True,
        )
        self.client.force_login(self.user)

    def _build_excel_file(self, rows):
        file_buffer = BytesIO()
        pd.DataFrame(rows).to_excel(file_buffer, index=False)
        return SimpleUploadedFile(
            "work_info_import.xlsx",
            file_buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def _valid_import_row(self, **overrides):
        row = {
            "Employee No": "EMP1001",
            "First Name": "John",
            "Last Name": "Doe",
            "Phone": "+639171234567",
            "Email": "john.doe@example.com",
            "Gender": "male",
            "Department": "",
            "Job Position": "",
            "Job Role": "",
            "Work Type": "",
            "Shift Information": "",
            "Employee Type": "",
            "Payroll Group": "",
            "Reporting Manager": "",
            "Company": "",
            "Work Location": "Main Office",
            "Joining Date": "2024-01-15",
            "Salary": 30000,
            "Salary Hour": 150,
            "GCash": "09171234567",
            "Metrobank": "000123456789",
        }
        row.update(overrides)
        return row

    def _post_import(self, file_buffer):
        with patch("employee.views.threading.Thread") as view_thread, patch(
            "employee.methods.methods.threading.Thread"
        ) as methods_thread:
            view_thread.return_value.start.return_value = None
            methods_thread.return_value.start.return_value = None
            return self.client.post(
                reverse("work-info-import"),
                {"file": file_buffer},
                HTTP_HX_REQUEST="true",
            )

    def test_work_info_import_template_is_downloadable(self):
        response = self.client.get(reverse("work-info-import-file"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response.get("Content-Disposition", ""))
        self.assertIn(
            'filename="work_info_template.xlsx"',
            response.get("Content-Disposition", ""),
        )

    def test_work_info_import_template_contains_payroll_group_header_and_reference(self):
        payroll_group = PayrollGroup.objects.create(name="Monthly Payroll")
        response = self.client.get(reverse("work-info-import-file"))
        self.assertEqual(response.status_code, 200)

        workbook = BytesIO(response.content)
        data_frame = pd.read_excel(workbook, sheet_name="Import Template")
        columns = list(data_frame.columns)

        self.assertGreater(len(columns), 6)
        self.assertIn("Payroll Group", columns)
        self.assertLess(
            columns.index("Employee Type"),
            columns.index("Payroll Group"),
        )
        self.assertLess(
            columns.index("Payroll Group"),
            columns.index("Reporting Manager"),
        )

        reference_frame = pd.read_excel(BytesIO(response.content), sheet_name="Reference")
        self.assertIn("Payroll Group", reference_frame.columns)
        self.assertIn(payroll_group.name, reference_frame["Payroll Group"].dropna().tolist())

    def test_work_info_import_accepts_downloaded_template_file(self):
        Company.objects.create(
            company="Martin Development Corporation",
            address="Sample Address",
            country="PH",
            state="NCR",
            city="Manila",
            zip="1000",
        )
        template_response = self.client.get(reverse("work-info-import-file"))
        self.assertEqual(template_response.status_code, 200)

        file_buffer = SimpleUploadedFile(
            "work_info_template.xlsx",
            template_response.content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Employee.objects.filter(employee_no="EMP001").exists())
        self.assertIn("Import Successful", response.content.decode("utf-8"))

    def test_work_info_import_shows_warning_when_create_fails(self):
        file_buffer = self._build_excel_file(
            [self._valid_import_row(**{"Employee No": "EMP2001", "Email": "emp2001@example.com"})]
        )

        with patch("employee.views.bulk_create_employee_import", return_value=[]), patch(
            "employee.views.threading.Thread"
        ) as view_thread, patch("employee.methods.methods.threading.Thread") as methods_thread:
            view_thread.return_value.start.return_value = None
            methods_thread.return_value.start.return_value = None
            response = self.client.post(
                reverse("work-info-import"),
                {"file": file_buffer},
                HTTP_HX_REQUEST="true",
            )

        content = response.content.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Import Warning", content)
        self.assertIn("No Employees were imported.", content)
        self.assertIn("Download Error File", content)
        self.assertFalse(Employee.objects.filter(employee_no="EMP2001").exists())

    def test_work_info_import_defaults_company_from_selected_company(self):
        company = Company.objects.create(
            company="Demo Company",
            address="Sample Address",
            country="PH",
            state="NCR",
            city="Manila",
            zip="1000",
        )
        session = self.client.session
        session["selected_company"] = str(company.id)
        session.save()

        file_buffer = self._build_excel_file(
            [self._valid_import_row(**{"Employee No": "EMP3001", "Email": "emp3001@example.com", "Company": ""})]
        )
        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        employee = Employee.objects.get(employee_no="EMP3001")
        work_info = EmployeeWorkInformation.objects.get(employee_id=employee)
        self.assertEqual(work_info.company_id, company)

    def test_work_info_import_creates_employee_and_work_info(self):
        file_buffer = self._build_excel_file([self._valid_import_row()])

        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Employee.objects.filter(employee_no="EMP1001").exists())

        employee = Employee.objects.get(employee_no="EMP1001")
        work_info = EmployeeWorkInformation.objects.get(employee_id=employee)
        self.assertEqual(work_info.basic_salary, 30000)
        self.assertEqual(work_info.salary_hour, 150)

        self.assertIn("Import Successful", response.content.decode("utf-8"))

    def test_work_info_import_sets_payroll_group(self):
        payroll_group = PayrollGroup.objects.create(name="Semi-Monthly Payroll")
        file_buffer = self._build_excel_file(
            [
                self._valid_import_row(
                    **{
                        "Employee No": "EMP1004",
                        "Email": "payroll.group@example.com",
                        "Payroll Group": payroll_group.name,
                    }
                )
            ]
        )

        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        employee = Employee.objects.get(employee_no="EMP1004")
        work_info = EmployeeWorkInformation.objects.get(employee_id=employee)
        self.assertEqual(work_info.payroll_group_id, payroll_group)

    def test_work_info_import_rejects_unknown_company_and_shows_error_download(self):
        file_buffer = self._build_excel_file(
            [self._valid_import_row(**{"Employee No": "EMP1002", "Company": "Unknown Co"})]
        )

        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(employee_no="EMP1002").exists())
        self.assertIn("Download Error File", response.content.decode("utf-8"))

    def test_work_info_import_rejects_unknown_payroll_group_and_shows_error_download(self):
        file_buffer = self._build_excel_file(
            [
                self._valid_import_row(
                    **{
                        "Employee No": "EMP1005",
                        "Email": "unknown.payroll@example.com",
                        "Payroll Group": "Unknown Payroll Group",
                    }
                )
            ]
        )

        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(employee_no="EMP1005").exists())
        self.assertIn("Download Error File", response.content.decode("utf-8"))

    def test_work_info_import_preserves_numeric_and_bank_values(self):
        file_buffer = self._build_excel_file(
            [
                self._valid_import_row(
                    **{
                        "Employee No": "EMP1003",
                        "Email": "numeric.user@example.com",
                        "Salary": 45678,
                        "Salary Hour": 220,
                        "Gcash": "09179998888",
                        "Metrobank": "009900110022",
                    }
                )
            ]
        )

        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        employee = Employee.objects.get(employee_no="EMP1003")
        work_info = EmployeeWorkInformation.objects.get(employee_id=employee)
        self.assertEqual(work_info.basic_salary, 45678)
        self.assertEqual(work_info.salary_hour, 220)

        bank_accounts = EmployeeBankDetails.objects.filter(employee_id=employee)
        self.assertTrue(bank_accounts.filter(bank__name="GCash").exists())
        self.assertTrue(bank_accounts.filter(bank__name="Metrobank").exists())


class EmployeeStatusTransitionTests(TransactionTestCase):
    def setUp(self):
        _thread_locals.request = None
        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="password123",
        )
        self.employee = Employee.objects.create(
            employee_user_id=self.user,
            employee_first_name="Test",
            employee_last_name="Employee",
            email="testuser@example.com",
            phone="09170000001",
            gender="male",
            employee_no="12345",
            is_active=True,
        )
        # Note: EmployeeWorkInformation is created in Employee.save() if it doesn't exist.
        self.work_info = getattr(self.employee, "employee_work_info", None)
        if not self.work_info:
            self.work_info = EmployeeWorkInformation.objects.create(employee_id=self.employee)

    def test_default_status_is_active(self):
        self.assertEqual(self.work_info.employee_status, "active")

    def test_status_transition_to_resigned_keeps_employee_no(self):
        self.work_info.employee_status = "resigned"
        self.work_info.save()
        
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employee_no, "12345")

    def test_status_transition_resigned_to_active_appends_suffix(self):
        # First transition to resigned
        self.work_info.employee_status = "resigned"
        self.work_info.save()
        
        # Then transition back to active
        self.work_info.employee_status = "active"
        self.work_info.save()
        
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employee_no, "12345-2")

    def test_multiple_transitions_keeps_suffix_at_two(self):
        # 1st transition resigned -> active
        self.work_info.employee_status = "resigned"
        self.work_info.save()
        self.work_info.employee_status = "active"
        self.work_info.save()
        
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employee_no, "12345-2")
        
        # 2nd transition awol -> active
        self.work_info.employee_status = "awol"
        self.work_info.save()
        self.work_info.employee_status = "active"
        self.work_info.save()
        
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employee_no, "12345-2")

    def test_transition_with_conflicting_suffix_raises_validation_error(self):
        from django.core.exceptions import ValidationError
        
        # Create another employee that already has the "12345-2" number
        other_user = User.objects.create_user(
            username="otheruser",
            email="otheruser@example.com",
            password="password123",
        )
        Employee.objects.create(
            employee_user_id=other_user,
            employee_first_name="Other",
            employee_last_name="Employee",
            email="otheruser@example.com",
            phone="09170000002",
            gender="male",
            employee_no="12345-2",
            is_active=True,
        )
        
        # Transition original back to active.
        # This will set it to "12345-2", which is a duplicate of other employee.
        # This must raise a ValidationError because employee_no unique constraint exists.
        self.work_info.employee_status = "resigned"
        self.work_info.save()
        self.work_info.employee_status = "active"
        with self.assertRaises(ValidationError):
            self.work_info.save()
