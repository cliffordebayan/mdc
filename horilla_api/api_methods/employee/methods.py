from base.models import *
from employee.models import *


def get_next_employee_no():
    """
    This method is used to generate employee no
    """
    from employee.methods.methods import get_next_employee_no as _get_next_employee_no

    return _get_next_employee_no()
