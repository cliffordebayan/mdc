"""
employee/methods.py
"""

import logging
import re
import threading
from datetime import date, datetime
from itertools import chain, groupby

import pandas as pd
from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection, models, transaction
from django.utils.translation import gettext as _

from base.context_processors import get_initial_prefix
from base.models import (
    Branch,
    BusinessUnit,
    Company,
    CostCenter,
    Department,
    EmployeeShift,
    EmployeeType,
    JobPosition,
    JobRole,
    WorkType,
)
from employee.models import Employee, EmployeeBankDetails, EmployeeTag, EmployeeWorkInformation

logger = logging.getLogger(__name__)

is_postgres = connection.vendor == "postgresql"

error_data_template = {
    field: []
    for field in [
        "Employee No",
        "First Name",
        "Last Name",
        "Phone",
        "Email",
        "Gender",
        "Department",
        "Job Position",
        "Job Role",
        "Work Type",
        "Shift",
        "Employee Type",
        "Reporting Manager",
        "Company",
        "Location",
        "Date Joining",
        "Contract End Date",
        "Basic Salary",
        "Salary Hour",
        "Email Error",
        "First Name Error",
        "Name and Email Error",
        "Phone Error",
        "Gender Error",
        "Joining Date Error",
        "Contract Date Error",
        "Employee No Error",
        "Basic Salary Error",
        "Salary Hour Error",
        "User ID Error",
        "Company Error",
    ]
}


def chunked(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def normalize_phone(phone):
    phone = str(phone).strip()
    if phone.startswith("+"):
        return "+" + re.sub(r"\D", "", phone[1:])
    return re.sub(r"\D", "", phone)


def import_valid_date(date_value, field_label, errors_dict, error_key):
    if pd.isna(date_value) or date_value is None or str(date_value).strip() == "":
        return None

    if isinstance(date_value, datetime):
        return date_value.date()

    date_str = str(date_value).strip()
    date_formats = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]

    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    errors_dict[error_key] = (
        f"{field_label} is not a valid date. Expected formats: YYYY-MM-DD, DD/MM/YYYY"
    )
    return None


def clean_employee_no(value):
    """
    Cleans and converts an employee no value from Excel import.

    - If the value is a whole number (e.g., 5480.0), returns it as an integer string ("5480").
    - If the value is a decimal (e.g., 567.67), returns it as a float string ("567.67").
    - If the value is a non-numeric string (e.g., "A101"), returns the stripped string.
    - If the value is NaN or None, returns an empty string.
    """
    if pd.isna(value):
        return ""

    try:
        float_val = float(value)
        if float_val.is_integer():
            return str(int(float_val))
        else:
            return str(float_val)
    except (ValueError, TypeError):
        return str(value).strip()


def convert_nan(field, dicts):
    """
    This method is returns None or field value
    """
    field_value = dicts.get(field)
    try:
        float(field_value)
        return None
    except ValueError:
        return field_value


def dynamic_prefix_sort(item):
    """
    Sorts items based on a dynamic prefix length.
    """
    # Assuming the dynamic prefix length is 3
    prefix = get_initial_prefix(None)["get_initial_prefix"]

    prefix_length = len(prefix) if len(prefix) >= 3 else 3
    return item[:prefix_length]


def get_ordered_employee_nos():
    """
    This method is used to return ordered employee nos
    """
    employees = Employee.objects.entire()
    data = (
        employees.exclude(employee_no=None)
        .order_by("employee_no")
        .values_list("employee_no", flat=True)
    )
    if not data.first():
        data = [
            f'{get_initial_prefix(None)["get_initial_prefix"]}0001',
        ]
    # Separate pure number strings and convert them to integers
    pure_numbers = [int(item) for item in data if item.isdigit()]

    # Remove pure number strings from the original data
    data = [item for item in data if not item.isdigit()]

    # Sort the remaining data by dynamic prefixes
    sorted_data = sorted(data, key=dynamic_prefix_sort)

    # Group the sorted data by dynamic prefixes
    grouped_data = [
        list(group) for _, group in groupby(sorted_data, key=dynamic_prefix_sort)
    ]

    # Sort each subgroup alphabetically and numerically
    for group in grouped_data:
        group.sort()
        filtered_group = [
            item for item in group if any(char.isdigit() for char in item)
        ]
        filtered_group.sort(key=lambda x: int("".join(filter(str.isdigit, x))))

    # Create a list containing the first and last items from each group
    result = [[group[0], group[-1]] for group in grouped_data]

    # Add the list of pure numbers at the beginning
    if pure_numbers:
        result.insert(0, [pure_numbers[0], pure_numbers[-1]])
    return result


def check_relationship_with_employee_model(model):
    """
    Checks the relationship of a given model with the Employee model.

    This function iterates through all the fields of the specified model
    and identifies fields that are either `ForeignKey` or `ManyToManyField`
    and are related to the `Employee` model. For each such field, it adds
    the field name and the type of relationship to a list.
    """
    related_fields = []
    for field in model._meta.get_fields():
        # Check if the field is a ForeignKey or ManyToManyField and related to Employee
        if isinstance(field, models.ForeignKey) and field.related_model == Employee:
            related_fields.append((field.name, "ForeignKey"))
        elif (
            isinstance(field, models.ManyToManyField)
            and field.related_model == Employee
        ):
            related_fields.append((field.name, "ManyToManyField"))
    return related_fields


def valid_import_file_headers(data_frame):
    if data_frame.empty:
        message = _("The uploaded file is empty, Not contain records.")
        return False, message

    required_keys = [
        "Employee No",
        "First Name",
        "Last Name",
        "Phone",
        "Email",
        "Gender",
        "Department",
        "Job Position",
        "Job Role",
        "Work Type",
        "Shift Information",
        "Employee Type",
        "Reporting Manager",
        "Company",
        "Work Location",
        "Joining Date",
        "Salary",
        "Salary Hour",
    ]

    missing_keys = [key for key in required_keys if key not in data_frame.columns]
    if missing_keys:
        message = _(
            "These required headers are missing in the uploaded file: "
        ) + ", ".join(missing_keys)
        return False, message
    return True, ""


def process_employee_records(data_frame):

    email_regex = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
    phone_regex = re.compile(r"^\+?\d{10,15}$")
    allowed_genders = frozenset(choice[0] for choice in Employee.choice_gender)
    # Preload entire employee nos for duplicate validation during import
    existing_employee_nos = frozenset(
        Employee.objects.entire().values_list("employee_no", flat=True)
    )
    existing_usernames = frozenset(User.objects.values_list("username", flat=True))
    existing_name_emails = frozenset(
        (fname, lname, email)
        for fname, lname, email in Employee.objects.values_list(
            "employee_first_name", "employee_last_name", "email"
        )
    )
    existing_companies = frozenset(Company.objects.values_list("company", flat=True))
    success_list, error_list = [], []
    employee_dicts = data_frame.to_dict("records")

    created_count = 0
    seen_employee_nos = set(existing_employee_nos)
    seen_usernames = set(existing_usernames)
    seen_name_emails = set(existing_name_emails)

    today = date.today()

    for emp in employee_dicts:
        errors = {}
        save = True

        email = str(emp.get("Email", "")).strip().lower()
        raw_phone = emp.get("Phone", "")
        phone = normalize_phone(raw_phone)
        employee_no = clean_employee_no(emp.get("Employee No"))
        first_name = convert_nan("First Name", emp)
        last_name = convert_nan("Last Name", emp)
        gender = str(emp.get("Gender") or "").strip().lower()
        company = convert_nan("Company", emp)
        basic_salary = convert_nan("Salary", emp)
        salary_hour = convert_nan("Salary Hour", emp)

        # Date validation
        joining_date = import_valid_date(
            emp.get("Joining Date"), "Joining Date", errors, "Joining Date Error"
        )
        if joining_date:
            if joining_date > today:
                errors["Joining Date Error"] = "Joining date cannot be in the future."
                save = False

        contract_end_date = import_valid_date(
            emp.get("End Date"),
            "End Date",
            errors,
            "Contract Date Error",
        )
        if contract_end_date and joining_date and contract_end_date < joining_date:
            errors["Contract Date Error"] = (
                "Contract end date cannot be before joining date."
            )
            save = False

        # Optional date: Date of Birth
        import_valid_date(emp.get("Date of Birth"), "Date of Birth", errors, "DOB Error")

        # Optional Work Email
        work_email = str(emp.get("Work Email") or "").strip().lower()
        if work_email and not email_regex.match(work_email):
            errors["Work Email Error"] = "Invalid work email address."
            save = False

        # Optional Work Phone
        raw_work_phone = emp.get("Work Phone", "")
        if raw_work_phone:
            emp["Work Phone"] = normalize_phone(raw_work_phone)

        # Email validation
        if not email or not email_regex.match(email):
            errors["Email Error"] = "Invalid email address."
            save = False

        # Name validation
        if not first_name:
            errors["First Name Error"] = "First name cannot be empty."
            save = False

        # Phone validation
        if not phone_regex.match(phone):
            errors["Phone Error"] = "Invalid phone number format."
            save = False

        # Employee No validation
        if not employee_no:
            errors["Employee No Error"] = "Employee No cannot be empty."
            save = False

        elif employee_no in seen_employee_nos:
            errors["Employee No Error"] = "An employee with this employee no already exists."
            save = False

        else:
            # Ensure consistent type (convert to string if needed)
            employee_no = str(employee_no).strip()
            emp["Employee No"] = employee_no
            seen_employee_nos.add(employee_no)

        # Username/email uniqueness
        if email in seen_usernames:
            errors["User ID Error"] = "User with this email already exists."
            save = False
        else:
            seen_usernames.add(email)

        # Name+email uniqueness
        name_email_tuple = (first_name, last_name, email)
        if name_email_tuple in seen_name_emails:
            errors["Name and Email Error"] = (
                "This employee already exists in the system."
            )
            save = False
        else:
            seen_name_emails.add(name_email_tuple)

        # Gender validation
        if gender and gender not in allowed_genders:
            errors["Gender Error"] = (
                f"Invalid gender. Allowed values: {', '.join(allowed_genders)}."
            )
            save = False

        # Company validation
        if company and company not in existing_companies:
            errors["Company Error"] = f"Company '{company}' does not exist."
            save = False

        # Salary validation
        if basic_salary not in [None, ""]:
            try:
                basic_salary_val = float(basic_salary)
                if basic_salary_val <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                errors["Basic Salary Error"] = "Basic salary must be a positive number."
                save = False

        if salary_hour not in [None, ""]:
            try:
                salary_hour_val = float(salary_hour)
                if salary_hour_val < 0:
                    raise ValueError
            except (ValueError, TypeError):
                errors["Salary Hour Error"] = (
                    "Salary hour must be a non-negative number."
                )
                save = False

        # Parse Is Active (optional boolean, default True)
        raw_is_active = emp.get("Is Active")
        if raw_is_active in (None, ""):
            emp["Is Active"] = True
        else:
            emp["Is Active"] = str(raw_is_active).strip().lower() in ("true", "yes", "1", "active")

        # Final processing
        if save:
            emp["Phone"] = phone
            emp["Joining Date"] = joining_date
            emp["End Date"] = contract_end_date
            success_list.append(emp)
            created_count += 1
        else:
            emp.update(errors)
            error_list.append(emp)

    return success_list, error_list, created_count


def bulk_create_user_import(success_lists):
    """
    Creates new User instances in bulk from a list of dictionaries containing user data.

    Returns:
        list: A list of created User instances. If no new users are created, returns an empty list.
    """
    emails = [row["Email"] for row in success_lists]
    existing_usernames = (
        set(User.objects.filter(username__in=emails).values_list("username", flat=True))
        if is_postgres
        else set(
            chain.from_iterable(
                User.objects.filter(username__in=chunk).values_list(
                    "username", flat=True
                )
                for chunk in chunked(emails, 999)
            )
        )
    )

    users_to_create = [
        User(
            username=row["Email"],
            email=row["Email"],
            password=str(row["Phone"]).strip(),
            is_superuser=False,
        )
        for row in success_lists
        if row["Email"] not in existing_usernames
    ]

    created_users = []
    if users_to_create:
        with transaction.atomic():
            created_users = User.objects.bulk_create(
                users_to_create, batch_size=None if is_postgres else 999
            )
    return created_users


def bulk_create_employee_import(success_lists):
    """
    Creates Employee instances in bulk based on imported data.
    Uses adaptive chunking for compatibility with SQLite, avoids chunking in PostgreSQL.
    """
    emails = [row["Email"] for row in success_lists]
    is_postgres = connection.vendor == "postgresql"

    existing_users = {
        user.username: user
        for user in (
            User.objects.filter(username__in=emails).only("id", "username")
            if is_postgres
            else chain.from_iterable(
                User.objects.filter(username__in=chunk).only("id", "username")
                for chunk in chunked(emails, 999)
            )
        )
    }

    def _parse_children(row):
        val = convert_nan("Children", row)
        if val in (None, ""):
            return None
        try:
            return int(float(str(val)))
        except (ValueError, TypeError):
            return None

    employees_to_create = [
        Employee(
            employee_user_id=existing_users[row["Email"]],
            employee_no=row["Employee No"],
            employee_first_name=convert_nan("First Name", row),
            employee_middle_name=convert_nan("Middle Name", row) or None,
            employee_last_name=convert_nan("Last Name", row),
            employee_extension=convert_nan("Extension", row) or None,
            email=row["Email"],
            phone=row["Phone"],
            gender=row.get("Gender", "").lower(),
            qualification=convert_nan("Qualification", row) or None,
            dob=row.get("Date of Birth") or None,
            marital_status=convert_nan("Marital Status", row) or None,
            children=_parse_children(row),
            address=convert_nan("Address", row) or None,
            city=convert_nan("City", row) or None,
            state=convert_nan("State", row) or None,
            country=convert_nan("Country", row) or None,
            zip=convert_nan("Zip Code", row) or None,
            emergency_contact_name=convert_nan("Emergency Contact Name", row) or None,
            emergency_contact=convert_nan("Emergency Contact", row) or None,
            emergency_contact_relation=convert_nan("Emergency Contact Relation", row) or None,
            tin_number=convert_nan("TIN Number", row) or None,
            sss_number=convert_nan("SSS Number", row) or None,
            hdmf_number=convert_nan("HDMF Number", row) or None,
            philhealth_number=convert_nan("PhilHealth Number", row) or None,
            is_active=row.get("Is Active", True),
        )
        for row in success_lists
        if row["Email"] in existing_users
    ]

    created_employees = []
    if employees_to_create:
        with transaction.atomic():
            created_employees = Employee.objects.bulk_create(
                employees_to_create, batch_size=None if is_postgres else 999
            )

    return created_employees


def set_initial_password(employees):
    """
    method to set initial password
    """

    logger.info("started to set initial password")
    for employee in employees:
        try:
            employee.employee_user_id.set_password(str(employee.phone))
            employee.employee_user_id.save()
        except Exception as e:
            logger.error(f"falied to set initial password for {employee}")
    logger.info("initial password configured")


def optimize_reporting_manager_lookup():
    """
    Optimizes the lookup of reporting managers from a list of work information.

    This function identifies unique reporting manager names from the provided
    list of work information, queries all matching `Employee` objects in a
    single database query, and creates a dictionary for quick lookups based
    on the full name of the reporting managers.
    """
    employees = Employee.objects.entire()

    employee_dict = {
        f"{employee.employee_first_name} {employee.employee_last_name}": employee
        for employee in employees
    }
    return employee_dict


def bulk_create_department_import(success_lists):
    """
    Bulk creation of department instances based on the excel import of employees
    """
    departments_to_import = {
        dept
        for work_info in success_lists
        if (dept := convert_nan("Department", work_info))
    }

    existing_departments = set(Department.objects.values_list("department", flat=True))

    new_departments = [
        Department(department=dept)
        for dept in departments_to_import - existing_departments
    ]

    if new_departments:
        with transaction.atomic():
            Department.objects.bulk_create(
                new_departments, batch_size=None if is_postgres else 999
            )


def bulk_create_job_position_import(success_lists):
    """
    Optimized: Bulk creation of job position instances based on the Excel import of employees.
    """

    # Step 1: Extract unique (job_position, department_name) pairs
    job_positions_to_import = {
        (convert_nan("Job Position", item), convert_nan("Department", item))
        for item in success_lists
        if convert_nan("Job Position", item) and convert_nan("Department", item)
    }

    if not job_positions_to_import:
        return  # No valid data to import

    # Step 2: Fetch all departments at once and build a name -> object map
    department_objs = Department.objects.only("id", "department")
    department_lookup = {dep.department: dep for dep in department_objs}

    # Step 3: Filter out entries with unknown departments
    valid_pairs = [
        (jp, department_lookup[dept])
        for jp, dept in job_positions_to_import
        if dept in department_lookup
    ]

    if not valid_pairs:
        return  # No valid (job_position, department_id) pairs to process

    # Step 4: Fetch existing job positions
    existing_pairs = set(
        JobPosition.objects.filter(
            department_id__in={dept_id for _, dept_id in valid_pairs}
        ).values_list("job_position", "department_id")
    )

    # Step 5: Create list of new JobPosition instances
    new_positions = [
        JobPosition(job_position=jp, department_id=dept_id)
        for jp, dept_id in valid_pairs
        if (jp, dept_id) not in existing_pairs
    ]

    # Step 6: Bulk create in a transaction
    if new_positions:
        with transaction.atomic():
            JobPosition.objects.bulk_create(
                new_positions, batch_size=None if is_postgres else 999
            )


def bulk_create_job_role_import(success_lists):
    """
    Bulk creation of job role instances based on the excel import of employees
    """
    # Extract unique (job_role, job_position) pairs, filtering out empty values
    job_roles_to_import = {
        (role, pos)
        for work_info in success_lists
        if (role := convert_nan("Job Role", work_info))
        and (pos := convert_nan("Job Position", work_info))
    }

    # Prefetch existing data efficiently
    job_positions = JobPosition.objects.only("id", "job_position")
    existing_job_roles = set(JobRole.objects.values_list("job_role", "job_position_id"))

    # Create new job roles
    new_job_roles = [
        JobRole(job_role=role, job_position_id=job_positions[pos].id)
        for role, pos in job_roles_to_import
        if pos in job_positions
        and (role, job_positions[pos].id) not in existing_job_roles
    ]

    # Bulk create if there are new roles
    if new_job_roles:
        with transaction.atomic():
            JobRole.objects.bulk_create(
                new_job_roles, batch_size=None if is_postgres else 999
            )


def bulk_create_work_types(success_lists):
    """
    Bulk creation of work type instances based on the excel import of employees
    """
    # Extract unique work types, filtering out None values
    work_types_to_import = {
        wt for work_info in success_lists if (wt := convert_nan("Work Type", work_info))
    }

    # Get existing work types in one optimized query
    existing_work_types = set(WorkType.objects.values_list("work_type", flat=True))

    # Create new work type objects
    new_work_types = [
        WorkType(work_type=wt) for wt in work_types_to_import - existing_work_types
    ]

    # Bulk create if there are new work types
    if new_work_types:
        with transaction.atomic():
            WorkType.objects.bulk_create(
                new_work_types, batch_size=None if is_postgres else 999
            )


def bulk_create_shifts(success_lists):
    """
    Bulk creation of shift instances based on the excel import of employees
    """
    # Extract unique shifts, filtering out None values
    shifts_to_import = {
        shift
        for work_info in success_lists
        if (shift := convert_nan("Shift Information", work_info))
    }

    # Get existing shifts in one optimized query
    existing_shifts = set(
        EmployeeShift.objects.values_list("employee_shift", flat=True)
    )

    # Create new shift objects
    new_shifts = [
        EmployeeShift(employee_shift=shift)
        for shift in shifts_to_import - existing_shifts
    ]

    # Bulk create if there are new shifts
    if new_shifts:
        with transaction.atomic():
            EmployeeShift.objects.bulk_create(
                new_shifts, batch_size=None if is_postgres else 999
            )


def bulk_create_employee_types(success_lists):
    """
    Bulk creation of employee type instances based on the excel import of employees
    """
    # Extract unique employee types, filtering out None values
    employee_types_to_import = {
        et
        for work_info in success_lists
        if (et := convert_nan("Employee Type", work_info))
    }

    # Get existing employee types in one optimized query
    existing_employee_types = set(
        EmployeeType.objects.values_list("employee_type", flat=True)
    )

    # Create new employee type objects
    new_employee_types = [
        EmployeeType(employee_type=et)
        for et in employee_types_to_import - existing_employee_types
    ]

    # Bulk create if there are new types
    if new_employee_types:
        with transaction.atomic():
            EmployeeType.objects.bulk_create(
                new_employee_types, batch_size=None if is_postgres else 999
            )


def create_contracts_in_thread(new_work_info_list, update_work_info_list):
    """
    Creates employee contracts in bulk based on provided work information.
    """
    from payroll.models.models import Contract

    def get_or_none(value):
        return value if value else None

    contracts_list = [
        Contract(
            contract_name=f"{work_info.employee_id}'s Contract",
            employee_id=work_info.employee_id,
            contract_start_date=(
                work_info.date_joining if work_info.date_joining else datetime.today()
            ),
            department=get_or_none(work_info.department_id),
            job_position=get_or_none(work_info.job_position_id),
            job_role=get_or_none(work_info.job_role_id),
            shift=get_or_none(work_info.shift_id),
            work_type=get_or_none(work_info.work_type_id),
            wage=work_info.basic_salary or 0,
        )
        for work_info in new_work_info_list + update_work_info_list
        if work_info.employee_id
    ]

    Contract.objects.bulk_create(contracts_list)


def bulk_create_work_info_import(success_lists):
    """
    Bulk creation of employee work info instances based on the excel import of employees
    """
    new_work_info_list = []
    update_work_info_list = []

    employee_nos = [row["Employee No"] for row in success_lists]
    departments = set(row.get("Department") for row in success_lists)
    job_positions = set(row.get("Job Position") for row in success_lists)
    job_roles = set(row.get("Job Role") for row in success_lists)
    work_types = set(row.get("Work Type") for row in success_lists)
    employee_types = set(row.get("Employee Type") for row in success_lists)
    shifts = set(row.get("Shift Information") for row in success_lists)
    companies = set(row.get("Company") for row in success_lists)
    branches = set(row.get("Branch") for row in success_lists if row.get("Branch"))
    cost_centers = set(row.get("Cost Center") for row in success_lists if row.get("Cost Center"))
    business_units = set(row.get("Business Unit") for row in success_lists if row.get("Business Unit"))

    chunk_size = None if is_postgres else 999
    employee_qs = (
        chain.from_iterable(
            Employee.objects.entire().filter(employee_no__in=chunk).only("employee_no")
            for chunk in chunked(employee_nos, chunk_size)
        )
        if chunk_size
        else Employee.objects.entire().filter(employee_no__in=employee_nos).only("employee_no")
    )

    existing_employees = {emp.employee_no: emp for emp in employee_qs}

    existing_employee_work_infos = {
        emp.employee_id: emp
        for emp in (
            EmployeeWorkInformation.objects.filter(
                employee_id__in=existing_employees.values()
            ).only("employee_id")
            if is_postgres
            else chain.from_iterable(
                EmployeeWorkInformation.objects.filter(employee_id__in=chunk).only(
                    "employee_id"
                )
                for chunk in chunked(list(existing_employees.values()), 900)
            )
        )
    }

    existing_departments = {
        dep.department: dep
        for dep in Department.objects.filter(department__in=departments).only(
            "department"
        )
    }
    existing_job_positions = {
        (jp.department_id, jp.job_position): jp
        for jp in JobPosition.objects.filter(job_position__in=job_positions)
        .select_related("department_id")
        .only("department_id", "job_position")
    }
    existing_job_roles = {
        (jr.job_position_id, jr.job_role): jr
        for jr in JobRole.objects.filter(job_role__in=job_roles)
        .select_related("job_position_id")
        .only("job_position_id", "job_role")
    }
    existing_work_types = {
        wt.work_type: wt
        for wt in WorkType.objects.filter(work_type__in=work_types).only("work_type")
    }
    existing_shifts = {
        es.employee_shift: es
        for es in EmployeeShift.objects.filter(employee_shift__in=shifts).only(
            "employee_shift"
        )
    }
    existing_employee_types = {
        et.employee_type: et
        for et in EmployeeType.objects.filter(employee_type__in=employee_types).only(
            "employee_type"
        )
    }
    existing_companies = {
        comp.company: comp
        for comp in Company.objects.filter(company__in=companies).only("company")
    }
    existing_branches = {
        b.branch: b
        for b in Branch.objects.filter(branch__in=branches).only("branch")
    }
    existing_cost_centers = {
        cc.name: cc
        for cc in CostCenter.objects.filter(name__in=cost_centers).only("name")
    }
    existing_business_units = {
        bu.name: bu
        for bu in BusinessUnit.objects.filter(name__in=business_units).only("name")
    }
    reporting_manager_dict = optimize_reporting_manager_lookup()

    for work_info in success_lists:
        employee_no = work_info["Employee No"]
        employee_obj = existing_employees.get(employee_no)
        if not employee_obj:
            continue

        email = work_info["Email"]
        employee_work_info = existing_employee_work_infos.get(employee_obj)
        department_obj = existing_departments.get(work_info.get("Department"))

        job_position_key = (
            existing_departments.get(work_info.get("Department")),
            work_info.get("Job Position"),
        )
        job_position_obj = existing_job_positions.get(job_position_key)

        job_role_key = (
            job_position_obj,
            work_info.get("Job Role"),
        )
        job_role_obj = existing_job_roles.get(job_role_key)

        work_type_obj = existing_work_types.get(work_info.get("Work Type"))
        employee_type_obj = existing_employee_types.get(work_info.get("Employee Type"))
        shift_obj = existing_shifts.get(work_info.get("Shift Information"))
        reporting_manager = work_info.get("Reporting Manager")
        reporting_manager_obj = None
        if isinstance(reporting_manager, str) and " " in reporting_manager:
            if reporting_manager in reporting_manager_dict:
                reporting_manager_obj = reporting_manager_dict[reporting_manager]

        company_obj = existing_companies.get(work_info.get("Company"))
        branch_obj = existing_branches.get(work_info.get("Branch"))
        cost_center_obj = existing_cost_centers.get(work_info.get("Cost Center"))
        business_unit_obj = existing_business_units.get(work_info.get("Business Unit"))
        employee_status = work_info.get("Employee Status") or None
        location = work_info.get("Work Location")
        work_email = convert_nan("Work Email", work_info) or None
        work_phone = convert_nan("Work Phone", work_info) or None
        experience_val = convert_nan("Experience", work_info)
        try:
            experience_val = float(experience_val) if experience_val not in (None, "") else None
        except (ValueError, TypeError):
            experience_val = None

        # Parsing dates and salary
        _dj = work_info.get("Joining Date")
        date_joining = _dj if (_dj is not None and not pd.isnull(_dj)) else datetime.today()

        _ce = work_info.get("End Date")
        contract_end_date = _ce if (_ce is not None and not pd.isnull(_ce)) else None

        basic_salary = (
            convert_nan("Salary", work_info)
            if type(convert_nan("Salary", work_info)) is int
            else 0
        )
        salary_hour = (
            convert_nan("Salary Hour", work_info)
            if type(convert_nan("Salary Hour", work_info)) is int
            else 0
        )

        if employee_work_info is None:
            # Create a new instance
            employee_work_info = EmployeeWorkInformation(
                employee_id=employee_obj,
                email=work_email,
                mobile=work_phone,
                department_id=department_obj,
                job_position_id=job_position_obj,
                job_role_id=job_role_obj,
                work_type_id=work_type_obj,
                employee_type_id=employee_type_obj,
                shift_id=shift_obj,
                reporting_manager_id=reporting_manager_obj,
                company_id=company_obj,
                branch_id=branch_obj,
                cost_center_id=cost_center_obj,
                business_unit_id=business_unit_obj,
                employee_status=employee_status,
                location=location,
                date_joining=(
                    date_joining if not pd.isnull(date_joining) else datetime.today()
                ),
                contract_end_date=(
                    contract_end_date if not pd.isnull(contract_end_date) else None
                ),
                basic_salary=basic_salary,
                salary_hour=salary_hour,
                experience=experience_val,
            )
            new_work_info_list.append(employee_work_info)
        else:
            # Update the existing instance
            employee_work_info.email = work_email
            employee_work_info.mobile = work_phone
            employee_work_info.department_id = department_obj
            employee_work_info.job_position_id = job_position_obj
            employee_work_info.job_role_id = job_role_obj
            employee_work_info.work_type_id = work_type_obj
            employee_work_info.employee_type_id = employee_type_obj
            employee_work_info.shift_id = shift_obj
            employee_work_info.reporting_manager_id = reporting_manager_obj
            employee_work_info.company_id = company_obj
            employee_work_info.branch_id = branch_obj
            employee_work_info.cost_center_id = cost_center_obj
            employee_work_info.business_unit_id = business_unit_obj
            employee_work_info.employee_status = employee_status
            employee_work_info.location = location
            employee_work_info.date_joining = (
                date_joining if not pd.isnull(date_joining) else datetime.today()
            )
            employee_work_info.contract_end_date = (
                contract_end_date if not pd.isnull(contract_end_date) else None
            )
            employee_work_info.basic_salary = basic_salary
            employee_work_info.salary_hour = salary_hour
            employee_work_info.experience = experience_val
            update_work_info_list.append(employee_work_info)
    if new_work_info_list:
        EmployeeWorkInformation.objects.bulk_create(
            new_work_info_list, batch_size=None if is_postgres else 999
        )
    if update_work_info_list:
        EmployeeWorkInformation.objects.bulk_update(
            update_work_info_list,
            [
                "email",
                "mobile",
                "department_id",
                "job_position_id",
                "job_role_id",
                "work_type_id",
                "employee_type_id",
                "shift_id",
                "reporting_manager_id",
                "company_id",
                "branch_id",
                "cost_center_id",
                "business_unit_id",
                "employee_status",
                "location",
                "date_joining",
                "contract_end_date",
                "basic_salary",
                "salary_hour",
                "experience",
            ],
            batch_size=None if is_postgres else 999,
        )
    if apps.is_installed("payroll"):

        contract_creation_thread = threading.Thread(
            target=create_contracts_in_thread,
            args=(new_work_info_list, update_work_info_list),
        )
        contract_creation_thread.start()


def bulk_create_bank_details_import(success_lists):
    """
    Creates EmployeeBankDetails for Gcash and Metrobank columns from the import.
    """
    employee_nos = [row["Employee No"] for row in success_lists]
    existing_employees = {
        emp.employee_no: emp
        for emp in Employee.objects.entire().filter(employee_no__in=employee_nos).only("employee_no")
    }

    existing_bank_keys = set(
        EmployeeBankDetails.objects.filter(
            employee_id__in=existing_employees.values()
        ).values_list("employee_id", "bank_name")
    )

    bank_details_to_create = []
    for row in success_lists:
        emp = existing_employees.get(row["Employee No"])
        if not emp:
            continue
        for col, bank_name in [("Gcash", "GCash"), ("Metrobank", "Metrobank")]:
            account_no = convert_nan(col, row)
            if account_no and (emp.pk, bank_name) not in existing_bank_keys:
                bank_details_to_create.append(
                    EmployeeBankDetails(
                        employee_id=emp,
                        bank_name=bank_name,
                        account_number=str(account_no).strip(),
                    )
                )
                existing_bank_keys.add((emp.pk, bank_name))

    if bank_details_to_create:
        with transaction.atomic():
            EmployeeBankDetails.objects.bulk_create(
                bank_details_to_create,
                batch_size=None if is_postgres else 999,
                ignore_conflicts=True,
            )


def bulk_set_tags_import(success_lists):
    """
    Sets M2M tags on EmployeeWorkInformation from the Tags column (comma-separated).
    """
    all_tag_titles = {
        title.strip()
        for row in success_lists
        for title in str(row.get("Tags") or "").split(",")
        if title.strip()
    }
    if not all_tag_titles:
        return

    tag_map = {}
    for title in all_tag_titles:
        tag, _ = EmployeeTag.objects.get_or_create(title=title)
        tag_map[title] = tag

    employee_nos = [row["Employee No"] for row in success_lists if str(row.get("Tags") or "").strip()]
    existing_employees = {
        emp.employee_no: emp
        for emp in Employee.objects.entire().filter(employee_no__in=employee_nos).only("employee_no")
    }

    work_infos = {
        wi.employee_id_id: wi
        for wi in EmployeeWorkInformation.objects.filter(
            employee_id__in=existing_employees.values()
        ).only("employee_id")
    }

    for row in success_lists:
        tags_raw = str(row.get("Tags") or "").strip()
        if not tags_raw:
            continue
        emp = existing_employees.get(row["Employee No"])
        if not emp:
            continue
        wi = work_infos.get(emp.pk)
        if not wi:
            continue
        tag_objs = [tag_map[t.strip()] for t in tags_raw.split(",") if t.strip() in tag_map]
        if tag_objs:
            wi.tags.add(*tag_objs)
