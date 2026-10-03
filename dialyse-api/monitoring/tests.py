from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from machines.models import Machine
from monitoring.models import LiveMeasurement
from monitoring.alerte import analyser_mesure
from monitoring.services import check_thresholds, persist_measurement_alerts
from patients.models import Patient
from seances.models import Seance


class RunDemoSimulationTests(TestCase):
    def setUp(self):
        self.patient = Patient.objects.create(
            first_name="Alice",
            last_name="Durand",
            date_of_birth="1990-01-01",
            age=35,
            groupe_sanguin="A+",
            type_de_dialyse="Hémodialyse",
        )

    def _machine(self, machine_id):
        return Machine.objects.create(machine_id=machine_id)

    def _session(self, patient, machine, status="en cours"):
        return Seance.objects.create(
            patient=patient,
            machine=machine,
            status=status,
        )

    @override_settings(DEMO_MODE=False)
    def test_demo_mode_disabled_creates_no_measurements(self):
        machine = self._machine("M-100")
        self._session(self.patient, machine, status="en cours")

        out, err = StringIO(), StringIO()
        call_command("run_demo_simulation", "--cycles", "1", stdout=out, stderr=err)

        self.assertEqual(LiveMeasurement.objects.count(), 0)

        self.assertIn("DEMO_MODE is disabled.", err.getvalue())

    @override_settings(DEMO_MODE=True)
    def test_single_active_session_gets_one_measurement(self):
        machine = self._machine("M-100")
        seance = self._session(self.patient, machine, status="en cours")

        call_command("run_demo_simulation", "--cycles", "1", stdout=StringIO())

        measurements = LiveMeasurement.objects.filter(seance=seance)
        self.assertEqual(measurements.count(), 1)
        lm = measurements.first()
        self.assertIsNotNone(lm.Debit_sang)
        self.assertIsNotNone(lm.PA)
        self.assertIsNotNone(lm.PTM)
        self.assertIsNotNone(lm.PV)
        self.assertIsNotNone(lm.Volume_UF)
        self.assertIsNotNone(lm.Taux_UF)
        self.assertIsNotNone(lm.Heparine)

    @override_settings(DEMO_MODE=True)
    def test_multiple_active_sessions_each_get_their_own_measurement(self):
        m1 = self._machine("M-100")
        m2 = self._machine("M-101")
        m3 = self._machine("M-102")
        s1 = self._session(self.patient, m1, status="en cours")
        s2 = self._session(self.patient, m2, status="en cours")
        s3 = self._session(self.patient, m3, status="en cours")

        call_command("run_demo_simulation", "--cycles", "1", stdout=StringIO())

        self.assertEqual(LiveMeasurement.objects.count(), 3)
        self.assertEqual(LiveMeasurement.objects.filter(seance=s1).count(), 1)
        self.assertEqual(LiveMeasurement.objects.filter(seance=s2).count(), 1)
        self.assertEqual(LiveMeasurement.objects.filter(seance=s3).count(), 1)

    @override_settings(DEMO_MODE=True)
    def test_no_active_sessions_creates_no_measurements(self):
        machine = self._machine("M-100")
        self._session(self.patient, machine, status="planifiée")

        call_command("run_demo_simulation", "--cycles", "1", stdout=StringIO())

        self.assertEqual(LiveMeasurement.objects.count(), 0)


class AlertThresholdTests(TestCase):
    def setUp(self):
        self.patient = Patient.objects.create(
            first_name="Alice",
            last_name="Durand",
            date_of_birth="1990-01-01",
            age=35,
            groupe_sanguin="A+",
            type_de_dialyse="Hémodialyse",
        )
        self.machine = Machine.objects.create(machine_id="M-200")
        self.seance = Seance.objects.create(
            patient=self.patient,
            machine=self.machine,
            status="en cours",
        )

    def test_normal_measurement_creates_no_alert(self):
        measurement = LiveMeasurement.objects.create(
            seance=self.seance,
            Debit_sang=250,
            PA=120,
            PV=120,
            PTM=100,
            Taux_UF=500,
            Volume_UF=1000,
            Heparine=800,
        )

        self.assertEqual(analyser_mesure(measurement), [])
        self.assertEqual(persist_measurement_alerts(self.seance, measurement), (0, 0))

    def test_configured_session_thresholds_drive_both_alert_models(self):
        self.seance.arterial_pressure_min = 120
        self.seance.arterial_pressure_max = 140
        self.seance.arterial_pressure_critical_low = 100
        self.seance.arterial_pressure_critical_high = 160
        self.seance.save()
        measurement = LiveMeasurement.objects.create(seance=self.seance, PA=110)

        alerts = analyser_mesure(measurement)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["danger_level"], "MEDIUM")
        self.assertIn("normal min : 120", alerts[0]["message"])
        self.assertEqual(check_thresholds(measurement)[0][0], "YELLOW")

        self.assertEqual(persist_measurement_alerts(self.seance, measurement), (1, 1))
        self.assertEqual(measurement.alertes.count(), 1)
        self.assertEqual(self.seance.alerts.count(), 1)

    def test_different_sessions_use_their_own_thresholds(self):
        other = Seance.objects.create(
            patient=self.patient,
            machine=Machine.objects.create(machine_id="M-201"),
            status="en cours",
            arterial_pressure_min=130,
            arterial_pressure_max=150,
            arterial_pressure_critical_low=110,
            arterial_pressure_critical_high=170,
        )
        first = LiveMeasurement.objects.create(seance=self.seance, PA=60)
        second = LiveMeasurement.objects.create(seance=other, PA=100)

        self.assertEqual(analyser_mesure(first)[0]["danger_level"], "HIGH")
        self.assertEqual(analyser_mesure(second)[0]["danger_level"], "HIGH")
        self.assertIn("seuil critique : 70", analyser_mesure(first)[0]["message"])
        self.assertIn("seuil critique : 110", analyser_mesure(second)[0]["message"])

    def test_default_threshold_is_used_without_session(self):
        class Measurement:
            PA = 190

        alerts = analyser_mesure(Measurement())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["danger_level"], "MEDIUM")