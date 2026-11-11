from django.urls import reverse

from core.testutils import AuthedTestCase
from decks.models import Deck


class DrillsIndexTests(AuthedTestCase):
    def _set_mode(self, mode):
        session = self.client.session
        session["language_mode"] = mode
        session.save()

    def test_lists_every_drill_in_russian_mode(self):
        self._set_mode(Deck.LanguageMode.DE_RU)
        response = self.client.get(reverse("reviews:drills"))
        self.assertEqual(response.status_code, 200)
        for label in [
            "Articles",
            "Adjective + preposition",
            "Verb + preposition",
            "Irregular verbs",
        ]:
            self.assertContains(response, label)

    def test_spanish_mode_hides_irregular_verbs(self):
        self._set_mode(Deck.LanguageMode.DE_ES)
        response = self.client.get(reverse("reviews:drills"))
        self.assertContains(response, "Verb + preposition")
        self.assertNotContains(response, "Irregular verbs")

    def test_sidebar_drills_link_points_to_the_hub(self):
        # A link straight to the article drill would dead-end users whose
        # cards have no articles.
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, 'href="{}"'.format(reverse("reviews:drills")))
