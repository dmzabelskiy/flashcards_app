import datetime
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.activity import build_activity_calendar
from core.testutils import AuthedTestCase
from core.views import _today_range
from decks.models import Deck
from reviews.models import ReviewLog
from reviews.ratings import Rating


class TodayRangeTests(TestCase):
    def test_today_range_starts_at_local_midnight(self):
        start, _now = _today_range()
        local_start = timezone.localtime(start)

        self.assertEqual(
            (local_start.hour, local_start.minute, local_start.second, local_start.microsecond),
            (0, 0, 0, 0),
        )


class LanguageModeTests(AuthedTestCase):
    def test_dashboard_selector_switches_session_to_spanish(self):
        response = self.client.post(
            reverse("core:set_language_mode"),
            {"language_mode": Deck.LanguageMode.DE_ES},
        )

        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertEqual(self.client.session["language_mode"], Deck.LanguageMode.DE_ES)

    def test_mode_selector_rejects_get_and_unknown_modes(self):
        url = reverse("core:set_language_mode")

        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"language_mode": "DE-FR"})

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("language_mode", self.client.session)

    def test_context_lists_every_supported_mode(self):
        response = self.client.get(reverse("core:dashboard"))

        self.assertEqual(
            [option["value"] for option in response.context["language_mode_options"]],
            list(Deck.LanguageMode.values),
        )

    def test_dashboard_renders_explicit_russian_and_spanish_options(self):
        response = self.client.get(reverse("core:dashboard"))

        # The "language-mode-switcher" class must not appear anywhere in the
        # rendered page.
        self.assertNotContains(response, "language-mode-switcher")
        self.assertContains(response, "German with Russian")
        self.assertContains(response, "German → Russian")
        self.assertContains(response, "German → Spanish")
        self.assertContains(response, 'value="DE-RU"')
        self.assertContains(response, 'value="DE-ES"')

    def test_spanish_dashboard_hides_russian_translation_drill(self):
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.get(reverse("core:dashboard"))

        self.assertContains(response, "German with Spanish")
        self.assertNotContains(response, "Irregular verbs")

    def test_dashboard_provides_the_daily_plan_context(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("daily_plan", response.context)
        self.assertEqual(response.context["daily_plan"]["total"], 3)
        self.assertIn("analytics", response.context)

    def test_dashboard_starts_the_kurzgesagt_prefetch(self):
        with patch("core.views.start_kurzgesagt_prefetch") as start_prefetch:
            self.client.get(reverse("core:dashboard"))

        start_prefetch.assert_called_once_with()

    def test_base_layout_has_sidebar_and_slim_topbar(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "site-sidebar")
        self.assertContains(response, 'href="{}"'.format(reverse("cards:list")))

    def test_dashboard_hub_renders_all_three_items(self):
        from django.urls import reverse

        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "No words to study yet")
        self.assertContains(response, "drill")
        self.assertContains(response, response.context["daily_plan"]["drill"]["label"])
        self.assertContains(response, "Watch or listen")
        # The dashboard has no Decks table.
        self.assertNotContains(response, "New deck")

    def test_media_is_not_duplicated_in_the_rail(self):
        # The video/podcast lives in the daily-plan checklist row, not in a
        # duplicate "Watch or listen" widget in the rail.
        response = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(response, "Watch in learning mode")

    def test_activity_widget_labels_weekdays(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertContains(response, "activity-week")
        self.assertContains(response, "activity-dow")


class ActivityCalendarTests(TestCase):
    def test_current_and_longest_streak(self):
        today = datetime.date(2026, 7, 5)
        day_counts = {
            datetime.date(2026, 7, 5): 3,
            datetime.date(2026, 7, 4): 1,
            datetime.date(2026, 7, 3): 2,
            datetime.date(2026, 6, 20): 5,  # isolated earlier day
        }
        result = build_activity_calendar(day_counts, today, window_days=30)
        self.assertEqual(result["current_streak"], 3)
        self.assertEqual(result["longest_streak"], 3)

    def test_current_streak_zero_when_today_has_no_reviews(self):
        today = datetime.date(2026, 7, 5)
        day_counts = {datetime.date(2026, 7, 4): 1}
        result = build_activity_calendar(day_counts, today, window_days=30)
        self.assertEqual(result["current_streak"], 0)

    def test_weeks_cover_the_window_and_bucket_levels(self):
        today = datetime.date(2026, 7, 5)
        result = build_activity_calendar({today: 12}, today, window_days=7)
        flat = [cell for week in result["weeks"] for cell in week if cell["date"] is not None]
        self.assertEqual(len(flat), 7)
        today_cell = next(cell for cell in flat if cell["date"] == today)
        self.assertEqual(today_cell["level"], 4)


class StatsHeatmapTests(AuthedTestCase):
    def test_stats_context_has_calendar_with_streaks(self):
        deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        card = Card.objects.create(deck=deck, front="a", back="b")
        now = timezone.now()
        ReviewLog.objects.create(
            card=card,
            rating=Rating.GOOD,
            reviewed_at=now,
            previous_due_at=now,
            new_due_at=now,
            previous_interval_days=0,
            new_interval_days=1,
        )

        response = self.client.get(reverse("core:stats"))

        self.assertIn("activity", response.context)
        self.assertEqual(response.context["activity"]["current_streak"], 1)


class DbPathEnvTests(SimpleTestCase):
    def test_db_path_honors_env_override_and_default(self):
        # Reload a fresh copy of the settings module rather than trusting the
        # live django.conf.settings object: Django's test runner mutates
        # DATABASES["default"]["NAME"] to ":memory:" once it creates the
        # sqlite test database, which makes reading the live settings
        # order-dependent on which tests ran first.
        import os
        from importlib import reload

        from config import settings as config_settings

        had_env = "DJANGO_DB_PATH" in os.environ
        old_value = os.environ.get("DJANGO_DB_PATH")
        try:
            os.environ.pop("DJANGO_DB_PATH", None)
            reload(config_settings)
            self.assertTrue(
                str(config_settings.DATABASES["default"]["NAME"]).endswith("db.sqlite3")
            )

            custom_path = "/some/custom/path.sqlite3"
            os.environ["DJANGO_DB_PATH"] = custom_path
            reload(config_settings)
            self.assertEqual(config_settings.DATABASES["default"]["NAME"], custom_path)
        finally:
            if had_env:
                os.environ["DJANGO_DB_PATH"] = old_value
            else:
                os.environ.pop("DJANGO_DB_PATH", None)
            reload(config_settings)


class SecretKeyEnvTests(SimpleTestCase):
    def test_debug_mode_uses_development_default_when_env_is_missing(self):
        from config.settings import _DEV_DEFAULT_SECRET_KEY, _get_secret_key

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(_get_secret_key(debug=True), _DEV_DEFAULT_SECRET_KEY)

    def test_production_rejects_missing_empty_and_example_secrets(self):
        from config.settings import _get_secret_key

        unsafe_values = [
            None,
            "",
            "replace-with-a-long-random-secret",
            "django-insecure-" + ("x" * 50),
            "x" * 50,
        ]
        for value in unsafe_values:
            env = {} if value is None else {"DJANGO_SECRET_KEY": value}
            with self.subTest(value=value), patch.dict("os.environ", env, clear=True):
                with self.assertRaises(ImproperlyConfigured):
                    _get_secret_key(debug=False)

    def test_production_accepts_a_long_random_secret(self):
        from config.settings import _get_secret_key

        secret = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        with patch.dict(
            "os.environ",
            {"DJANGO_SECRET_KEY": secret},
            clear=True,
        ):
            self.assertEqual(_get_secret_key(debug=False), secret)
