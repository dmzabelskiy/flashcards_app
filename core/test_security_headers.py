from django.test import TestCase
from django.urls import reverse


class ReferrerPolicyTests(TestCase):
    def test_pages_send_origin_to_cross_origin_embeds(self):
        # The YouTube player iframe fails with error 153 when the browser
        # sends no Referer at all, which Django's default "same-origin"
        # policy causes for every cross-origin request.
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
