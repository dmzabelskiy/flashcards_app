"""The drill engine, run on a made-up language folder (``reviews/testdata``).

These tests use every field kind, alternatives, per-target answers, ``targets``
gating, retirement and flashcards without depending on German content.  What
is true of the real packs is in ``test_drill_contract.py`` and
``test_drill_german_facts.py``.
"""

import re
import shutil
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews import drills
from reviews.analytics import build_personal_analytics
from reviews.daily_plan import available_drills, drill_of_the_day
from reviews.drill_specs import ROUND_FORMAT, DataDrillSpec
from reviews.drills import registry_from_directory
from reviews.models import DrillItemProgress, TrainerAttempt

SYNTHETIC_FOLDER = Path(__file__).parent / "testdata" / "languages"
SYNTHETIC = registry_from_directory(SYNTHETIC_FOLDER)
KINDS = "all-kinds"
KINDS_KEY = "synth_kinds_drill_session"
RU_ONLY = "ru-only"
RU_ONLY_KEY = "synth_ru_only_drill_session"
ONE_QUESTION = "one-question"
ONE_QUESTION_KEY = "synth_one_question_drill_session"
GOOD_ALPHA = {"word": "grüne", "gloss": "один", "kind": "X", "tag": "t1"}


class SyntheticRegistryTests(SimpleTestCase):
    def test_modes_offer_the_drills_whose_targets_include_them(self):
        self.assertEqual(
            [d.slug for d in SYNTHETIC.for_mode("DE-RU")], [KINDS, RU_ONLY, ONE_QUESTION]
        )
        self.assertEqual([d.slug for d in SYNTHETIC.for_mode("DE-ES")], [KINDS])
        self.assertEqual(SYNTHETIC.for_mode("NL-EN"), [])

    def test_an_item_without_the_modes_target_is_skipped(self):
        drill = SYNTHETIC.by_slug(KINDS)
        self.assertIn("gamma", [item.id for item in drill.items_for("ru")])
        self.assertNotIn("gamma", [item.id for item in drill.items_for("es")])

    def test_the_pool_of_a_field_is_every_answer_for_the_target(self):
        drill = SYNTHETIC.by_slug(KINDS)
        self.assertEqual(drill.pool("tag", "ru"), ["t1", "t2", "t3", "t4", "t5"])
        self.assertEqual(drill.pool("tag", "es"), ["t1", "t2", "t3", "t5"])

    def test_trainer_and_slug_lookups(self):
        self.assertEqual(SYNTHETIC.for_trainer("DE-RU", "synth_ru_only").slug, RU_ONLY)
        self.assertIsNone(SYNTHETIC.for_trainer("DE-ES", "synth_ru_only"))
        self.assertIsNotNone(SYNTHETIC.by_slug(RU_ONLY))
        self.assertIsNone(SYNTHETIC.by_slug("no-such-drill"))


class SyntheticDrillCase(AuthedTestCase):
    """A logged-in client whose registry is the made-up language folder."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(drills, "REGISTRY", SYNTHETIC)
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_mode(self, mode):
        session = self.client.session
        session["language_mode"] = mode
        session.save()

    def url(self, name, slug=KINDS):
        return reverse(f"reviews:{name}", args=[slug])

    def set_round(self, queue, key=KINDS_KEY, **extra):
        session = self.client.session
        session[key] = {
            "queue": list(queue),
            "index": 0,
            "seen": 0,
            "correct": 0,
            "last": None,
            "pending_correction": None,
            "pending_retirement": None,
            "round_format": ROUND_FORMAT,
            **extra,
        }
        session.save()

    def round_of(self, *queue, slug=KINDS, **extra):
        """A round of these items, its choices made by the drill's own preparation."""
        drill = SYNTHETIC.by_slug(slug)
        spec = DataDrillSpec(drill)
        request = RequestFactory().get("/")
        request.user = self.user
        request.session = self.client.session
        context = spec.context(request)
        prepared = {}
        spec.prepare(prepared, [spec._item(drill.item(item)) for item in queue], context)
        self.set_round(queue, key=spec.session_key, **prepared, **extra)

    def choices_of(self, item, field="tag", key=KINDS_KEY):
        return self.saved(key)["choices"][item][field]

    def saved(self, key=KINDS_KEY):
        return self.client.session[key]

    def answer(self, post, key=KINDS_KEY):
        self.client.post(self.url("drill"), post)
        return self.saved(key)

    def progress(self, trainer, item_key):
        return DrillItemProgress.objects.get(owner=self.user, trainer=trainer, item_key=item_key)


class DataDrillRoundTests(SyntheticDrillCase):
    def test_start_builds_a_round_of_the_drills_size(self):
        response = self.client.get(self.url("drill_start"))

        self.assertRedirects(response, self.url("drill"))
        queue = self.saved()["queue"]
        self.assertEqual(len(queue), 3)
        self.assertEqual(len(set(queue)), 3)

    def test_a_drill_without_round_size_asks_every_item(self):
        self.client.get(self.url("drill_start", RU_ONLY))

        self.assertCountEqual(self.saved(RU_ONLY_KEY)["queue"], ["one", "two"])

    def test_start_fixes_the_pool_choices_of_every_item_for_the_round(self):
        self.client.get(self.url("drill_start"))

        session = self.saved()
        for item_id in session["queue"]:
            choices = session["choices"][item_id]["tag"]
            self.assertEqual(len(set(choices)), len(choices))
            self.assertGreaterEqual(len(choices), 3)

    def test_the_step_page_has_one_input_per_field_from_the_file(self):
        self.round_of("alpha")

        page = self.client.get(self.url("drill"))

        self.assertContains(page, "<h2>alpha</h2>")
        self.assertContains(page, "Translate into Russian.")
        self.assertContains(page, 'name="word"')
        self.assertContains(page, 'placeholder="e.g. blorp"')
        self.assertContains(page, "Made-up field hint")
        self.assertContains(page, 'name="gloss"')
        self.assertContains(page, "<span>Russian</span>")
        self.assertContains(page, 'name="kind" value="X"')
        self.assertContains(page, "<b>Y</b>Why")
        self.assertIn("t1", self.choices_of("alpha"))
        for tag in self.choices_of("alpha"):
            self.assertContains(page, f'name="tag" type="radio" value="{tag}"')
        self.assertContains(page, "Made-up footer hint")
        self.assertContains(page, "Question <b>1</b> / 1")

    def test_choice_and_pool_fields_are_labelled_radio_groups(self):
        self.round_of("alpha")

        page = self.client.get(self.url("drill")).content.decode()

        self.assertEqual(page.count("<fieldset"), 2)
        self.assertIn("<legend>Kind</legend>", page)
        self.assertIn("<legend>Tag</legend>", page)

    def test_the_target_label_follows_the_mode(self):
        self.set_mode("DE-ES")
        self.round_of("alpha")

        page = self.client.get(self.url("drill"))

        self.assertContains(page, "Translate into Spanish.")
        self.assertContains(page, "<span>Spanish</span>")

    def test_the_pool_choice_is_every_accepted_answer_plus_two_others_from_the_pool(self):
        accepted = {
            "alpha": {"t1"},
            "beta": {"t2", "t3"},  # one value per alternative
            "gamma": {"t4"},
            "delta": {"t5"},
            "epsilon": {"t1"},
            "zeta": {"t2"},
        }
        for _ in range(15):
            self.client.get(self.url("drill_start"))
            session = self.saved()
            for item_id in session["queue"]:
                choices = session["choices"][item_id]["tag"]
                self.assertTrue(accepted[item_id] <= set(choices))
                self.assertEqual(len(choices), len(accepted[item_id]) + 2)
                self.assertEqual(len(set(choices)), len(choices))
                self.assertTrue(set(choices) <= {"t1", "t2", "t3", "t4", "t5"})

    def test_the_choices_do_not_change_between_page_loads(self):
        self.client.get(self.url("drill_start"))
        first = self.client.get(self.url("drill")).content.decode()
        second = self.client.get(self.url("drill")).content.decode()

        pattern = re.compile(r'name="tag" type="radio" value="([^"]+)"')
        self.assertEqual(
            len(pattern.findall(first)), len(self.choices_of(self.saved()["queue"][0]))
        )
        self.assertEqual(pattern.findall(first), pattern.findall(second))

    def test_a_finished_round_redirects_to_completion_and_renders_it(self):
        self.round_of("alpha")
        self.answer(GOOD_ALPHA)

        page = self.client.get(self.url("drill"))
        self.assertRedirects(page, self.url("drill_complete"), fetch_redirect_response=False)
        complete = self.client.get(self.url("drill_complete"))

        self.assertContains(complete, "100%")
        self.assertContains(complete, "01 / 01 correct")
        self.assertContains(complete, "Start another round")
        self.assertContains(complete, self.url("drill_start"))


class DataDrillCheckTests(SyntheticDrillCase):
    def setUp(self):
        super().setUp()
        self.round_of("alpha", "beta")

    def test_a_fully_correct_answer_scores_and_shows_every_row_right(self):
        session = self.answer(GOOD_ALPHA)

        self.assertEqual((session["seen"], session["correct"]), (1, 1))
        rows = session["last"]["rows"]
        self.assertEqual([row["correct"] for row in rows], [True, True, True, True])
        self.assertEqual(session["last"]["example"], "Example of alpha.")
        self.assertEqual(session["last"]["reveal"], [["Note", "first"]])

    def test_one_wrong_field_fails_the_answer_and_marks_only_that_row(self):
        wrong = {"word": "zzz", "gloss": "zzz", "kind": "Y", "tag": "t2"}
        for position, (key, value) in enumerate(wrong.items()):
            with self.subTest(field=key):
                self.round_of("alpha")
                session = self.answer({**GOOD_ALPHA, key: value})

                self.assertEqual(session["correct"], 0)
                flags = [row["correct"] for row in session["last"]["rows"]]
                self.assertEqual(flags, [index != position for index in range(4)])

    def test_every_accepted_answer_of_a_typed_field_is_accepted(self):
        for gloss in ("один", "раз"):
            with self.subTest(gloss=gloss):
                self.round_of("alpha")
                self.assertEqual(self.answer({**GOOD_ALPHA, "gloss": gloss})["correct"], 1)

    def test_typed_fields_match_through_their_named_normaliser(self):
        self.round_of("alpha")

        session = self.answer({**GOOD_ALPHA, "word": "  GRUENE ", "gloss": "Один!"})

        self.assertEqual(session["correct"], 1)

    def test_an_empty_answer_is_wrong(self):
        session = self.answer({"word": "", "gloss": "", "kind": "", "tag": ""})

        self.assertEqual(session["correct"], 0)
        self.assertEqual(session["seen"], 1)

    def test_the_feedback_page_shows_expected_given_and_the_example(self):
        self.answer({**GOOD_ALPHA, "gloss": "nope"})

        page = self.client.get(self.url("drill"))

        self.assertContains(page, "Review the marked fields")
        self.assertContains(page, "<strong>один / раз</strong>")
        self.assertContains(page, "Your answer: nope")
        self.assertContains(page, "Note: first")
        self.assertContains(page, "Example of alpha.")
        self.assertContains(page, "I was right")

    def test_a_choice_is_shown_by_its_label(self):
        self.answer({**GOOD_ALPHA, "kind": "Y"})

        page = self.client.get(self.url("drill"))

        self.assertContains(page, "<strong>Ex</strong>")
        self.assertContains(page, "Your answer: Why")

    def test_a_choice_value_must_arrive_whole(self):
        self.assertEqual(self.answer({**GOOD_ALPHA, "kind": "x"})["correct"], 1)

        self.round_of("alpha")
        self.assertEqual(self.answer({**GOOD_ALPHA, "kind": " X "})["correct"], 0)

    def test_a_pool_choice_outside_the_rounds_choices_is_wrong(self):
        outside = next(
            tag for tag in ("t2", "t3", "t4", "t5") if tag not in self.choices_of("alpha")
        )

        self.assertEqual(self.answer({**GOOD_ALPHA, "tag": outside})["correct"], 0)

    def test_the_exact_normaliser_ignores_case_and_surrounding_space(self):
        self.set_round(["one"], key=RU_ONLY_KEY)

        self.client.post(self.url("drill", RU_ONLY), {"answer": " eins "})

        self.assertEqual(self.saved(RU_ONLY_KEY)["correct"], 1)


class DataDrillAlternativeTests(SyntheticDrillCase):
    def setUp(self):
        super().setUp()
        self.round_of("beta", "alpha")
        self.first = {"word": "aa", "gloss": "a-ru", "kind": "X", "tag": "t2"}
        self.second = {"word": "bb", "gloss": "b-ru", "kind": "Y", "tag": "t3"}

    def test_the_prepared_choices_offer_the_value_of_every_alternative(self):
        for _ in range(10):
            self.round_of("beta")
            self.assertTrue({"t2", "t3"} <= set(self.choices_of("beta")))

    def test_either_complete_alternative_is_correct_in_a_prepared_round(self):
        for _ in range(10):
            self.round_of("beta", "alpha")
            self.assertEqual(self.answer(self.first)["correct"], 1)

            self.round_of("beta", "alpha")
            self.assertEqual(self.answer(self.second)["correct"], 1)

    def test_fields_taken_from_different_alternatives_are_wrong(self):
        mixed = {**self.first, "word": "bb"}

        self.assertEqual(self.answer(mixed)["correct"], 0)

    def test_the_example_of_the_matching_alternative_is_shown(self):
        self.answer(self.second)

        page = self.client.get(self.url("drill"))

        self.assertContains(page, "Example of beta two.")
        self.assertNotContains(page, "Example of beta one.")

    def test_a_wrong_answer_shows_the_first_alternative_and_all_accepted_ones(self):
        self.answer({**self.first, "word": "zzz"})

        page = self.client.get(self.url("drill"))

        self.assertContains(page, "Example of beta one.")
        self.assertContains(page, "aa · a-ru · Ex · t2")
        self.assertContains(page, "bb · b-ru · Why · t3")


class DataDrillTargetTests(SyntheticDrillCase):
    def test_answers_are_per_target(self):
        self.set_mode("DE-ES")
        self.round_of("alpha")
        self.assertEqual(self.answer({**GOOD_ALPHA, "gloss": "один"})["correct"], 0)

        self.round_of("alpha")
        self.assertEqual(self.answer({**GOOD_ALPHA, "gloss": "uno"})["correct"], 1)

    def test_an_item_without_the_targets_answers_is_never_asked(self):
        self.set_mode("DE-ES")
        self.round_of("gamma")

        response = self.client.get(self.url("drill"))

        self.assertRedirects(response, self.url("drill_start"), fetch_redirect_response=False)
        self.assertNotIn(KINDS_KEY, self.client.session)

    def test_start_in_a_target_never_queues_items_without_it(self):
        self.set_mode("DE-ES")
        for _ in range(20):
            self.client.get(self.url("drill_start"))
            self.assertNotIn("gamma", self.saved()["queue"])

    def test_a_drill_whose_targets_exclude_the_mode_is_not_offered(self):
        self.set_mode("DE-ES")

        for name in ("drill_start", "drill", "drill_complete"):
            response = self.client.get(self.url(name, RU_ONLY), follow=True)
            self.assertEqual(response.redirect_chain[-1][0], reverse("reviews:drills"))
            self.assertContains(response, "not available in the selected language mode")

    def test_a_slug_in_no_pack_is_a_404(self):
        for name in ("drill_start", "drill", "drill_complete"):
            self.assertEqual(self.client.get(self.url(name, "no-such-drill")).status_code, 404)

    def test_the_drills_index_lists_exactly_the_offered_drills(self):
        self.assertEqual(
            [d["slug"] for d in available_drills("DE-RU")], [KINDS, RU_ONLY, ONE_QUESTION]
        )
        page = self.client.get(reverse("reviews:drills"))
        self.assertContains(page, "All kinds")
        self.assertContains(page, "Russian only")
        self.assertContains(page, reverse("reviews:flashcards_start", args=["synth_kinds"]))
        self.assertContains(page, "#pic-verb")

        self.set_mode("DE-ES")
        page = self.client.get(reverse("reviews:drills"))
        self.assertContains(page, "All kinds")
        self.assertNotContains(page, "Russian only")

        self.set_mode("NL-EN")
        page = self.client.get(reverse("reviews:drills"))
        self.assertContains(page, "No drills are available for this study language yet.")

    def test_a_mode_with_no_drills_cannot_start_one(self):
        self.set_mode("NL-EN")

        response = self.client.get(self.url("drill_start"))

        self.assertRedirects(response, reverse("reviews:drills"))


class DataDrillProgressTests(SyntheticDrillCase):
    """Streaks, retirement and "I was right" for a drill that is only data."""

    trainer = "synth_ru_only"

    def post(self, answer):
        return self.client.post(self.url("drill", RU_ONLY), {"answer": answer})

    def set_queue(self, *ids):
        self.set_round(ids, key=RU_ONLY_KEY)

    def test_correct_answers_build_a_streak_and_a_wrong_one_resets_it(self):
        for expected in (1, 2):
            self.set_queue("one")
            self.post("eins")
            self.assertEqual(self.progress(self.trainer, "one").correct_streak, expected)

        self.set_queue("one")
        self.post("nope")

        self.assertEqual(self.progress(self.trainer, "one").correct_streak, 0)

    def test_attempts_are_recorded_under_the_drills_trainer_and_label(self):
        self.set_queue("one", "two")
        self.post("eins")
        self.post("nope")

        attempts = TrainerAttempt.objects.filter(trainer=self.trainer).order_by("occurred_at")
        self.assertEqual([a.is_correct for a in attempts], [True, False])
        self.assertEqual([a.item_label for a in attempts], ["one", "two"])
        self.assertEqual(attempts[0].language_mode, "DE-RU")

    def test_three_correct_answers_offer_retirement_and_retiring_empties_the_pool(self):
        for _ in range(3):
            self.set_queue("one")
            self.post("eins")
        self.assertEqual(self.saved(RU_ONLY_KEY)["pending_retirement"]["item_key"], "one")
        complete = self.client.get(self.url("drill_complete", RU_ONLY))
        self.assertContains(complete, "Retire one from trainer")

        response = self.client.post(self.url("drill", RU_ONLY), {"action": "retire_word"})

        self.assertRedirects(response, self.url("drill", RU_ONLY), fetch_redirect_response=False)
        self.assertIsNotNone(self.progress(self.trainer, "one").retired_at)
        self.client.get(self.url("drill_start", RU_ONLY))
        self.assertEqual(self.saved(RU_ONLY_KEY)["queue"], ["two"])
        self.client.get(reverse("reviews:flashcards_start", args=[self.trainer]))
        self.assertEqual(self.client.session["flashcards_session"]["queue"], ["two"])

    def test_i_was_right_corrects_the_score_the_attempt_and_the_streak(self):
        DrillItemProgress.objects.create(
            owner=self.user, trainer=self.trainer, item_key="one", correct_streak=2
        )
        self.set_queue("one", "two")
        self.post("nope")
        attempt = TrainerAttempt.objects.get()
        self.assertFalse(attempt.is_correct)
        self.assertEqual(self.progress(self.trainer, "one").correct_streak, 0)
        self.assertContains(self.client.get(self.url("drill", RU_ONLY)), "I was right")

        self.client.post(self.url("drill", RU_ONLY), {"action": "mark_last_correct"})

        attempt.refresh_from_db()
        self.assertTrue(attempt.is_correct)
        self.assertEqual(self.saved(RU_ONLY_KEY)["correct"], 1)
        self.assertIsNone(self.saved(RU_ONLY_KEY).get("pending_correction"))
        self.assertEqual(self.progress(self.trainer, "one").correct_streak, 3)
        corrected = self.client.get(self.url("drill", RU_ONLY))
        self.assertContains(corrected, "3 correct in a row")
        self.assertContains(corrected, "Retire from trainer")


class DataDrillFlashcardTests(SyntheticDrillCase):
    def test_the_card_is_derived_from_the_drill_file(self):
        self.client.get(reverse("reviews:flashcards_start", args=["synth_kinds"]))
        session = self.client.session
        session["flashcards_session"]["queue"] = ["alpha"]
        session["flashcards_session"]["index"] = 0
        session.save()

        page = self.client.get(reverse("reviews:flashcards", args=["synth_kinds"]))

        self.assertContains(page, ">alpha</h2>")
        self.assertContains(page, "All kinds")
        self.assertContains(page, "Translate into Russian.")
        for label, value in (
            ("Form", "grüne"),
            ("Russian", "один / раз"),
            ("Kind", "Ex"),
            ("Tag", "t1"),
            ("Note", "first"),
        ):
            self.assertContains(page, f"<dt>{label}</dt>")
            self.assertContains(page, f"<dd>{value}</dd>")
        self.assertContains(page, "Example of alpha.")

    def test_only_the_first_alternative_is_shown(self):
        self.client.get(reverse("reviews:flashcards_start", args=["synth_kinds"]))
        session = self.client.session
        session["flashcards_session"]["queue"] = ["beta"]
        session.save()

        page = self.client.get(reverse("reviews:flashcards", args=["synth_kinds"]))

        self.assertContains(page, "<dd>aa</dd>")
        self.assertNotContains(page, "<dd>bb</dd>")

    def test_the_queue_follows_the_modes_target(self):
        self.client.get(reverse("reviews:flashcards_start", args=["synth_kinds"]))
        self.assertEqual(len(self.client.session["flashcards_session"]["queue"]), 6)

        self.set_mode("DE-ES")
        self.client.get(reverse("reviews:flashcards_start", args=["synth_kinds"]))
        queue = self.client.session["flashcards_session"]["queue"]
        self.assertCountEqual(queue, ["alpha", "beta", "delta", "epsilon", "zeta"])

    def test_a_drill_the_mode_does_not_offer_has_no_flashcards(self):
        self.set_mode("DE-ES")

        response = self.client.get(reverse("reviews:flashcards_start", args=["synth_ru_only"]))

        self.assertRedirects(response, reverse("core:dashboard"))

    def test_a_saved_session_for_a_stale_item_restarts_instead_of_raising(self):
        self.client.get(reverse("reviews:flashcards_start", args=["synth_kinds"]))
        session = self.client.session
        session["flashcards_session"]["queue"] = ["not-an-item", 7, None]
        session.save()

        for index in range(3):
            session = self.client.session
            session["flashcards_session"]["index"] = index
            session.save()
            response = self.client.get(reverse("reviews:flashcards", args=["synth_kinds"]))
            self.assertEqual(response.status_code, 302)

    def test_a_malformed_flashcard_session_restarts_instead_of_raising(self):
        for payload in ("text", {"trainer": "synth_kinds"}, {"trainer": "synth_kinds", "queue": 3}):
            session = self.client.session
            session["flashcards_session"] = payload
            session.save()
            for name in ("flashcards", "flashcards_complete"):
                response = self.client.get(reverse(f"reviews:{name}", args=["synth_kinds"]))
                self.assertRedirects(
                    response,
                    reverse("reviews:flashcards_start", args=["synth_kinds"]),
                    fetch_redirect_response=False,
                )


class DataDrillSessionCompatibilityTests(SyntheticDrillCase):
    """A round that is damaged, or was saved by the older code, is dropped, never an error."""

    def test_a_round_without_the_current_format_is_discarded(self):
        self.round_of("alpha")
        session = self.client.session
        del session[KINDS_KEY]["round_format"]
        session.save()

        response = self.client.get(self.url("drill"))

        self.assertRedirects(response, self.url("drill_start"), fetch_redirect_response=False)
        self.assertNotIn(KINDS_KEY, self.client.session)

    def test_the_round_start_stores_the_current_format(self):
        self.client.get(self.url("drill_start"))

        self.assertEqual(self.saved()["round_format"], ROUND_FORMAT)

    def test_old_choices_that_lack_an_accepted_answer_are_rebuilt(self):
        self.set_round(["alpha"], choices={"alpha": {"tag": ["t4", "t5"]}})

        page = self.client.get(self.url("drill"))

        self.assertContains(page, 'name="tag" type="radio" value="t1"')

    def test_damaged_sessions_are_discarded_with_a_redirect(self):
        damaged = [
            {"queue": "text", "index": 0, "seen": 0, "correct": 0},
            {"queue": ["alpha"], "index": "0", "seen": 0, "correct": 0},
            {"queue": ["alpha"], "index": 5, "seen": 0, "correct": 0},
            {"queue": [["alpha"], 3, None], "index": 0, "seen": 0, "correct": 0},
            {"queue": ["no-such-item"], "index": 0, "seen": 0, "correct": 0},
            {"cursor": 0},
            "not a dict",
        ]
        for payload in damaged:
            with self.subTest(payload=payload):
                session = self.client.session
                session[KINDS_KEY] = payload
                session.save()

                response = self.client.get(self.url("drill"))

                self.assertEqual(response.status_code, 302)
                self.assertNotIn(KINDS_KEY, self.client.session)

    def test_a_damaged_choices_map_is_replaced(self):
        self.set_round(["alpha"], choices="text")

        page = self.client.get(self.url("drill"))

        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(self.saved()["choices"]["alpha"]["tag"]), 3)


class OneQuestionDrillTests(SyntheticDrillCase):
    """A drill with one item and a round of one question runs start to finish."""

    def url(self, name, slug=ONE_QUESTION):
        return super().url(name, slug)

    def test_start_queues_the_single_item(self):
        self.client.get(self.url("drill_start"))

        self.assertEqual(self.saved(ONE_QUESTION_KEY)["queue"], ["only"])

    def test_a_correct_answer_ends_the_round(self):
        self.client.get(self.url("drill_start"))

        self.client.post(self.url("drill"), {"answer": "Einzig"})

        session = self.saved(ONE_QUESTION_KEY)
        self.assertEqual((session["seen"], session["correct"]), (1, 1))
        self.assertRedirects(
            self.client.get(self.url("drill")),
            self.url("drill_complete"),
            fetch_redirect_response=False,
        )
        self.assertContains(self.client.get(self.url("drill_complete")), "100%")

    def test_a_wrong_answer_ends_the_round_with_feedback_and_a_correction(self):
        self.client.get(self.url("drill_start"))

        self.client.post(self.url("drill"), {"answer": "nope"})

        self.assertEqual(self.saved(ONE_QUESTION_KEY)["correct"], 0)
        self.assertRedirects(
            self.client.get(self.url("drill")),
            self.url("drill_complete"),
            fetch_redirect_response=False,
        )


class MultipleAcceptedValuesTests(SimpleTestCase):
    """A pool field whose accepted values differ between and within alternatives."""

    DRILL = textwrap.dedent(
        """
        slug: many
        trainer: synth_many
        label: Many
        description: Several accepted values.
        fields:
          - {key: pick, label: Pick, kind: pool_choice, distractors: 2}
        items:
          - id: multi
            prompt: multi
            alternatives:
              - pick: [a, b]
              - pick: [c]
          - id: other
            prompt: other
            alternatives:
              - pick: [d]
              - pick: [e]
              - pick: [f]
        """
    )
    PACK = textwrap.dedent(
        """
        code: de
        name: Testland
        articles: []
        article_genders: []
        speech_locale: de-DE
        media: {video_channel: null, podcast: false}
        """
    )

    def setUp(self):
        scratch = Path(settings.BASE_DIR) / "temp"
        scratch.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(dir=scratch))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "de" / "drills").mkdir(parents=True)
        (root / "de" / "pack.yml").write_text(self.PACK, encoding="utf-8")
        (root / "de" / "drills" / "many.yml").write_text(self.DRILL, encoding="utf-8")
        self.drill = registry_from_directory(root).by_slug("many")
        self.spec = DataDrillSpec(self.drill)
        self.context = {"target": "ru", "target_label": "Russian"}

    def prepared(self, item_id):
        session = {}
        item = self.spec._item(self.drill.item(item_id))
        self.spec.prepare(session, [item], self.context)
        return session["choices"][item_id]["pick"]

    def test_the_choices_hold_every_accepted_value_and_two_that_are_not(self):
        for _ in range(20):
            choices = self.prepared("multi")
            self.assertEqual(len(choices), 5)
            self.assertTrue({"a", "b", "c"} <= set(choices))
            self.assertTrue(set(choices) - {"a", "b", "c"} <= {"d", "e", "f"})

    def test_every_alternative_can_be_answered_correctly(self):
        item = self.spec._item(self.drill.item("other"))
        for value in ("d", "e", "f"):
            with self.subTest(value=value):
                session = {}
                self.spec.prepare(session, [item], self.context)
                result = self.spec.check(item, {"pick": value}, session, self.context)
                self.assertTrue(result.is_correct)


class DataDrillListsFollowThePackTests(SyntheticDrillCase):
    def test_the_drill_of_the_day_is_drawn_from_the_modes_drills(self):
        ru = {drill_of_the_day("DE-RU", day) for day in self.days(10)}
        es = {drill_of_the_day("DE-ES", day) for day in self.days(10)}

        self.assertEqual(ru, {"synth_kinds", "synth_ru_only", "synth_one_question"})
        self.assertEqual(es, {"synth_kinds"})

    @staticmethod
    def days(count):
        import datetime

        return [
            datetime.date(2026, 3, 1) + datetime.timedelta(days=offset) for offset in range(count)
        ]

    def test_the_analytics_breakdown_lists_the_modes_drills_with_their_own_start_link(self):
        for trainer, correct in (("synth_kinds", True), ("synth_ru_only", False)):
            for _ in range(3):
                TrainerAttempt.objects.create(
                    owner=self.user,
                    trainer=trainer,
                    language_mode="DE-RU",
                    is_correct=correct,
                    occurred_at=timezone.now(),
                )

        analytics = build_personal_analytics(self.user, "DE-RU", 7)

        rows = {row["key"]: row for row in analytics["trainers"]}
        self.assertEqual(rows["synth_kinds"]["label"], "All kinds")
        self.assertEqual(rows["synth_ru_only"]["accuracy"], 0)
        self.assertEqual(analytics["focus_trainer"]["key"], "synth_ru_only")
        page = self.client.get(reverse("core:stats"))
        self.assertContains(page, f'href="{self.url("drill_start", RU_ONLY)}">Start trainer')

    def test_drills_the_mode_does_not_offer_are_left_out_of_the_breakdown(self):
        for _ in range(3):
            TrainerAttempt.objects.create(
                owner=self.user,
                trainer="synth_ru_only",
                language_mode="DE-ES",
                is_correct=True,
                occurred_at=timezone.now(),
            )

        analytics = build_personal_analytics(self.user, "DE-ES", 7)

        self.assertNotIn("synth_ru_only", [row["key"] for row in analytics["trainers"]])

    def test_the_dashboard_drill_item_links_to_the_drills_start_url(self):
        with mock.patch("reviews.daily_plan.drill_of_the_day", return_value="synth_ru_only"):
            page = self.client.get(reverse("core:dashboard"))

        self.assertContains(page, self.url("drill_start", RU_ONLY))
        self.assertContains(page, "Russian only")


class MalformedDrillSessionTests(AuthedTestCase):
    """An in-flight article round from an older release must not crash the engine."""

    def setUp(self):
        super().setUp()
        self.deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        self.noun = Card.objects.create(
            deck=self.deck, front="der Tisch", back="table", article="der"
        )
        self.step = reverse("reviews:drill", args=["articles"])
        self.start = reverse("reviews:drill_start", args=["articles"])

    def _write(self, payload):
        session = self.client.session
        session["article_drill_session"] = payload
        session.save()

    def test_session_without_the_engine_keys_is_cleared_and_restarted(self):
        self._write({"queue": [self.noun.pk], "cursor": 0})

        response = self.client.get(self.step)

        self.assertRedirects(response, self.start, fetch_redirect_response=False)
        self.assertNotIn("article_drill_session", self.client.session)

    def test_session_whose_queue_no_longer_resolves_is_cleared_and_restarted(self):
        self._write(
            {"queue": [self.noun.pk + 9999], "index": 0, "seen": 0, "correct": 0, "last": None}
        )

        response = self.client.get(self.step)

        self.assertRedirects(response, self.start, fetch_redirect_response=False)
        self.assertNotIn("article_drill_session", self.client.session)

    def test_clearing_a_broken_session_adds_no_message(self):
        self._write({"queue": "not-a-queue", "index": 0, "seen": 0, "correct": 0})

        response = self.client.get(self.step, follow=True)

        self.assertEqual(list(response.context["messages"]), [])


class ArticleDrillRetirementTests(AuthedTestCase):
    def test_a_retired_card_leaves_the_article_drill_pool(self):
        deck = Deck.objects.create(
            name="RU", language_mode=Deck.LanguageMode.DE_RU, owner=self.user
        )
        kept = Card.objects.create(deck=deck, front="der Tisch", back="стол", article="der")
        retired = Card.objects.create(deck=deck, front="die Lampe", back="лампа", article="die")
        DrillItemProgress.objects.create(
            owner=self.user,
            trainer=TrainerAttempt.Trainer.ARTICLE,
            item_key=str(retired.pk),
            correct_streak=3,
            retired_at=timezone.now(),
        )

        self.client.get(reverse("reviews:drill_start", args=["articles"]))

        self.assertEqual(self.client.session["article_drill_session"]["queue"], [kept.pk])
