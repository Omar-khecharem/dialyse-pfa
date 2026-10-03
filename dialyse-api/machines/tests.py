import json

from django.test import Client, TestCase, override_settings

from .models import Machine, RaspiDevice


@override_settings(DEVICE_TOKEN="hb-test-token")
class RaspiHeartbeatSecurityTests(TestCase):
    """POST /machines/raspi/heartbeat/ — jeton machine obligatoire,
    appareil enregistré et actif uniquement."""

    TOKEN = "hb-test-token"

    def setUp(self):
        self.client = Client()
        self.machine = Machine.objects.create(
            machine_id="MHB",
            model="Model-X",
            manufacturer="DialysisCorp",
            status="Prete",
        )
        self.device = RaspiDevice.objects.create(
            raspi_id="RASPI-SEC",
            machine=self.machine,
            is_active=True,
        )
        self.inactive_device = RaspiDevice.objects.create(
            raspi_id="RASPI-OFF",
            is_active=False,
        )

    def _heartbeat(self, raspi_id="RASPI-SEC", token=None):
        extra = {"HTTP_X_DEVICE_TOKEN": token} if token is not None else {}
        return self.client.post(
            "/machines/raspi/heartbeat/",
            data=json.dumps({"raspi_id": raspi_id}),
            content_type="application/json",
            **extra,
        )

    def test_heartbeat_rejected_without_token(self):
        res = self._heartbeat()
        self.assertEqual(res.status_code, 401)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.last_seen)

    def test_heartbeat_rejected_with_invalid_token(self):
        res = self._heartbeat(token="wrong-token")
        self.assertEqual(res.status_code, 401)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.last_seen)

    def test_heartbeat_accepted_with_valid_token(self):
        res = self._heartbeat(token=self.TOKEN)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["machine_id"], "MHB")
        self.assertTrue(data["is_active"])
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)

    def test_heartbeat_unknown_device_returns_404(self):
        res = self._heartbeat(raspi_id="RASPI-UNKNOWN", token=self.TOKEN)
        self.assertEqual(res.status_code, 404)

    def test_heartbeat_deactivated_device_rejected(self):
        res = self._heartbeat(raspi_id="RASPI-OFF", token=self.TOKEN)
        self.assertEqual(res.status_code, 403)
        self.inactive_device.refresh_from_db()
        self.assertIsNone(self.inactive_device.last_seen)

    def test_heartbeat_rejected_when_server_token_not_configured(self):
        with override_settings(DEVICE_TOKEN=""):
            res = self._heartbeat(token=self.TOKEN)
        self.assertEqual(res.status_code, 401)
