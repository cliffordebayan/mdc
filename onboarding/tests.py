from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from onboarding.views import (
    ONBOARDING_PENDING_USERS_SESSION_KEY,
    employee_creation,
    user_creation,
    user_save,
)


class OnboardingPortalSessionFlowTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _attach_session_and_messages(self, request):
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        setattr(request, "_messages", FallbackStorage(request))

    @staticmethod
    def _build_candidate(email="candidate@example.com"):
        return SimpleNamespace(
            name="Candidate Name",
            mobile="+63900111222",
            address="123 Test Street",
            dob=None,
            email=email,
            recruitment_id=SimpleNamespace(company_id="Acme, Inc."),
        )

    def test_user_save_stores_pending_user_in_session(self):
        token = "token-step-1"
        candidate = self._build_candidate()
        portal = SimpleNamespace(
            token=token,
            used=False,
            count=0,
            candidate_id=candidate,
        )
        portal.save = MagicMock()

        request = self.factory.post(f"/onboarding/user-creation/{token}")
        self._attach_session_and_messages(request)

        unsaved_user = User(username="temporary")
        unsaved_user.password = "pbkdf2_sha256$test$hashed-password"
        form = MagicMock()
        form.save.return_value = unsaved_user

        response = user_save(form, portal, request, token)

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/onboarding/profile-view/{token}", response.url)
        self.assertEqual(portal.count, 1)
        portal.save.assert_called_once()
        form.save.assert_called_once_with(commit=False)

        pending_users = request.session.get(ONBOARDING_PENDING_USERS_SESSION_KEY, {})
        self.assertIn(token, pending_users)
        self.assertEqual(pending_users[token]["username"], candidate.email)
        self.assertEqual(pending_users[token]["password"], unsaved_user.password)

    @patch("onboarding.views.render", return_value=HttpResponse("ok"))
    @patch("onboarding.views.OnboardingEmployeePersonalForm")
    @patch("onboarding.views.Employee.objects.filter")
    @patch("onboarding.views.User.objects.filter")
    @patch("onboarding.views.OnboardingPortal.objects.filter")
    def test_employee_creation_uses_session_pending_user_when_db_user_missing(
        self,
        onboarding_portal_filter,
        user_filter,
        employee_filter,
        personal_form,
        _render,
    ):
        token = "token-step-3"
        candidate_email = "pending@example.com"
        candidate = self._build_candidate(email=candidate_email)
        portal = SimpleNamespace(
            token=token,
            used=False,
            count=2,
            candidate_id=candidate,
        )
        portal.save = MagicMock()

        onboarding_portal_filter.return_value.first.return_value = portal
        user_filter.return_value.first.return_value = None

        email_lookup_values = []

        def employee_filter_side_effect(*args, **kwargs):
            if "email" in kwargs:
                email_lookup_values.append(kwargs["email"])
                email_lookup = MagicMock()
                email_lookup.exists.return_value = False
                return email_lookup
            if "employee_user_id" in kwargs:
                employee_lookup = MagicMock()
                employee_lookup.first.return_value = None
                return employee_lookup
            raise AssertionError(f"Unexpected filter args: {args}, {kwargs}")

        employee_filter.side_effect = employee_filter_side_effect
        personal_form.return_value = MagicMock(name="OnboardingEmployeePersonalForm")

        request = self.factory.get(f"/onboarding/employee-creation/{token}")
        self._attach_session_and_messages(request)
        request.session[ONBOARDING_PENDING_USERS_SESSION_KEY] = {
            token: {
                "username": candidate_email,
                "password": "pbkdf2_sha256$session$hashed-password",
            }
        }
        request.session.save()

        response = employee_creation(request, token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
        self.assertEqual(len(email_lookup_values), 1)
        self.assertIsInstance(email_lookup_values[0], User)
        self.assertEqual(email_lookup_values[0].username, candidate_email)
        self.assertEqual(email_lookup_values[0].pk, None)
        portal.save.assert_not_called()

    @patch("onboarding.views.OnboardingPortal.objects.filter")
    @patch("onboarding.views.User.objects.filter")
    def test_employee_creation_missing_session_and_db_user_resets_to_step_one(
        self,
        user_filter,
        onboarding_portal_filter,
    ):
        token = "token-expired"
        candidate = self._build_candidate(email="expired@example.com")
        portal = SimpleNamespace(
            token=token,
            used=False,
            count=2,
            candidate_id=candidate,
        )
        portal.save = MagicMock()

        onboarding_portal_filter.return_value.first.return_value = portal
        user_filter.return_value.first.return_value = None

        request = self.factory.get(f"/onboarding/employee-creation/{token}")
        self._attach_session_and_messages(request)
        request.session[ONBOARDING_PENDING_USERS_SESSION_KEY] = {
            token: {
                "username": candidate.email,
                "password": "stale",
            }
        }
        request.session.pop(ONBOARDING_PENDING_USERS_SESSION_KEY)
        request.session.save()

        response = employee_creation(request, token)

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/onboarding/user-creation/{token}", response.url)
        self.assertEqual(portal.count, 0)
        portal.save.assert_called_once()
        self.assertNotIn(token, request.session.get(ONBOARDING_PENDING_USERS_SESSION_KEY, {}))

    @patch("onboarding.views.OnboardingPortal.objects.get")
    def test_user_creation_auto_forwards_to_current_step(self, onboarding_portal_get):
        token = "token-forward"
        candidate = self._build_candidate(email="progress@example.com")
        portal = SimpleNamespace(
            token=token,
            used=False,
            count=2,
            candidate_id=candidate,
        )
        onboarding_portal_get.return_value = portal

        request = self.factory.get(f"/onboarding/user-creation/{token}")
        self._attach_session_and_messages(request)

        response = user_creation(request, token)

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/onboarding/employee-creation/{token}", response.url)
