import datetime
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from core.completion import mark_task_complete
from core.testutils import create_user
from decks.services import get_global_deck
from reviews.analytics import record_trainer_attempt
from reviews.daily import DAILY_TARGET
from reviews.daily_plan import build_daily_plan, drill_of_the_day
from reviews.models import DailyStudyProgress, TrainerAttempt


def _request(user, mode="DE-RU"):
    request = RequestFactory().get("/")
    request.user = user
    request.session = {"language_mode": mode}
    return request


class DrillRotationTests(TestCase):
    def test_rotation_is_deterministic_for_a_day_and_mode(self):
        import datetime

        day = datetime.date(2026, 8, 2)
        self.assertEqual(drill_of_the_day("DE-RU", day), drill_of_the_day("DE-RU", day))

    def test_de_es_pool_excludes_irregular_verbs(self):
        import datetime

        for offset in range(60):
            day = datetime.date(2026, 8, 2) + datetime.timedelta(days=offset)
            self.assertNotEqual(
                drill_of_the_day("DE-ES", day),
                TrainerAttempt.Trainer.IRREGULAR_VERB,
            )

    def test_drill_never_repeats_on_consecutive_days(self):
        import datetime

        day = datetime.date(2026, 1, 1)
        for offset in range(120):
            self.assertNotEqual(
                drill_of_the_day("DE-RU", day + datetime.timedelta(days=offset)),
                drill_of_the_day("DE-RU", day + datetime.timedelta(days=offset + 1)),
                f"drill repeated across offset {offset}",
            )

    def test_drill_pool_is_covered_within_a_cycle(self):
        import datetime

        from reviews.drills import drills_for_mode

        pool = {drill.trainer for drill in drills_for_mode("DE-RU")}
        day = datetime.date(2026, 8, 2)
        seen = {
            drill_of_the_day("DE-RU", day + datetime.timedelta(days=i)) for i in range(len(pool))
        }
        self.assertEqual(seen, pool)


class BuildDailyPlanTests(TestCase):
    def setUp(self):
        self.user = create_user("planner")
        self.other = create_user("other-planner")
        self.deck = get_global_deck(self.user, "DE-RU")

    def test_study_item_reflects_global_deck_progress(self):
        DailyStudyProgress.objects.create(
            deck=self.deck, date=timezone.localdate(), correct_count=DAILY_TARGET
        )
        plan = build_daily_plan(_request(self.user))
        self.assertTrue(plan["study"]["is_done"])
        self.assertEqual(plan["study"]["correct_count"], DAILY_TARGET)

    def test_drill_item_counts_only_correct_attempts_today_for_that_type(self):
        plan = build_daily_plan(_request(self.user))
        chosen = plan["drill"]["trainer"]
        for _ in range(DAILY_TARGET):
            record_trainer_attempt(
                user=self.user, trainer=chosen, language_mode="DE-RU", is_correct=True
            )
        record_trainer_attempt(
            user=self.user, trainer=chosen, language_mode="DE-RU", is_correct=False
        )
        plan = build_daily_plan(_request(self.user))
        self.assertEqual(plan["drill"]["correct_count"], DAILY_TARGET)
        self.assertTrue(plan["drill"]["is_done"])

    def test_media_done_only_after_explicit_completion_today(self):
        plan = build_daily_plan(_request(self.user))
        self.assertFalse(plan["media"]["is_done"])
        for trainer in (TrainerAttempt.Trainer.VIDEO, TrainerAttempt.Trainer.PODCAST):
            record_trainer_attempt(user=self.user, trainer=trainer, language_mode="DE-RU")
        plan = build_daily_plan(_request(self.user))
        self.assertFalse(plan["media"]["is_done"], "opening media is activity, not completion")
        mark_task_complete(self.user, "media", "DE-RU")
        plan = build_daily_plan(_request(self.user))
        self.assertTrue(plan["media"]["is_done"])

    def test_completion_from_another_user_or_another_day_does_not_count(self):
        yesterday = timezone.localdate() - timezone.timedelta(days=1)
        for mode in ("DE-RU", "DE-ES"):
            for task in ("media",):
                with self.subTest(mode=mode, task=task):
                    mark_task_complete(self.other, task, mode)
                    mark_task_complete(self.user, task, mode, date=yesterday)
                    plan = build_daily_plan(_request(self.user, mode))
                    self.assertFalse(plan[task]["is_done"])
                    self.assertTrue(build_daily_plan(_request(self.other, mode))[task]["is_done"])
                    mark_task_complete(self.user, task, mode)
                    self.assertTrue(build_daily_plan(_request(self.user, mode))[task]["is_done"])

    def test_german_tasks_are_complete_only_in_the_exact_active_mode(self):
        for completed_mode, active_mode in (("DE-RU", "DE-ES"), ("DE-ES", "DE-RU")):
            user = create_user(f"mode-planner-{completed_mode}")
            for task in ("media",):
                with self.subTest(completed_mode=completed_mode, task=task):
                    mark_task_complete(user, task, completed_mode)
                    self.assertTrue(
                        build_daily_plan(_request(user, completed_mode))[task]["is_done"]
                    )
                    self.assertFalse(build_daily_plan(_request(user, active_mode))[task]["is_done"])
                    mark_task_complete(user, task, active_mode)
                    self.assertTrue(build_daily_plan(_request(user, active_mode))[task]["is_done"])

    def test_completion_uses_the_local_calendar_day_at_a_utc_date_boundary(self):
        now = datetime.datetime(2026, 10, 1, 23, 30, tzinfo=datetime.UTC)
        with (
            timezone.override("Europe/Berlin"),
            patch("django.utils.timezone.now", return_value=now),
        ):
            for mode in ("DE-RU", "DE-ES"):
                for task in ("media",):
                    with self.subTest(mode=mode, task=task):
                        mark_task_complete(self.user, task, mode, date=now.date())
                        self.assertFalse(
                            build_daily_plan(_request(self.user, mode))[task]["is_done"]
                        )
                        mark_task_complete(self.user, task, mode)
                        self.assertTrue(
                            build_daily_plan(_request(self.user, mode))[task]["is_done"]
                        )

    def test_opening_german_tasks_does_not_complete_either_mode(self):
        for mode in ("DE-RU", "DE-ES"):
            for trainer in (
                TrainerAttempt.Trainer.VIDEO,
                TrainerAttempt.Trainer.PODCAST,
            ):
                record_trainer_attempt(user=self.user, trainer=trainer, language_mode=mode)
            plan = build_daily_plan(_request(self.user, mode))
            with self.subTest(mode=mode):
                self.assertFalse(plan["media"]["is_done"])
                self.assertEqual(plan["completed"], 0)

    def test_completion_in_another_language_pack_does_not_count(self):
        mark_task_complete(self.user, "media", "NL-EN")
        plan = build_daily_plan(_request(self.user, "DE-RU"))
        self.assertFalse(plan["media"]["is_done"])

    def test_plan_is_isolated_to_the_requesting_user(self):
        chosen = build_daily_plan(_request(self.user))["drill"]["trainer"]
        for _ in range(DAILY_TARGET):
            record_trainer_attempt(
                user=self.other, trainer=chosen, language_mode="DE-RU", is_correct=True
            )
        mark_task_complete(self.other, "media", "DE-RU")
        plan = build_daily_plan(_request(self.user))
        self.assertEqual(plan["drill"]["correct_count"], 0)
        self.assertFalse(plan["media"]["is_done"])

    def test_rollup_counts_completed_items(self):
        plan = build_daily_plan(_request(self.user))
        self.assertEqual(plan["total"], 3)
        self.assertEqual(plan["completed"], 0)
        mark_task_complete(self.user, "media", "DE-RU")
        plan = build_daily_plan(_request(self.user))
        self.assertEqual(plan["completed"], 1)
