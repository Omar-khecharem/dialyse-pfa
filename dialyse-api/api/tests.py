import json
import base64
from datetime import date
from unittest import mock
from django.test import TestCase, Client, override_settings
from accounts.models import User, Role, PasswordResetRequest
from patients.models import Patient
from machines.models import Machine, RaspiDevice
from seances.models import Seance, Alert as SeanceAlert
from seances.models import PreSessionMeasurements, PostSessionMeasurements, RapportSeance
from monitoring.models import LiveMeasurement, Alerte

class Phase1ApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.doctor_role = Role.objects.create(name="Docteur")
        self.nurse_role = Role.objects.create(name="Infirmier")
        self.doctor_user = User.objects.create(
            username="doc_test",
            password="password123",
            role=self.doctor_role,
            email="doc@test.com",
        )
        self.nurse_user = User.objects.create(
            username="nurse_test",
            password="password123",
            role=self.nurse_role,
            email="nurse@test.com",
        )
        self.patient = Patient.objects.create(
            first_name="Ahmed",
            last_name="Ben Salah",
            date_of_birth=date(1980, 5, 20),
            age=45,
            groupe_sanguin="O+",
            type_de_dialyse="Hémodialyse",
        )
        self.machine = Machine.objects.create(
            machine_id="M100",
            model="Model-X",
            manufacturer="DialysisCorp",
            status="Prete",
        )
        self.raspi = RaspiDevice.objects.create(
            raspi_id="RASPI-TEST",
            machine=self.machine,
            is_active=True,
        )
        self.seance = Seance.objects.create(
            patient=self.patient,
            machine=self.machine,
            session_date=date.today(),
            start_hour="08:00",
            duration=4,
            status="planifiée",
            debit=60,
        )

    def _login(self, user):
        session = self.client.session
        session["app_user_id"] = user.id
        session.save()

    def test_mobile_login_success(self):
        res = self.client.post(
            "/api/login/",
            data=json.dumps({"username": "doc_test", "password": "password123"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["user"]["username"], "doc_test")
        self.assertTrue(data.get("sessionid"))

    def test_monitoring_live_via_session_header(self):
        """Flutter Web sends X-Session-Id when Cookie cannot be set."""
        login = self.client.post(
            "/api/login/",
            data=json.dumps({"username": "doc_test", "password": "password123"}),
            content_type="application/json",
        )
        session_id = login.json()["sessionid"]
        # Fresh client with no cookies — only the header, like Flutter Web.
        bare = Client()
        res = bare.get(
            "/api/monitoring/live/",
            HTTP_X_SESSION_ID=session_id,
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        res2 = bare.get(
            "/api/monitoring/?sort=-login_at",
            HTTP_X_SESSION_ID=session_id,
        )
        self.assertEqual(res2.status_code, 200)

    def test_mobile_login_fail(self):
        res = self.client.post(
            "/api/login/",
            data=json.dumps({"username": "doc_test", "password": "wrongpassword"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)
        data = res.json()
        self.assertFalse(data.get("success"))

    def test_mobile_logout(self):
        self._login(self.doctor_user)
        res = self.client.post("/api/logout/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))
        self.assertNotIn("app_user_id", self.client.session)

    def test_password_reset_request_known_email(self):
        """Same neutral message, token persisted, email sent once."""
        with mock.patch("api.views.send_mail") as mock_mail:
            res = self.client.post(
                "/api/password-reset/request/",
                data=json.dumps({"email": "doc@test.com"}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(
            data["message"],
            "Si un compte existe avec cet email, un lien a été envoyé.",
        )
        self.assertEqual(mock_mail.call_count, 1)
        request = PasswordResetRequest.objects.filter(user=self.doctor_user).first()
        self.assertIsNotNone(request)
        self.assertTrue(request.token)
        self.assertFalse(request.is_used())

    def test_password_reset_request_unknown_email(self):
        """Neutral message, no token row created, no email sent."""
        with mock.patch("api.views.send_mail") as mock_mail:
            res = self.client.post(
                "/api/password-reset/request/",
                data=json.dumps({"email": "does-not-exist@test.com"}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(
            data["message"],
            "Si un compte existe avec cet email, un lien a été envoyé.",
        )
        self.assertEqual(mock_mail.call_count, 0)
        self.assertEqual(PasswordResetRequest.objects.count(), 0)

    def test_password_reset_request_requires_json_email(self):
        res = self.client.post(
            "/api/password-reset/request/",
            data=json.dumps({"email": ""}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json().get("success"))
        res2 = self.client.get("/api/password-reset/request/")
        self.assertEqual(res2.status_code, 405)

    def test_profile_photo_upload_cropped_image(self):
        """Same cropped_image base64 contract as accounts.views.profile."""
        User.objects.filter(id=self.doctor_user.id).update(first_login=False)
        self.doctor_user.refresh_from_db()
        self._login(self.doctor_user)
        from accounts.models import Profile
        # 1x1 PNG as a data-URL, like the web CropperJS output.
        png_b64 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE"
            "QVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
        )
        data_url = f"data:image/png;base64,{png_b64}"
        res = self.client.put(
            "/api/profile/",
            data=json.dumps({"cropped_image": data_url}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))
        profile = Profile.objects.get(user=self.doctor_user)
        self.assertTrue(profile.image)
        self.assertTrue(res.json()["data"]["photo_url"])

    def test_profile_photo_upload_invalid_image(self):
        User.objects.filter(id=self.doctor_user.id).update(first_login=False)
        self.doctor_user.refresh_from_db()
        self._login(self.doctor_user)
        res = self.client.put(
            "/api/profile/",
            data=json.dumps({"cropped_image": "not-a-data-url"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json().get("success"))

    def test_profile_payload_has_photo_url(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/profile/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("photo_url", res.json()["data"])

    def test_patients_api_unauthorized(self):
        res = self.client.get("/api/patients/")
        self.assertEqual(res.status_code, 401)

    def test_patients_api(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/patients/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(len(data["data"]), 1)
        self.assertEqual(data["data"][0]["first_name"], "Ahmed")

    def test_patients_api_search(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/patients/?search=Salah")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["data"]), 1)

    def test_patient_detail_api(self):
        self._login(self.doctor_user)
        res = self.client.get(f"/api/patients/{self.patient.id}/")
        self.assertEqual(res.status_code, 200)
        # Patient ids are always strings on the API contract (see _patient_pk).
        self.assertEqual(res.json()["data"]["id"], str(self.patient.id))

    def test_machines_api(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/machines/")
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["raspi"]["raspi_id"], "RASPI-TEST")

    def test_machine_detail_api(self):
        self._login(self.doctor_user)
        res = self.client.get(f"/api/machines/{self.machine.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["data"]["machine_id"], "M100")

    def test_sessions_list_api(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/sessions/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["data"]), 1)

    def test_session_create_api_doctor(self):
        self._login(self.doctor_user)
        payload = {
            "patient_id": self.patient.id,
            "machine_id": self.machine.id,
            "session_date": "2026-09-01",
            "start_time": "10:00",
            "duration": 4,
            "debit": 30,
        }
        res = self.client.post("/api/sessions/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.json().get("success"))

    def test_session_create_api_nurse_forbidden(self):
        """Nurses must not create sessions via the API (same rule as web)."""
        self._login(self.nurse_user)
        payload = {
            "patient_id": self.patient.id,
            "machine_id": self.machine.id,
            "session_date": "2026-09-01",
            "start_time": "10:00",
            "duration": 4,
            "debit": 30,
        }
        res = self.client.post("/api/sessions/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 403)
        self.assertFalse(Seance.objects.filter(session_date="2026-09-01", start_hour="10:00").exists())

    def test_session_create_api_anonymous_forbidden(self):
        payload = {
            "patient_id": self.patient.id,
            "machine_id": self.machine.id,
            "session_date": "2026-09-01",
            "start_time": "10:00",
            "duration": 4,
            "debit": 30,
        }
        res = self.client.post("/api/sessions/", data=json.dumps(payload), content_type="application/json")
        self.assertEqual(res.status_code, 401)

    def test_session_start_end_cancel(self):
        self._login(self.doctor_user)
        # Start session
        res_start = self.client.post(
            f"/api/sessions/{self.seance.id}/start/",
            data=json.dumps({"weight": 70.0, "blood_pressure": "120/80", "heart_rate": 72, "temperature": 36.6, "saturation": 98.0}),
            content_type="application/json",
        )
        self.assertEqual(res_start.status_code, 200)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "en cours")

        # End session
        res_end = self.client.post(
            f"/api/sessions/{self.seance.id}/end/",
            data=json.dumps({"weight": 68.0, "complications": "Aucune"}),
            content_type="application/json",
        )
        self.assertEqual(res_end.status_code, 200)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "terminée")

        # Ending a session must generate exactly one rapport (OneToOne).
        rapport = RapportSeance.objects.filter(seance=self.seance).first()
        self.assertIsNotNone(rapport)
        self.assertIn(rapport.qualite_seance, ("normale", "difficile"))
        self.assertTrue(rapport.contenu_html.strip())
        self.assertTrue(rapport.nom_fichier.endswith(".html"))
        self.assertEqual(RapportSeance.objects.filter(seance=self.seance).count(), 1)

    def test_planned_session_can_be_cancelled(self):
        self._login(self.nurse_user)
        response = self.client.post(f"/api/sessions/{self.seance.id}/cancel/")

        self.assertEqual(response.status_code, 200)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "annulée")

    def test_cancelled_session_cannot_be_started(self):
        self.seance.status = "annulée"
        self.seance.save(update_fields=["status"])
        self._login(self.doctor_user)

        response = self.client.post(f"/api/sessions/{self.seance.id}/start/")

        self.assertEqual(response.status_code, 400)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "annulée")

    def test_started_session_cannot_be_started_again(self):
        self.seance.status = "en cours"
        self.seance.save(update_fields=["status"])
        self._login(self.doctor_user)

        response = self.client.post(f"/api/sessions/{self.seance.id}/start/")

        self.assertEqual(response.status_code, 400)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "en cours")

    def test_ended_session_cannot_be_started_again(self):
        self.seance.status = "terminée"
        self.seance.save(update_fields=["status"])
        self._login(self.doctor_user)

        response = self.client.post(f"/api/sessions/{self.seance.id}/start/")

        self.assertEqual(response.status_code, 400)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "terminée")

    def test_cancelled_session_cannot_be_cancelled_again(self):
        self.seance.status = "annulée"
        self.seance.save(update_fields=["status"])
        self._login(self.doctor_user)

        response = self.client.post(f"/api/sessions/{self.seance.id}/cancel/")

        self.assertEqual(response.status_code, 400)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "annulée")

    def test_session_cancel_requires_session_role(self):
        other_role = Role.objects.create(name="Auditeur")
        other_user = User.objects.create(
            username="auditor_test",
            password="password123",
            role=other_role,
            email="auditor@test.com",
        )
        self._login(other_user)

        response = self.client.post(f"/api/sessions/{self.seance.id}/cancel/")

        self.assertEqual(response.status_code, 403)
        self.seance.refresh_from_db()
        self.assertEqual(self.seance.status, "planifiée")

    def test_rapport_generation_content(self):
        """The rapport must embed real session data (charts, alerts, pre/post)."""
        self._login(self.doctor_user)
        PreSessionMeasurements.objects.create(
            seance=self.seance, weight=80.5, blood_pressure="120/80",
            heart_rate=72, temperature=36.6, saturation=98.0,
        )
        PostSessionMeasurements.objects.create(
            seance=self.seance, weight=78.9, blood_pressure="110/75",
            heart_rate=70, temperature=36.4, saturation=99.0,
        )
        self.seance.status = "en cours"
        self.seance.save()
        LiveMeasurement.objects.create(
            seance=self.seance, Debit_sang=200, PA=110,
            PTM=60, PV=120, Taux_UF=500, Volume_UF=1000, Heparine=800,
        )
        SeanceAlert.objects.create(
            seance=self.seance, alert_type="PA", message="PA élevée",
            danger_level="HIGH", recommended_action="Vérifier",
        )
        from seances.rapport import generate_rapport
        rapport = generate_rapport(self.seance)
        self.assertIn(self.patient.first_name, rapport.contenu_html)
        self.assertIn(self.patient.last_name, rapport.contenu_html)
        self.assertIn("chartPression", rapport.contenu_html)
        # The alert message is embedded inside the json_script block (JSON
        # escaped), and the quality badge renders "difficile" when a HIGH alert
        # or complication exists.
        self.assertIn("PA ", rapport.contenu_html)
        self.assertIn("1.60 kg", rapport.contenu_html)
        self.assertEqual(rapport.qualite_seance, "difficile")
        # Idempotency: calling again updates instead of duplicating.
        generate_rapport(self.seance)
        self.assertEqual(RapportSeance.objects.filter(seance=self.seance).count(), 1)

    def test_session_detail_api(self):
        self._login(self.doctor_user)
        self.machine.location = "Salle 1"
        self.machine.save()
        self.seance.status = "en cours"
        self.seance.save()
        meas = LiveMeasurement.objects.create(
            seance=self.seance,
            Debit_sang=200,
            PA=110,
            PTM=60,
            PV=120,
            Taux_UF=500,
            Volume_UF=1000,
        )
        res = self.client.get(f"/api/sessions/{self.seance.id}/")
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertEqual(data["machine"]["location"], "Salle 1")
        self.assertEqual(data["machine"]["status"], "Prete")
        self.assertEqual(data["readings_count"], 1)
        self.assertEqual(data["last_reading"]["volume_uf"], 1000)
        self.assertEqual(data["last_reading"]["debit_sang"], 200)
        self.assertEqual(data["readings"][0]["pa"], 110)
        self.assertEqual(data["readings"][0]["qb"], 200)
        self.assertGreaterEqual(data["readings"][0]["time"], 0)

    def test_dashboard_api(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/dashboard/")
        self.assertEqual(res.status_code, 200)
        kpis = res.json()["kpis"]
        self.assertIn("active_sessions", kpis)
        self.assertIn("available_machines", kpis)
        self.assertIn("total_machines", kpis)

    def test_monitoring_live_requires_session(self):
        res = self.client.get("/api/monitoring/live/")
        self.assertEqual(res.status_code, 401)
        self._login(self.doctor_user)
        res = self.client.get("/api/monitoring/live/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])

    @override_settings(DEVICE_TOKEN="test-device-token")
    def test_seance_debit_api(self):
        self.seance.status = "en cours"
        self.seance.debit = 30
        self.seance.save()
        res = self.client.get(
            "/api/seance/debit/?machine_id=M100",
            HTTP_X_DEVICE_TOKEN="test-device-token",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["debit"], 30)

    @override_settings(DEVICE_TOKEN="test-device-token")
    def test_push_measurement_pipeline(self):
        self.seance.status = "en cours"
        self.seance.save()
        payload = {
            "machine_id": "M100",
            "Qb": 80,          # Critical low -> trigger alert
            "UF_rate": 500,
            "PA": 190,         # High -> trigger alert
            "PTM": 50,
            "PV": 100,
            "UF_volume": 1000,
            "Heparin": 5,
        }
        res = self.client.post(
            "/api/push/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_DEVICE_TOKEN="test-device-token",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertGreater(data.get("monitoring_alerts_created", 0), 0)
        self.assertGreater(data.get("seance_alerts_created", 0), 0)

    def test_alerts_api_and_ack_resolve(self):
        self._login(self.doctor_user)
        self.seance.status = "en cours"
        self.seance.save()
        meas = LiveMeasurement.objects.create(seance=self.seance, PA=210)
        m_alert = Alerte.objects.create(reading=meas, niveau="RED", message="PA trop élevée", status="NEW")
        s_alert = SeanceAlert.objects.create(
            seance=self.seance,
            alert_type="PA",
            message="PA très élevée",
            danger_level="HIGH",
            recommended_action="Réduire débit",
        )
        res = self.client.get("/api/alerts/")
        self.assertEqual(res.status_code, 200)
        self.assertGreaterEqual(len(res.json()["data"]), 2)

        # Ack monitoring alert
        res_ack = self.client.post(f"/api/alerts/{m_alert.id}/ack/")
        self.assertEqual(res_ack.status_code, 200)
        m_alert.refresh_from_db()
        self.assertEqual(m_alert.status, "ACK")

        # Resolve monitoring alert
        res_res = self.client.post(f"/api/alerts/{m_alert.id}/resolve/")
        self.assertEqual(res_res.status_code, 200)
        m_alert.refresh_from_db()
        self.assertEqual(m_alert.status, "RESOLVED")

        # Seance alerts start NEW and are returned with a real status
        res_list = self.client.get("/api/alerts/")
        s_alert.refresh_from_db()
        self.assertEqual(s_alert.status, "NEW")
        item = next(
            (a for a in res_list.json()["data"] if a["id"] == s_alert.id), None
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["status"], "NEW")

        # Ack seance alert — must persist
        res_s_ack = self.client.post(f"/api/alerts/{s_alert.id}/ack/")
        self.assertEqual(res_s_ack.status_code, 200)
        s_alert.refresh_from_db()
        self.assertEqual(s_alert.status, "ACK")
        res_list = self.client.get("/api/alerts/")
        item = next(
            (a for a in res_list.json()["data"] if a["id"] == s_alert.id), None
        )
        self.assertEqual(item["status"], "ACK")

        # Resolve seance alert — must persist
        res_s_res = self.client.post(f"/api/alerts/{s_alert.id}/resolve/")
        self.assertEqual(res_s_res.status_code, 200)
        s_alert.refresh_from_db()
        self.assertEqual(s_alert.status, "RESOLVED")
        res_list = self.client.get("/api/alerts/")
        item = next(
            (a for a in res_list.json()["data"] if a["id"] == s_alert.id), None
        )
        self.assertEqual(item["status"], "RESOLVED")

        # Status filter applies to both sources
        res_new = self.client.get("/api/alerts/?status=NEW")
        ids_new = {a["id"] for a in res_new.json()["data"]}
        self.assertNotIn(s_alert.id, ids_new)
        res_resolved = self.client.get("/api/alerts/?status=RESOLVED")
        ids_resolved = {a["id"] for a in res_resolved.json()["data"]}
        self.assertIn(s_alert.id, ids_resolved)

    def test_dashboard_kpis_are_real_values(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/dashboard/")
        self.assertEqual(res.status_code, 200)
        kpis = res.json()["kpis"]
        # Machine KPIs come from the DB, not hardcoded zeros.
        self.assertEqual(kpis["total_machines"], Machine.objects.count())
        self.assertEqual(
            kpis["available_machines"],
            Machine.objects.filter(status="Prete").count(),
        )
        self.assertEqual(kpis["patients_count"], Patient.objects.count())
        self.assertEqual(
            kpis["active_alerts"],
            Alerte.objects.filter(status="NEW").count()
            + SeanceAlert.objects.filter(status="NEW").count(),
        )

    def test_nurses_kpis_are_real_values(self):
        self._login(self.doctor_user)
        self.nurse_user.etat = True
        self.nurse_user.save()
        res = self.client.get("/api/nurses/")
        self.assertEqual(res.status_code, 200)
        kpis = res.json()["kpis"]
        self.assertEqual(kpis["kpi_total_patients"], Patient.objects.count())
        self.assertEqual(
            kpis["kpi_active_sessions"],
            Seance.objects.filter(status="en cours").count(),
        )
        self.assertEqual(
            kpis["kpi_scheduled_sessions"],
            Seance.objects.filter(status="planifiée").count(),
        )
        self.assertEqual(kpis["kpi_active_nurses"], 1)

    def test_doctors_do_not_fabricate_rating(self):
        admin_role = Role.objects.create(name="Admin")
        admin_user = User.objects.create(
            username="admin_kpi",
            password="password123",
            role=admin_role,
            email="admin_kpi@test.com",
        )
        self._login(admin_user)
        res = self.client.get("/api/doctors/")
        self.assertEqual(res.status_code, 200)
        for doctor in res.json()["data"]:
            # No rating data exists in the schema — must not be a fake 4.8.
            self.assertEqual(doctor["rating"], 0)


@override_settings(DEVICE_TOKEN="test-device-token")
class EndpointSecurityTests(TestCase):
    """Sécurité des endpoints relevés par l'audit :
    /api/push/ et /api/seance/debit/ (jeton machine) et
    /api/real-monitoring/ (session utilisateur).
    Le heartbeat est couvert dans machines/tests.py."""

    TOKEN = "test-device-token"

    def setUp(self):
        self.client = Client()
        self.doctor_role = Role.objects.create(name="Docteur")
        self.doctor_user = User.objects.create(
            username="sec_doc",
            password="password123",
            role=self.doctor_role,
            email="sec_doc@test.com",
        )
        patient = Patient.objects.create(
            first_name="Sami",
            last_name="Trabelsi",
            date_of_birth=date(1975, 1, 1),
            age=50,
            groupe_sanguin="A+",
            type_de_dialyse="Hémodialyse",
        )
        machine = Machine.objects.create(
            machine_id="MSEC",
            model="Model-X",
            manufacturer="DialysisCorp",
            status="Prete",
        )
        self.seance = Seance.objects.create(
            patient=patient,
            machine=machine,
            session_date=date.today(),
            start_hour="09:00",
            duration=4,
            status="en cours",
            debit=30,
        )

    def _login(self, user):
        session = self.client.session
        session["app_user_id"] = user.id
        session.save()

    def _push(self, token=None):
        payload = {
            "machine_id": "MSEC",
            "Qb": 280,
            "UF_rate": 500,
            "PA": 120,
            "PTM": 60,
            "PV": 120,
            "UF_volume": 800,
            "Heparin": 10,
        }
        extra = {"HTTP_X_DEVICE_TOKEN": token} if token is not None else {}
        return self.client.post(
            "/api/push/",
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )

    # ---------- /api/push/ ----------

    def test_push_rejected_without_token(self):
        res = self._push()
        self.assertEqual(res.status_code, 401)
        self.assertEqual(LiveMeasurement.objects.count(), 0)

    def test_push_rejected_with_invalid_token(self):
        res = self._push(token="wrong-token")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(LiveMeasurement.objects.count(), 0)

    def test_push_accepted_with_valid_token(self):
        res = self._push(token=self.TOKEN)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
        self.assertEqual(LiveMeasurement.objects.filter(seance=self.seance).count(), 1)

    def test_push_rejected_when_server_token_not_configured(self):
        # Fail-closed : pas de DEVICE_TOKEN côté serveur → tout est refusé.
        with override_settings(DEVICE_TOKEN=""):
            res = self._push(token=self.TOKEN)
        self.assertEqual(res.status_code, 401)

    # ---------- /api/seance/debit/ ----------

    def test_debit_rejected_without_token(self):
        res = self.client.get("/api/seance/debit/?machine_id=MSEC")
        self.assertEqual(res.status_code, 401)

    def test_debit_rejected_with_invalid_token(self):
        res = self.client.get(
            "/api/seance/debit/?machine_id=MSEC",
            HTTP_X_DEVICE_TOKEN="wrong-token",
        )
        self.assertEqual(res.status_code, 401)

    def test_debit_accepted_with_valid_token(self):
        res = self.client.get(
            "/api/seance/debit/?machine_id=MSEC",
            HTTP_X_DEVICE_TOKEN=self.TOKEN,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["debit"], 30)

    # ---------- /api/real-monitoring/ ----------

    def test_real_monitoring_rejected_for_anonymous(self):
        res = self.client.get("/api/real-monitoring/")
        self.assertEqual(res.status_code, 401)

    def test_real_monitoring_rejected_with_invalid_session(self):
        # X-Session-Id inexpiré/invalide : la session n'est jamais liée.
        res = self.client.get(
            "/api/real-monitoring/",
            HTTP_X_SESSION_ID="expired-session-key",
        )
        self.assertEqual(res.status_code, 401)

    def test_real_monitoring_accepted_with_valid_session(self):
        self._login(self.doctor_user)
        res = self.client.get("/api/real-monitoring/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["success"])
