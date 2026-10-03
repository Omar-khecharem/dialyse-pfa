from django.test import TestCase
from django.test import Client
from django.contrib.auth.hashers import check_password
from accounts.models import User, Role, PasswordResetRequest
from accounts.reset_utils import make_reset_token


class AccountCreationAuthorizationTests(TestCase):
    """Regression tests for B1: add_doctor / ajout_infirmier must require auth + role."""

    def setUp(self):
        self.client = Client()
        self.admin_role = Role.objects.create(name="Admin")
        self.doctor_role = Role.objects.create(name="Docteur")
        self.nurse_role = Role.objects.create(name="Infirmier")

        self.admin_user = User.objects.create(
            username="admin_test",
            password="password123",
            role=self.admin_role,
            email="admin_test@test.com",
            first_login=False,
        )
        self.doctor_user = User.objects.create(
            username="doc_test",
            password="password123",
            role=self.doctor_role,
            email="doc_test@test.com",
            first_login=False,
        )
        self.nurse_user = User.objects.create(
            username="nurse_test",
            password="password123",
            role=self.nurse_role,
            email="nurse_test@test.com",
            first_login=False,
        )

    def _login(self, user):
        session = self.client.session
        session["app_user_id"] = user.id
        session.save()

    def test_anonymous_cannot_add_nurse(self):
        res = self.client.post(
            "/nurses/ajouter/",
            {"nom": "anon_nurse", "email": "anon_nurse@test.com", "telephone": "000"},
        )
        self.assertIn(res.status_code, (301, 302))
        # must NOT be a redirect to nurses list (success path)
        self.assertNotEqual(res.url, "/nurses/")
        self.assertFalse(User.objects.filter(username="anon_nurse").exists())

    def test_anonymous_cannot_add_doctor(self):
        res = self.client.post(
            "/add-doctor/",
            {"fullName": "anon_doctor", "email": "anon_doctor@test.com", "speciality": "Test", "phone": "000"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertNotEqual(res.url, "/docteurs/")
        self.assertFalse(User.objects.filter(username="anon_doctor").exists())

    def test_nurse_cannot_add_doctor(self):
        self._login(self.nurse_user)
        res = self.client.post(
            "/add-doctor/",
            {"fullName": "nurse_doc", "email": "nurse_doc@test.com", "speciality": "Test", "phone": "000"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertEqual(res.url, "/error/")
        self.assertFalse(User.objects.filter(username="nurse_doc").exists())

    def test_doctor_cannot_add_doctor(self):
        self._login(self.doctor_user)
        res = self.client.post(
            "/add-doctor/",
            {"fullName": "doc_doc", "email": "doc_doc@test.com", "speciality": "Test", "phone": "000"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertEqual(res.url, "/error/")
        self.assertFalse(User.objects.filter(username="doc_doc").exists())

    def test_admin_can_add_doctor(self):
        self._login(self.admin_user)
        res = self.client.post(
            "/add-doctor/",
            {"fullName": "ok_doc", "email": "ok_doc@test.com", "speciality": "Cardio", "phone": "111"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertEqual(res.url, "/docteurs/")
        self.assertTrue(User.objects.filter(username="ok_doc").exists())

    def test_nurse_cannot_add_nurse(self):
        self._login(self.nurse_user)
        res = self.client.post(
            "/nurses/ajouter/",
            {"nom": "nurse_nurse", "email": "nurse_nurse@test.com", "telephone": "000"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertEqual(res.url, "/error/")
        self.assertFalse(User.objects.filter(username="nurse_nurse").exists())

    def test_doctor_can_add_nurse(self):
        self._login(self.doctor_user)
        res = self.client.post(
            "/nurses/ajouter/",
            {"nom": "ok_nurse", "email": "ok_nurse@test.com", "telephone": "111"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertEqual(res.url, "/nurses/")
        self.assertTrue(User.objects.filter(username="ok_nurse").exists())

    def test_admin_can_add_nurse(self):
        self._login(self.admin_user)
        res = self.client.post(
            "/nurses/ajouter/",
            {"nom": "ok_nurse2", "email": "ok_nurse2@test.com", "telephone": "111"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.assertEqual(res.url, "/nurses/")
        self.assertTrue(User.objects.filter(username="ok_nurse2").exists())


class DashboardAuthorizationTests(TestCase):
    """Regression tests for B3: /dashboard/ must require authentication."""

    def setUp(self):
        self.client = Client()
        self.admin_role = Role.objects.create(name="Admin")
        self.admin_user = User.objects.create(
            username="admin_dash",
            password="password123",
            role=self.admin_role,
            email="admin_dash@test.com",
            first_login=False,
        )

    def test_anonymous_dashboard_redirects_to_login(self):
        res = self.client.get("/dashboard/")
        self.assertIn(res.status_code, (301, 302))
        self.assertIn("/", res.url)  # login is at root ''

    def test_authenticated_dashboard_ok(self):
        session = self.client.session
        session["app_user_id"] = self.admin_user.id
        session.save()
        res = self.client.get("/dashboard/")
        self.assertEqual(res.status_code, 200)


class PasswordResetTests(TestCase):
    """Regression tests for B2: password reset must never 500, tokens one-time."""

    def setUp(self):
        self.client = Client()
        self.nurse_role = Role.objects.create(name="Infirmier")
        self.user = User.objects.create(
            username="reset_user",
            password="OldPass123!",
            role=self.nurse_role,
            email="reset_user@test.com",
            first_login=False,
        )

    def _reset_page(self, token):
        return self.client.get(f"/password-reset/{token}/")

    def test_valid_token_shows_form(self):
        token = make_reset_token(self.user.id)
        PasswordResetRequest.objects.create(user=self.user, token=token)
        res = self._reset_page(token)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "password1")

    def test_valid_reset_changes_password(self):
        token = make_reset_token(self.user.id)
        PasswordResetRequest.objects.create(user=self.user, token=token)
        res = self.client.post(
            f"/password-reset/{token}/",
            {"password1": "NewPass456!", "password2": "NewPass456!"},
        )
        self.assertIn(res.status_code, (301, 302))
        self.user.refresh_from_db()
        self.assertTrue(check_password("NewPass456!", self.user.password))

    def test_invalid_token_returns_page_not_500(self):
        res = self._reset_page("totally-invalid-token-xyz")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Lien invalide ou expiré")

    def test_reused_token_returns_invalid_page_not_500(self):
        token = make_reset_token(self.user.id)
        prr = PasswordResetRequest.objects.create(user=self.user, token=token)
        # mark as used, like a previous reset
        from django.utils import timezone
        prr.used_at = timezone.now()
        prr.save()
        res = self._reset_page(token)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Lien invalide ou expiré")

    def test_expired_token_returns_invalid_page_not_500(self):
        # Craft a token whose embedded timestamp is older than the 30-min TTL.
        import time as _time
        from django.core.signing import TimestampSigner, b64_encode
        signer = TimestampSigner(salt="reset-password")
        value = b64_encode(str(self.user.id).encode()).decode()
        old_ts = b64_encode(str(int(_time.time()) - 7200).encode()).decode()
        raw = f"{value}:{old_ts}"
        expired_token = f"{raw}:{signer.signature(raw)}"
        PasswordResetRequest.objects.create(user=self.user, token=expired_token)
        # sanity: read_reset_token must treat it as expired
        from accounts.reset_utils import read_reset_token
        self.assertIsNone(read_reset_token(expired_token, max_age_seconds=1800))
        res = self._reset_page(expired_token)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Lien invalide ou expiré")