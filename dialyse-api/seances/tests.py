import json

from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounts.models import User, Role
from machines.models import Machine
from patients.models import Patient
from seances.models import Seance
from seances.views import search_sessions


class SearchSessionsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.patient = Patient.objects.create(
            first_name="Alice",
            last_name="Durand",
            date_of_birth="1990-01-01",
            age=35,
            groupe_sanguin="A+",
            type_de_dialyse="Hémodialyse",
        )
        self.machine = Machine.objects.create(machine_id="M-100")
        self.session = Seance.objects.create(
            patient=self.patient,
            machine=self.machine,
            status="en cours",
            session_date=timezone.localdate(),
            start_hour="08:00:00",
        )

    def test_search_sessions_filters_active_sessions(self):
        request = self.factory.get(
            "/seances/search/",
            {"status": "en cours"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        response = search_sessions(request)

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["sessions"][0]["id"], str(self.session.id))


class CreateSessionPageTests(TestCase):
    """Regression tests for B4/B5: create_session GET redirect + role authorization."""

    def setUp(self):
        self.admin_role = Role.objects.create(name="Admin")
        self.doctor_role = Role.objects.create(name="Docteur")
        self.nurse_role = Role.objects.create(name="Infirmier")
        self.admin = User.objects.create(
            username="admin_cs", password="password123",
            role=self.admin_role, email="admin_cs@test.com", first_login=False,
        )
        self.doctor = User.objects.create(
            username="doc_cs", password="password123",
            role=self.doctor_role, email="doc_cs@test.com", first_login=False,
        )
        self.nurse = User.objects.create(
            username="nurse_cs", password="password123",
            role=self.nurse_role, email="nurse_cs@test.com", first_login=False,
        )
        self.patient = Patient.objects.create(
            first_name="Bob", last_name="Martin", date_of_birth="1985-05-05",
            age=40, groupe_sanguin="B+", type_de_dialyse="Hémodialyse",
        )
        self.machine = Machine.objects.create(machine_id="M-CS", status="Prete")

    def _login(self, user):
        session = self.client.session
        session["app_user_id"] = user.id
        session.save()

    # ---- B4: GET create_session must redirect to planning (no empty page) ----
    def test_get_create_session_redirects_to_planning(self):
        self._login(self.admin)
        res = self.client.get("/seances/create_session/")
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, "/seances/")

    # ---- B5: create_session authorization (web) ----
    def test_nurse_cannot_create_session_via_web(self):
        self._login(self.nurse)
        res = self.client.post(
            "/seances/create_session/",
            {
                "patient": str(self.patient.id),
                "session_date": "2026-08-20",
                "start_time": "09:00",
                "duration": "4",
                "machine": str(self.machine.id),
                "notes": "blocked",
                "debit": "60",
            },
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertEqual(res.url, "/error/")
        self.assertFalse(Seance.objects.filter(notes="blocked").exists())

    def test_doctor_can_create_session_via_web(self):
        self._login(self.doctor)
        res = self.client.post(
            "/seances/create_session/",
            {
                "patient": str(self.patient.id),
                "session_date": "2026-08-20",
                "start_time": "09:00",
                "duration": "4",
                "machine": str(self.machine.id),
                "notes": "ok-doc",
                "debit": "60",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(Seance.objects.filter(notes="ok-doc").exists())

    def test_admin_can_create_session_via_web(self):
        self._login(self.admin)
        res = self.client.post(
            "/seances/create_session/",
            {
                "patient": str(self.patient.id),
                "session_date": "2026-08-20",
                "start_time": "10:00",
                "duration": "4",
                "machine": str(self.machine.id),
                "notes": "ok-admin",
                "debit": "60",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(Seance.objects.filter(notes="ok-admin").exists())

    def test_anonymous_cannot_create_session_via_web(self):
        res = self.client.post(
            "/seances/create_session/",
            {
                "patient": str(self.patient.id),
                "session_date": "2026-08-20",
                "start_time": "11:00",
                "duration": "4",
                "machine": str(self.machine.id),
                "notes": "anon-blocked",
                "debit": "60",
            },
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertFalse(Seance.objects.filter(notes="anon-blocked").exists())

    def test_planning_button_hidden_for_nurse(self):
        self._login(self.nurse)
        res = self.client.get("/seances/")
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, 'id="newSessionBtn"')

    def test_planning_button_shown_for_admin(self):
        self._login(self.admin)
        res = self.client.get("/seances/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "newSessionBtn")
