from io import BytesIO
from unittest.mock import patch

import pandas as pd
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from django.urls import reverse

from base.models import Company
from employee.models import Employee, EmployeeBankDetails, EmployeeWorkInformation
from horilla.horilla_middlewares import _thread_locals


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
            "Reporting Manager": "",
            "Company": "",
            "Work Location": "Main Office",
            "Joining Date": "2024-01-15",
            "Salary": 30000,
            "Salary Hour": 150,
            "Gcash": "09171234567",
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

    def test_work_info_import_template_contains_required_headers_only(self):
        response = self.client.get(reverse("work-info-import-file"))
        self.assertEqual(response.status_code, 200)

        data_frame = pd.read_excel(BytesIO(response.content))
        self.assertEqual(
            list(data_frame.columns),
            ["Employee No", "First Name", "Last Name", "Phone", "Email", "Gender"],
        )

    def test_work_info_import_accepts_downloaded_template_file(self):
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

    def test_work_info_import_rejects_unknown_company_and_shows_error_download(self):
        file_buffer = self._build_excel_file(
            [self._valid_import_row(**{"Employee No": "EMP1002", "Company": "Unknown Co"})]
        )

        response = self._post_import(file_buffer)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(employee_no="EMP1002").exists())
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
        self.assertTrue(bank_accounts.filter(bank_name="GCash").exists())
        self.assertTrue(bank_accounts.filter(bank_name="Metrobank").exists())


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
