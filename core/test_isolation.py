from django.urls import reverse
from django.utils import timezone

from core.testutils import AuthedTestCase, create_user
from decks.models import Deck
from decks.services import get_global_deck
from reviews.models import DailyStudyProgress


class DashboardIsolationTests(AuthedTestCase):
    def test_dashboard_daily_plan_ignores_other_users_progress(self):
        other = create_user("other")
        other_deck = get_global_deck(other, Deck.LanguageMode.DE_RU)
        DailyStudyProgress.objects.create(
            deck=other_deck, date=timezone.localdate(), correct_count=20
        )

        response = self.client.get(reverse("core:dashboard"))

        self.assertFalse(response.context["daily_plan"]["study"]["is_done"])
        self.assertEqual(response.context["daily_plan"]["study"]["correct_count"], 0)
