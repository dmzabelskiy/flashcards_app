from django.test import TestCase
from django.urls import reverse

from core.testutils import create_user


class AuthGateTests(TestCase):
    def test_anonymous_request_to_app_redirects_to_login(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_login_page_is_reachable_anonymously(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_authenticated_user_can_load_dashboard(self):
        user = create_user()
        self.client.force_login(user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
