from django.test import SimpleTestCase
from django.urls import reverse

from core.language_modes import get_language_mode_options, get_source_language, get_target_language
from core.language_packs import DUTCH, GERMAN, PACKS, pack_for_mode, pack_for_source
from core.testutils import AuthedTestCase
from decks.models import Deck
from video_study.services import MEDIA_CHANNELS


class LanguagePackTests(SimpleTestCase):
    def test_modes_map_to_packs(self):
        self.assertIs(pack_for_mode("DE-RU"), GERMAN)
        self.assertIs(pack_for_mode("DE-ES"), GERMAN)
        self.assertIs(pack_for_mode("NL-EN"), DUTCH)

    def test_unknown_mode_falls_back_to_german(self):
        self.assertIs(pack_for_mode("XX-YY"), GERMAN)
        self.assertIs(pack_for_mode(None), GERMAN)

    def test_source_and_target_of_nl_en(self):
        self.assertEqual(get_source_language("NL-EN"), "nl")
        self.assertEqual(get_target_language("NL-EN"), "en")
        self.assertEqual(get_source_language("DE-RU"), "de")

    def test_dutch_pack_contents(self):
        self.assertEqual(DUTCH.articles, ("de", "het"))
        self.assertEqual(DUTCH.features, frozenset({"article_drill", "video"}))
        self.assertEqual(DUTCH.video_channel_key, "nl-channel")
        self.assertIs(pack_for_source("nl"), DUTCH)

    def test_german_pack_has_every_feature(self):
        self.assertEqual(
            GERMAN.features,
            frozenset(
                {
                    "podcast",
                    "article_drill",
                    "video",
                }
            ),
        )

    def test_known_answer_german_pack(self):
        self.assertEqual(GERMAN.code, "de")
        self.assertEqual(GERMAN.name, "German")
        self.assertEqual(GERMAN.articles, ("der", "die", "das"))
        self.assertEqual(GERMAN.article_genders, ("Masculine", "Feminine", "Neuter"))
        self.assertEqual(GERMAN.video_channel_key, "kurzgesagt-de")
        self.assertEqual(GERMAN.speech_locale, "de-DE")
        self.assertEqual(
            GERMAN.features,
            frozenset(
                {
                    "podcast",
                    "article_drill",
                    "video",
                }
            ),
        )

    def test_known_answer_dutch_pack(self):
        self.assertEqual(DUTCH.code, "nl")
        self.assertEqual(DUTCH.name, "Dutch")
        self.assertEqual(DUTCH.articles, ("de", "het"))
        self.assertEqual(DUTCH.article_genders, ("Common", "Neuter"))
        self.assertEqual(DUTCH.video_channel_key, "nl-channel")
        self.assertEqual(DUTCH.speech_locale, "nl-NL")
        self.assertEqual(DUTCH.features, frozenset({"article_drill", "video"}))

    def test_the_german_and_dutch_packs_are_loaded(self):
        # A new language folder may add to PACKS; these two must stay.
        self.assertLessEqual({"de", "nl"}, set(PACKS))
        self.assertIs(PACKS["de"], GERMAN)
        self.assertIs(PACKS["nl"], DUTCH)

    def test_every_pack_video_channel_exists(self):
        for pack in PACKS.values():
            if pack.video_channel_key:
                self.assertIn(pack.video_channel_key, MEDIA_CHANNELS, pack.code)

    def test_option_descriptions_use_the_source_name(self):
        descriptions = {o["value"]: o["description"] for o in get_language_mode_options("NL-EN")}
        self.assertEqual(descriptions["NL-EN"], "Dutch → English")
        self.assertEqual(descriptions["DE-RU"], "German → Russian")


class LanguageSelectorTests(AuthedTestCase):
    def test_nl_en_can_be_selected_and_renders(self):
        response = self.client.post(reverse("core:set_language_mode"), {"language_mode": "NL-EN"})
        self.assertRedirects(response, reverse("core:dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["language_mode"], Deck.LanguageMode.NL_EN)
        page = self.client.get(reverse("cards:list"))
        self.assertContains(page, "NL → EN")
        self.assertContains(page, "Dutch with English")
        self.assertContains(page, "img/flags/en.png")
