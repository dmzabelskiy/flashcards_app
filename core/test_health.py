from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class HealthzTests(TestCase):
    def test_anonymous_get_returns_ok(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_post_is_not_allowed(self):
        response = self.client.post(reverse("healthz"))
        self.assertEqual(response.status_code, 405)

    @patch("core.views.connection.cursor")
    def test_database_error_returns_503(self, cursor):
        cursor.side_effect = Exception("database is unavailable")
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error"})
