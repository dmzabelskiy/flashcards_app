from unittest.mock import patch

from django.urls import reverse

from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.daily_plan import available_drills, drill_of_the_day
from reviews.models import TrainerAttempt

Trainer = TrainerAttempt.Trainer


MEDIA_STUB = {
    "is_done": False,
    "video": None,
    "podcast": None,
    "video_url": None,
    "podcast_url": None,
}


# Patch the whole media row so this test does not depend on how media is chosen.
@patch("reviews.daily_plan._media_item", return_value=MEDIA_STUB)
class NlModeGatingTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.NL_EN
        session.save()

    def test_only_the_article_drill_is_offered(self, *_):
        self.assertEqual([d["trainer"] for d in available_drills("NL-EN")], [Trainer.ARTICLE])
        self.assertEqual(drill_of_the_day("NL-EN"), Trainer.ARTICLE)

    def test_article_description_names_dutch_articles(self, *_):
        self.assertIn("de / het", available_drills("NL-EN")[0]["description"])
        self.assertIn("der / die / das", available_drills("DE-RU")[0]["description"])

    def test_drills_index_hides_german_only_entries(self, *_):
        response = self.client.get(reverse("reviews:drills"))
        self.assertContains(response, "Articles")
        for hidden in (
            "Verb + preposition",
            "Adjective + preposition",
            "Irregular verbs",
        ):
            self.assertNotContains(response, hidden)

    def test_dashboard_has_three_items(self, *_):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.context["daily_plan"]["total"], 3)
