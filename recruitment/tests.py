import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from base.models import Company, Department, JobPosition
from employee.models import Employee
from recruitment.models import Candidate, CandidateRating, Recruitment, Stage


class RecruitmentPipelineCacheFallbackTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.mkdtemp(prefix="recruitment-tests-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._temp_media, ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="pipeline-admin",
            password="test-pass-123",
            is_staff=True,
            is_superuser=True,
        )
        cls.employee = Employee.objects.create(
            employee_user_id=cls.user,
            employee_first_name="Pipeline",
            employee_last_name="Admin",
            email="pipeline-admin@example.com",
            phone="09123456789",
        )

        cls.company = Company.objects.create(
            company="MDC",
            address="Address",
            country="PH",
            state="NCR",
            city="Makati",
            zip="1200",
        )
        cls.department = Department(department="Engineering")
        cls.department.save()
        cls.department.company_id.add(cls.company)
        cls.job_position = JobPosition(
            job_position="QA Engineer",
            department_id=cls.department,
        )
        cls.job_position.save()
        cls.job_position.company_id.add(cls.company)

        cls.recruitment = Recruitment.objects.create(
            title="Pipeline Recruitment",
            description="Pipeline fallback test recruitment",
            vacancy=1,
            is_published=False,
            job_position_id=cls.job_position,
            company_id=cls.company,
        )
        cls.recruitment.open_positions.add(cls.job_position)

        cls.stage = Stage.objects.create(
            recruitment_id=cls.recruitment,
            stage="Screening",
            stage_type="initial",
            sequence=1,
        )
        resume = SimpleUploadedFile(
            "resume.pdf",
            b"%PDF-1.4\n%Test\n",
            content_type="application/pdf",
        )
        cls.candidate = Candidate.objects.create(
            name="Alice Candidate",
            recruitment_id=cls.recruitment,
            job_position_id=cls.job_position,
            stage_id=cls.stage,
            email="alice.candidate@example.com",
            resume=resume,
        )

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["pipeline_test"] = True
        session.save()
        cache.delete(self._pipeline_cache_key())

    def _pipeline_cache_key(self):
        return f"{self.client.session.session_key}pipeline"

    def test_stage_component_renders_without_refresh_when_cache_is_empty(self):
        response = self.client.get(
            reverse("pipeline-stages-component", args=["list"]),
            {"rec_id": self.recruitment.id, "candidate_name": "Alice"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("HX-Refresh"))
        self.assertContains(response, "pipelineStageContainer")
        self.assertIsNotNone(cache.get(self._pipeline_cache_key()))

    def test_candidate_component_renders_for_list_and_card_without_cache(self):
        list_response = self.client.get(
            reverse("candidate-stage-component"),
            {"stage_id": self.stage.id, "candidate_name": "Alice", "view": "list"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertIsNone(list_response.headers.get("HX-Refresh"))
        self.assertContains(list_response, "Alice Candidate")

        cache.delete(self._pipeline_cache_key())

        card_response = self.client.get(
            reverse("candidate-stage-component"),
            {"stage_id": self.stage.id, "candidate_name": "Alice", "view": "card"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(card_response.status_code, 200)
        self.assertIsNone(card_response.headers.get("HX-Refresh"))
        self.assertContains(card_response, "oh-kanban__card")

    def test_candidate_stage_and_sequence_endpoints_work_without_cached_pipeline(self):
        cache.delete(self._pipeline_cache_key())

        response = self.client.get(
            reverse("update-candidate-stage-and-sequence"),
            {"stage_id": self.stage.id, "order": [self.candidate.id]},
        )
        self.assertEqual(response.status_code, 200)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.stage_id_id, self.stage.id)
        self.assertEqual(self.candidate.sequence, 0)

        self.candidate.sequence = 7
        self.candidate.save(update_fields=["sequence"])
        cache.delete(self._pipeline_cache_key())

        response = self.client.get(
            reverse("update-candidate-sequence"),
            {"stage_id": self.stage.id, "order": [self.candidate.id]},
        )
        self.assertEqual(response.status_code, 200)
        self.candidate.refresh_from_db()
        self.assertEqual(self.candidate.stage_id_id, self.stage.id)
        self.assertEqual(self.candidate.sequence, 0)

    def test_filter_query_is_propagated_to_stage_and_candidate_htmx_urls(self):
        query = {"candidate_name": "Alice", "view": "card", "closed": "false"}
        filter_response = self.client.get(
            reverse("pipeline-search"),
            query,
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(filter_response.status_code, 200)
        self.assertContains(
            filter_response,
            reverse("pipeline-stages-component", args=["card"]),
            html=False,
        )
        self.assertContains(filter_response, "candidate_name=Alice", html=False)
        self.assertContains(filter_response, "view=card", html=False)
        self.assertContains(filter_response, "closed=false", html=False)

        stage_response = self.client.get(
            reverse("pipeline-stages-component", args=["card"]),
            {"rec_id": self.recruitment.id, **query},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(stage_response.status_code, 200)
        self.assertContains(
            stage_response,
            f"{reverse('candidate-stage-component')}?stage_id={self.stage.id}",
            html=False,
        )
        self.assertContains(stage_response, "candidate_name=Alice", html=False)
        self.assertContains(stage_response, "view=card", html=False)
        self.assertContains(stage_response, "closed=false", html=False)


class RecruitmentCandidateRatingToggleTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._temp_media = tempfile.mkdtemp(prefix="recruitment-rating-tests-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._temp_media)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._temp_media, ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="rating-admin",
            password="test-pass-123",
            is_staff=True,
            is_superuser=True,
        )
        cls.employee = Employee.objects.create(
            employee_user_id=cls.user,
            employee_first_name="Rating",
            employee_last_name="Admin",
            email="rating-admin@example.com",
            phone="09123456780",
        )

        cls.company = Company.objects.create(
            company="MDC Rating",
            address="Address",
            country="PH",
            state="NCR",
            city="Makati",
            zip="1200",
        )
        cls.department = Department(department="Recruitment Rating")
        cls.department.save()
        cls.department.company_id.add(cls.company)
        cls.job_position = JobPosition(
            job_position="Recruiter",
            department_id=cls.department,
        )
        cls.job_position.save()
        cls.job_position.company_id.add(cls.company)

        cls.recruitment = Recruitment.objects.create(
            title="Rating Recruitment",
            description="Candidate rating tests",
            vacancy=1,
            is_published=False,
            job_position_id=cls.job_position,
            company_id=cls.company,
        )
        cls.recruitment.open_positions.add(cls.job_position)

        cls.stage = Stage.objects.create(
            recruitment_id=cls.recruitment,
            stage="Rating Stage",
            stage_type="initial",
            sequence=1,
        )
        resume = SimpleUploadedFile(
            "resume.pdf",
            b"%PDF-1.4\n%Test\n",
            content_type="application/pdf",
        )
        cls.candidate = Candidate.objects.create(
            name="Bob Rated",
            recruitment_id=cls.recruitment,
            job_position_id=cls.job_position,
            stage_id=cls.stage,
            email="bob.rated@example.com",
            resume=resume,
        )

    def setUp(self):
        self.client.force_login(self.user)
        CandidateRating.objects.all().delete()

    def test_create_candidate_rating_creates_when_positive(self):
        response = self.client.post(
            reverse("create-candidate-rating", args=[self.candidate.id]),
            {"rating": "4", "clear_rating": "0"},
        )

        self.assertEqual(response.status_code, 302)
        rating = CandidateRating.objects.get(
            candidate_id=self.candidate, employee_id=self.employee
        )
        self.assertEqual(rating.rating, 4)

    def test_create_candidate_rating_does_not_create_for_clear_or_zero(self):
        clear_response = self.client.post(
            reverse("create-candidate-rating", args=[self.candidate.id]),
            {"clear_rating": "1", "rating": "5"},
        )
        self.assertEqual(clear_response.status_code, 302)
        self.assertFalse(CandidateRating.objects.exists())

        zero_response = self.client.post(
            reverse("create-candidate-rating", args=[self.candidate.id]),
            {"clear_rating": "0", "rating": "0"},
        )
        self.assertEqual(zero_response.status_code, 302)
        self.assertFalse(CandidateRating.objects.exists())

    def test_update_candidate_rating_updates_then_clears(self):
        CandidateRating.objects.create(
            candidate_id=self.candidate, employee_id=self.employee, rating=4
        )

        update_response = self.client.post(
            reverse("update-candidate-rating", args=[self.candidate.id]),
            {"rating": "5", "clear_rating": "0"},
        )
        self.assertEqual(update_response.status_code, 302)

        rating = CandidateRating.objects.get(
            candidate_id=self.candidate, employee_id=self.employee
        )
        self.assertEqual(rating.rating, 5)

        clear_response = self.client.post(
            reverse("update-candidate-rating", args=[self.candidate.id]),
            {"clear_rating": "1"},
        )
        self.assertEqual(clear_response.status_code, 302)
        self.assertFalse(
            CandidateRating.objects.filter(
                candidate_id=self.candidate, employee_id=self.employee
            ).exists()
        )

    def test_rating_endpoints_are_post_only(self):
        create_url = reverse("create-candidate-rating", args=[self.candidate.id])
        update_url = reverse("update-candidate-rating", args=[self.candidate.id])
        factory = RequestFactory()

        create_response = resolve(create_url).func(
            factory.get(create_url), cand_id=self.candidate.id
        )
        update_response = resolve(update_url).func(
            factory.get(update_url), cand_id=self.candidate.id
        )

        self.assertEqual(create_response.status_code, 405)
        self.assertEqual(update_response.status_code, 405)
