from datetime import date, time

from django.test import TestCase, Client

from accounts.models import User, Role
from machines.models import Machine
from patients.models import Patient
from seances.models import Seance, PostSessionMeasurements, RapportSeance


class RapportViewerTests(TestCase):
    def setUp(self):
        self.client = Client()
        role = Role.objects.create(name="Admin")
        self.user = User.objects.create(
            username="admin_test",
            password="password123",
            role=role,
            email="admin@test.com",
            first_login=False,
        )
        self.patient = Patient.objects.create(
            first_name="Sami",
            last_name="Ben Ali",
            date_of_birth=date(1975, 3, 10),
            age=50,
            groupe_sanguin="A+",
            type_de_dialyse="Hémodialyse",
        )
        self.machine = Machine.objects.create(
            machine_id="M200",
            model="Model-Y",
            manufacturer="DialysisCorp",
            status="Prete",
            location="Salle 2",
        )

    def _login(self):
        session = self.client.session
        session["app_user_id"] = self.user.id
        session.save()

    def test_web_completion_generates_rapport_and_viewer_renders(self):
        self._login()
        seance = Seance.objects.create(
            patient=self.patient,
            machine=self.machine,
            session_date=date.today(),
            start_hour=time(8, 0),
            duration=4,
            status="en cours",
        )
        PostSessionMeasurements.objects.create(
            seance=seance,
            weight=70.0,
            blood_pressure="120/80",
            heart_rate=70,
            temperature=36.6,
            saturation=98.0,
        )

        # POST the post-session form (same shape as post_session_page).
        response = self.client.post(
            f"/seances/{seance.id}/post/",
            {
                "weight": "70.0",
                "blood_pressure": "120/80",
                "temperature": "36.6",
                "heart_rate": "70",
                "saturation": "98.0",
                "complications": "",
            },
        )
        self.assertEqual(response.status_code, 302)  # redirect to planning

        seance.refresh_from_db()
        self.assertEqual(seance.status, "terminée")
        rapport = RapportSeance.objects.filter(seance=seance).first()
        self.assertIsNotNone(rapport)
        self.assertTrue(rapport.contenu_html.strip())
        self.assertEqual(RapportSeance.objects.filter(seance=seance).count(), 1)

        # The detail page renders rapport_viewer with the generated HTML.
        detail = self.client.get(f"/patients/{seance.id}/detail/")
        self.assertEqual(detail.status_code, 200)
        content = detail.content.decode("utf-8")
        self.assertIn("rapport-content", content)
        self.assertIn(self.patient.last_name, content)
        self.assertIn(self.machine.machine_id, content)
