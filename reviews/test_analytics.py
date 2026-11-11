from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from reviews.analytics import build_personal_analytics, record_trainer_attempt
from reviews.models import ReviewLog, TrainerAttempt
from reviews.ratings import Rating


class PersonalAnalyticsTests(TestCase):
    def setUp(self):
        self.user = create_user("analytics")
        self.other = create_user("other-analytics")
        self.deck = Deck.objects.create(
            name="German",
            owner=self.user,
            language_mode=Deck.LanguageMode.DE_RU,
        )
        self.card = Card.objects.create(
            deck=self.deck,
            front="der Tisch",
            back="table",
        )

    def _review(self, rating, days_ago=0):
        reviewed_at = timezone.now() - timezone.timedelta(days=days_ago)
        ReviewLog.objects.create(
            card=self.card,
            rating=rating,
            reviewed_at=reviewed_at,
            previous_due_at=reviewed_at,
            new_due_at=reviewed_at,
            previous_interval_days=0,
            new_interval_days=1,
        )

    def test_analytics_scopes_trainer_events_to_the_owner(self):
        record_trainer_attempt(
            user=self.user,
            trainer=TrainerAttempt.Trainer.ARTICLE,
            language_mode=Deck.LanguageMode.DE_RU,
            is_correct=True,
            item_label="der Tisch",
        )
        record_trainer_attempt(
            user=self.other,
            trainer=TrainerAttempt.Trainer.ARTICLE,
            language_mode=Deck.LanguageMode.DE_RU,
            is_correct=False,
            item_label="die Lampe",
        )

        analytics = build_personal_analytics(self.user, Deck.LanguageMode.DE_RU)

        article = next(item for item in analytics["trainers"] if item["key"] == "article")
        self.assertEqual(article["attempts"], 1)
        self.assertEqual(article["accuracy"], 100)

    def test_analytics_builds_best_and_attention_words(self):
        for _ in range(3):
            self._review(Rating.GOOD)
        for _ in range(2):
            self._review(Rating.AGAIN)

        analytics = build_personal_analytics(self.user, Deck.LanguageMode.DE_RU)

        self.assertEqual(analytics["review_count"], 5)
        self.assertEqual(analytics["accuracy"], 60.0)
        self.assertEqual(analytics["attention_words"][0]["label"], "der Tisch")

    def test_typed_reviews_are_not_counted_in_the_quick_review_row(self):
        # Both study flows write a ReviewLog; only the TrainerAttempt knows which
        # flow it was. The per-trainer breakdown must key off TrainerAttempt so a
        # typed review never inflates the spaced ("Quick review") row.
        for _ in range(3):
            self._review(Rating.GOOD)
            record_trainer_attempt(
                user=self.user,
                trainer=TrainerAttempt.Trainer.TYPED_REVIEW,
                language_mode=Deck.LanguageMode.DE_RU,
                is_correct=True,
                card=self.card,
                item_label=self.card.front,
            )
        for _ in range(2):
            self._review(Rating.AGAIN)
            record_trainer_attempt(
                user=self.user,
                trainer=TrainerAttempt.Trainer.SPACED_REVIEW,
                language_mode=Deck.LanguageMode.DE_RU,
                is_correct=False,
                card=self.card,
                item_label=self.card.front,
            )

        analytics = build_personal_analytics(self.user, Deck.LanguageMode.DE_RU)
        trainers = {item["key"]: item for item in analytics["trainers"]}

        self.assertEqual(trainers[TrainerAttempt.Trainer.TYPED_REVIEW]["attempts"], 3)
        self.assertEqual(trainers[TrainerAttempt.Trainer.SPACED_REVIEW]["attempts"], 2)
        # Headline total still reflects every ReviewLog exactly once.
        self.assertEqual(analytics["review_count"], 5)

    def test_period_parameter_changes_daily_activity_window(self):
        self._review(Rating.GOOD, days_ago=20)

        analytics = build_personal_analytics(self.user, Deck.LanguageMode.DE_RU, period=30)

        self.assertEqual(analytics["period"], 30)
        self.assertEqual(len(analytics["daily_activity"]), 30)
        self.assertEqual(analytics["review_count"], 1)

    def test_grammar_trainer_type_exists_and_is_labelled(self):
        from reviews.analytics import TRAINER_LABELS

        self.assertEqual(TrainerAttempt.Trainer.GRAMMAR.value, "grammar")
        self.assertEqual(TRAINER_LABELS[TrainerAttempt.Trainer.GRAMMAR], "Grammar book")

    def test_nl_en_stats_hide_german_only_trainers(self):
        for _ in range(3):
            record_trainer_attempt(
                user=self.user,
                trainer=TrainerAttempt.Trainer.GRAMMAR,
                language_mode=Deck.LanguageMode.NL_EN,
                is_correct=True,
                item_label="lesson",
            )

        analytics = build_personal_analytics(self.user, Deck.LanguageMode.NL_EN)

        keys = {item["key"] for item in analytics["trainers"]}
        self.assertNotIn(TrainerAttempt.Trainer.GRAMMAR, keys)
        self.assertIsNone(analytics["focus_trainer"])


class RetiredTrainerHistoryTests(AuthedTestCase):
    """Stored history for the removed trainers stays out of the breakdown and focus,
    but still counts toward activity days and the streak."""

    RETIRED = (
        TrainerAttempt.Trainer.GRAMMAR,
        TrainerAttempt.Trainer.TELC_B2,
        TrainerAttempt.Trainer.DEUTSCH_ONLINE,
    )
    MODES = (Deck.LanguageMode.DE_RU, Deck.LanguageMode.NL_EN)

    def _record_retired_history(self, language_mode, occurred_at=None, times=3):
        for trainer in self.RETIRED:
            for _ in range(times):
                record_trainer_attempt(
                    user=self.user,
                    trainer=trainer,
                    language_mode=language_mode,
                    is_correct=False,
                    item_label="legacy",
                    occurred_at=occurred_at,
                )

    def _record_article_attempts(self, language_mode):
        for is_correct in (True, True, False):
            record_trainer_attempt(
                user=self.user,
                trainer=TrainerAttempt.Trainer.ARTICLE,
                language_mode=language_mode,
                is_correct=is_correct,
                item_label="der Tisch",
            )

    def test_retired_trainers_stay_out_of_the_breakdown_and_focus(self):
        for language_mode in self.MODES:
            with self.subTest(language_mode=language_mode):
                self._record_retired_history(language_mode)
                self._record_article_attempts(language_mode)

                analytics = build_personal_analytics(self.user, language_mode)

                keys = {item["key"] for item in analytics["trainers"]}
                self.assertIn(TrainerAttempt.Trainer.ARTICLE, keys)
                self.assertFalse(keys & set(self.RETIRED))
                self.assertEqual(analytics["focus_trainer"]["key"], TrainerAttempt.Trainer.ARTICLE)

    def test_retired_trainer_history_still_counts_toward_activity_and_streak(self):
        now = timezone.now()
        for language_mode in self.MODES:
            with self.subTest(language_mode=language_mode):
                for days_ago in (0, 1, 2):
                    self._record_retired_history(
                        language_mode,
                        occurred_at=now - timezone.timedelta(days=days_ago),
                        times=1,
                    )

                analytics = build_personal_analytics(self.user, language_mode)

                self.assertEqual(analytics["streak"], 3)
                self.assertEqual(
                    [day["count"] for day in analytics["daily_activity"][-3:]], [3, 3, 3]
                )

    def test_stats_page_renders_with_retired_trainer_history(self):
        for language_mode in self.MODES:
            with self.subTest(language_mode=language_mode):
                session = self.client.session
                session["language_mode"] = language_mode
                session.save()
                self._record_retired_history(language_mode)

                response = self.client.get(reverse("core:stats"))

                self.assertEqual(response.status_code, 200)
