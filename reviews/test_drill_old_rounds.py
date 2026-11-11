"""A drill round saved without ``round_format`` is dropped, not converted.

That session held per-drill feedback shapes (and, for verbs with
prepositions, a flat list of choices). Current rounds carry ``round_format``; a
round without it is cleared and the user is sent to the drill's start. Only the
round in the session goes: attempts and streaks are stored rows and stay as
they were.
"""

from django.urls import reverse
from django.utils import timezone

from core.testutils import AuthedTestCase
from reviews.drill_specs import ROUND_FORMAT
from reviews.drills import REGISTRY
from reviews.models import DrillItemProgress, TrainerAttempt

# The session["last"] shapes of a round without ``round_format``, one per drill.
OLD_FEEDBACK = {
    "adjective-prepositions": {
        "correct": False,
        "adjective": "stolz",
        "chosen_preposition": "auf",
        "chosen_case": "D",
        "answers": [{"preposition": "auf", "case": "A", "case_name": "Akkusativ"}],
        "example": "Ich bin stolz auf dich.",
        "correct_streak": 0,
    },
    "verb-prepositions": {
        "correct": False,
        "verb": "warten",
        "case_name": "Akkusativ",
        "results": [
            {"label": "Russian", "chosen": "x", "expected": "ждать", "correct": False},
            {"label": "Präposition", "chosen": "an", "expected": "auf", "correct": False},
        ],
        "correct_streak": 0,
    },
    "irregular-verbs": {
        "correct": False,
        "infinitive": "gehen",
        "results": [
            {
                "key": "present",
                "label": "Präsens",
                "chosen": "x",
                "expected": "geht",
                "correct": False,
            }
        ],
        "correct_streak": 0,
    },
}


class OldRoundTests(AuthedTestCase):
    def drills(self):
        return [REGISTRY.by_slug(slug) for slug in OLD_FEEDBACK]

    def old_round(self, drill, **extra):
        items = [item.id for item in drill.items[:3]]
        round_ = {
            "queue": items,
            "index": 1,
            "seen": 1,
            "correct": 0,
            "last": None,
            "pending_correction": None,
            "pending_retirement": None,
            **extra,
        }
        if drill.trainer == "verb_preposition":
            round_["choices"] = {item: ["auf", "an", "mit"] for item in items}
        session = self.client.session
        session[f"{drill.trainer}_drill_session"] = round_
        session.save()
        return items

    def attempt(self, drill):
        return TrainerAttempt.objects.create(
            owner=self.user,
            trainer=drill.trainer,
            language_mode="DE-RU",
            is_correct=False,
            occurred_at=timezone.now(),
        )

    def urls(self, drill):
        return {
            name: reverse(f"reviews:{name}", args=[drill.slug])
            for name in ("drill_start", "drill", "drill_complete")
        }

    def test_an_old_round_is_cleared_and_redirects_to_the_start(self):
        for drill in self.drills():
            with self.subTest(drill=drill.slug):
                self.old_round(drill, last=OLD_FEEDBACK[drill.slug])
                urls = self.urls(drill)

                for name in ("drill", "drill_complete"):
                    self.old_round(drill, last=OLD_FEEDBACK[drill.slug])
                    response = self.client.get(urls[name])

                    self.assertRedirects(
                        response, urls["drill_start"], fetch_redirect_response=False
                    )
                    self.assertNotIn(f"{drill.trainer}_drill_session", self.client.session)

    def test_an_old_round_with_a_pending_correction_is_dropped_whole(self):
        for drill in self.drills():
            with self.subTest(drill=drill.slug):
                attempt = self.attempt(drill)
                progress = DrillItemProgress.objects.create(
                    owner=self.user,
                    trainer=drill.trainer,
                    item_key=drill.items[0].id,
                    correct_streak=0,
                )
                feedback = OLD_FEEDBACK[drill.slug]
                self.old_round(
                    drill,
                    last=feedback,
                    pending_correction={
                        "attempt_id": attempt.pk,
                        "item_key": drill.items[0].id,
                        "label": "x",
                        "previous_correct_streak": 2,
                        "feedback": feedback,
                    },
                )

                response = self.client.post(
                    self.urls(drill)["drill"], {"action": "mark_last_correct"}
                )

                self.assertRedirects(
                    response, self.urls(drill)["drill_start"], fetch_redirect_response=False
                )
                self.assertNotIn(f"{drill.trainer}_drill_session", self.client.session)
                # Stored rows are untouched: no half-applied "I was right".
                attempt.refresh_from_db()
                progress.refresh_from_db()
                self.assertFalse(attempt.is_correct)
                self.assertEqual(progress.correct_streak, 0)

    def test_the_start_after_a_drop_begins_a_current_round(self):
        for drill in self.drills():
            with self.subTest(drill=drill.slug):
                self.old_round(drill, last=OLD_FEEDBACK[drill.slug])

                self.client.get(self.urls(drill)["drill_start"])
                page = self.client.get(self.urls(drill)["drill"])

                self.assertEqual(page.status_code, 200)
                saved = self.client.session[f"{drill.trainer}_drill_session"]
                self.assertEqual(saved["round_format"], ROUND_FORMAT)
