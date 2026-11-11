"""One contract every real pack, study mode and drill file must meet.

Nothing here names a drill: it walks ``languages/*/drills`` through the
registry, so a new language or drill is covered by adding its files.  The
behaviour of the engine itself is in ``test_drill_engine.py``.
"""

from pathlib import Path
from unittest import mock

from django.conf import settings
from django.urls import reverse

from cards.models import Card
from core.language_modes import get_target_language, get_target_language_label
from core.language_packs import PACKS, pack_for_mode
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews import drills as drill_module
from reviews.daily_plan import available_drills, drill_of_the_day
from reviews.drill_specs import spec_for
from reviews.drills import REGISTRY, RESERVED_TRAINERS, load_registry, registry_from_directory
from reviews.models import TrainerAttempt

MODES = Deck.LanguageMode.values
WRONG = "zzz-wrong"
PICTOGRAMS = Path(settings.BASE_DIR, "templates", "core", "_pictograms.html").read_text()


def offered(drill, mode):
    """The rule, stated independently of the registry: pack contents plus targets."""
    pack = pack_for_mode(mode)
    if drill.builtin:
        return bool(pack.articles)
    return drill.pack == pack.code and drill.offered_for(get_target_language(mode))


def every_drill():
    seen = set()
    for drills in REGISTRY.drills_by_pack.values():
        for drill in drills:
            if drill.slug not in seen:
                seen.add(drill.slug)
                yield drill


class DrillFileContractTests(AuthedTestCase):
    def test_every_file_loads_and_the_registry_is_consistent(self):
        reloaded = load_registry(settings.LANGUAGES_DIR, PACKS)

        self.assertEqual(set(reloaded.drills_by_pack), set(PACKS))
        for pack, drills in reloaded.drills_by_pack.items():
            self.assertEqual(
                [d.slug for d in drills], [d.slug for d in REGISTRY.drills_by_pack[pack]]
            )

    def test_slugs_and_trainers_are_unique_and_do_not_reuse_other_trainers(self):
        data = [d for d in every_drill() if not d.builtin]
        self.assertEqual(len({d.slug for d in data}), len(data))
        self.assertEqual(len({d.trainer for d in data}), len(data))
        for drill in data:
            with self.subTest(drill=drill.slug):
                self.assertNotIn(drill.trainer, RESERVED_TRAINERS)
        # The loader's reserved names are the built-in and legacy trainers of the enum.
        self.assertEqual(
            RESERVED_TRAINERS, set(TrainerAttempt.Trainer.values) - {d.trainer for d in data}
        )

    def test_article_drill_is_first_in_every_pack_that_has_articles(self):
        for code, pack in PACKS.items():
            drills = REGISTRY.drills_by_pack[code]
            with self.subTest(pack=code):
                builtin = [d for d in drills if d.builtin]
                if pack.articles:
                    self.assertEqual(builtin, [drills[0]])
                    self.assertEqual(drills[0].slug, "articles")
                else:
                    self.assertEqual(builtin, [])

    def test_every_drill_names_a_pictogram_that_exists_and_has_text(self):
        for drill in every_drill():
            with self.subTest(drill=drill.slug):
                self.assertIn(f'id="pic-{drill.pictogram}"', PICTOGRAMS)
                self.assertTrue(drill.label and drill.description)
                self.assertLessEqual(len(drill.trainer), 32)

    def test_every_item_has_an_answer_in_every_target_the_drill_offers(self):
        for mode in MODES:
            target = get_target_language(mode)
            for drill in REGISTRY.for_mode(mode):
                if drill.builtin:
                    continue
                with self.subTest(mode=mode, drill=drill.slug):
                    self.assertTrue(drill.items_for(target), "no item is askable in this mode")
                    label = get_target_language_label(mode)
                    for text in (drill.label, drill.instruction, drill.hint):
                        self.assertNotIn("{", text.replace("{target_label}", label))
                    for field in drill.fields:
                        for text in (field.label, field.hint, field.placeholder):
                            self.assertNotIn("{", text.replace("{target_label}", label))


class DrillGradingContractTests(AuthedTestCase):
    """For every item: what the file accepts is correct, a wrong answer is not."""

    def context(self, mode):
        return {
            "user": self.user,
            "language_mode": mode,
            "target": get_target_language(mode),
            "target_label": get_target_language_label(mode),
        }

    def grade(self, spec, context, item, post):
        return spec.check(item, post, {}, context).is_correct

    def wrong_value(self, field, entry, target):
        if field.kind != "choice":
            return WRONG
        taken = {
            value
            for alternative in entry.alternatives_for(target)
            for value in alternative.accepted(field.key, target)
        }
        return next((value for value, _ in field.options if value not in taken), None)

    def test_every_accepted_answer_is_correct_and_a_wrong_one_is_not(self):
        checked = 0
        for mode in MODES:
            context = self.context(mode)
            target = context["target"]
            for drill in REGISTRY.for_mode(mode):
                if drill.builtin:
                    continue
                spec = spec_for(drill)
                for item in spec.items(context):
                    entry = item.answers
                    for alternative in entry.alternatives_for(target):
                        good = {f.key: alternative.accepted(f.key, target)[0] for f in drill.fields}
                        with self.subTest(mode=mode, drill=drill.slug, item=item.key):
                            self.assertTrue(self.grade(spec, context, item, good))
                            for field in drill.fields:
                                for answer in alternative.accepted(field.key, target):
                                    post = {**good, field.key: answer}
                                    self.assertTrue(
                                        self.grade(spec, context, item, post), field.key
                                    )
                                wrong = self.wrong_value(field, entry, target)
                                if wrong is not None:
                                    post = {**good, field.key: wrong}
                                    self.assertFalse(
                                        self.grade(spec, context, item, post), field.key
                                    )
                            self.assertFalse(
                                self.grade(spec, context, item, {f.key: "" for f in drill.fields})
                            )
                    checked += 1
        self.assertGreater(checked, 0)


class RoundRunner(AuthedTestCase):
    """Runs a drill's three pages the way a browser would, for any registry."""

    def set_mode(self, mode):
        session = self.client.session
        session["language_mode"] = mode
        session.save()

    def run_round(self, mode, drill):
        """The correct and the wrong scenario, each in a round of its own.

        A drill may have one item or a round of one question, so a round is
        never assumed to hold a second question.
        """
        for correct in (True, False):
            with self.subTest(correct=correct):
                self.run_one_round(mode, drill, correct)

    def run_one_round(self, mode, drill, correct):
        start = reverse("reviews:drill_start", args=[drill.slug])
        step = reverse("reviews:drill", args=[drill.slug])
        key = f"{drill.trainer}_drill_session"

        self.assertRedirects(self.client.get(start), step, fetch_redirect_response=False)
        self.assertEqual(self.client.get(step).status_code, 200)
        session = self.client.session[key]
        current = session["queue"][session["index"]]
        if drill.builtin:
            card = Card.objects.get(pk=current)
            post = {"answer": card.article if correct else WRONG}
        else:
            target = get_target_language(mode)
            alternative = drill.item(current).alternatives_for(target)[0]
            post = {f.key: alternative.accepted(f.key, target)[0] for f in drill.fields}
            if not correct:
                post = {name: WRONG for name in post}
        self.assertEqual(self.client.post(step, post).status_code, 302)
        session = self.client.session[key]
        self.assertEqual((session["seen"], session["correct"]), (1, int(correct)))
        if len(session["queue"]) > session["index"]:
            feedback = self.client.get(step)
            self.assertEqual(feedback.status_code, 200)
            self.assertContains(feedback, "drill-feedback")
        session = self.client.session
        session[key]["index"] = len(session[key]["queue"])
        session.save()
        self.assertRedirects(
            self.client.get(step),
            reverse("reviews:drill_complete", args=[drill.slug]),
            fetch_redirect_response=False,
        )
        complete = self.client.get(reverse("reviews:drill_complete", args=[drill.slug]))
        self.assertContains(complete, "Round complete")
        self.assertContains(complete, start)
        self.assertEqual(spec_for(drill).trainer, drill.trainer)


class SyntheticPageContractTests(RoundRunner):
    """The same page contract on the made-up folder, with a one-question drill."""

    def test_a_round_runs_for_every_drill_of_the_synthetic_folder(self):
        synthetic = registry_from_directory(Path(__file__).parent / "testdata" / "languages")
        with mock.patch.object(drill_module, "REGISTRY", synthetic):
            ran = []
            for mode in MODES:
                self.set_mode(mode)
                for drill in synthetic.for_mode(mode):
                    with self.subTest(mode=mode, drill=drill.slug):
                        self.run_round(mode, drill)
                        ran.append(drill.slug)
        self.assertIn("one-question", ran)
        self.assertEqual(synthetic.by_slug("one-question").round_size, 1)
        self.assertEqual(len(synthetic.by_slug("one-question").items), 1)


class DrillPageContractTests(RoundRunner):
    """The drills index, the three drill pages and the flashcards in every mode."""

    def setUp(self):
        super().setUp()
        for mode in MODES:
            pack = pack_for_mode(mode)
            deck = Deck.objects.create(name=mode, language_mode=mode, owner=self.user)
            if pack.articles:
                for number, article in enumerate(pack.articles):
                    Card.objects.create(
                        deck=deck, front=f"{article} woord{number}", back="x", article=article
                    )

    def test_the_drills_index_lists_a_drill_exactly_in_the_modes_that_offer_it(self):
        for mode in MODES:
            self.set_mode(mode)
            page = self.client.get(reverse("reviews:drills"))
            self.assertEqual(page.status_code, 200)
            html = page.content.decode()
            for drill in every_drill():
                with self.subTest(mode=mode, drill=drill.slug):
                    start = f'href="{reverse("reviews:drill_start", args=[drill.slug])}"'
                    flashcards = (
                        f'href="{reverse("reviews:flashcards_start", args=[drill.trainer])}"'
                    )
                    self.assertEqual(start in html, offered(drill, mode))
                    self.assertEqual(flashcards in html, offered(drill, mode))
            self.assertEqual(
                [d["slug"] for d in available_drills(mode)],
                [d.slug for d in REGISTRY.for_mode(mode)],
            )

    def test_the_dashboard_and_stats_render_in_every_mode(self):
        for mode in MODES:
            self.set_mode(mode)
            for name in ("core:dashboard", "core:stats"):
                with self.subTest(mode=mode, page=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_the_drill_of_the_day_is_one_the_mode_offers(self):
        for mode in MODES:
            self.assertIn(drill_of_the_day(mode), [d.trainer for d in REGISTRY.for_mode(mode)])

    def test_start_step_feedback_and_completion_render_for_every_offered_drill(self):
        for mode in MODES:
            self.set_mode(mode)
            for drill in REGISTRY.for_mode(mode):
                with self.subTest(mode=mode, drill=drill.slug):
                    self.run_round(mode, drill)

    def test_a_drill_the_mode_does_not_offer_redirects_to_the_index(self):
        for mode in MODES:
            self.set_mode(mode)
            for drill in every_drill():
                if offered(drill, mode) or drill.builtin:
                    continue
                with self.subTest(mode=mode, drill=drill.slug):
                    for name in ("drill_start", "drill", "drill_complete"):
                        response = self.client.get(reverse(f"reviews:{name}", args=[drill.slug]))
                        self.assertRedirects(
                            response, reverse("reviews:drills"), fetch_redirect_response=False
                        )
                    response = self.client.get(
                        reverse("reviews:flashcards_start", args=[drill.trainer])
                    )
                    self.assertRedirects(
                        response, reverse("core:dashboard"), fetch_redirect_response=False
                    )

    def test_a_slug_in_no_pack_is_a_404(self):
        self.assertEqual(self.client.get("/reviews/drill/no-such-drill/start/").status_code, 404)

    def test_flashcards_start_and_step_for_every_offered_drill(self):
        for mode in MODES:
            self.set_mode(mode)
            target = get_target_language(mode)
            for drill in REGISTRY.for_mode(mode):
                with self.subTest(mode=mode, drill=drill.slug):
                    url = reverse("reviews:flashcards", args=[drill.trainer])
                    start = self.client.get(
                        reverse("reviews:flashcards_start", args=[drill.trainer])
                    )
                    self.assertRedirects(start, url, fetch_redirect_response=False)
                    queue = self.client.session["flashcards_session"]["queue"]
                    expected = (
                        Card.objects.filter(deck__language_mode=mode).exclude(article="").count()
                        if drill.builtin
                        else len(drill.items_for(target))
                    )
                    self.assertEqual(len(queue), expected)
                    self.assertEqual(self.client.get(url).status_code, 200)
                    self.client.post(url, {"action": "show_answer"})
                    revealed = self.client.get(url)
                    self.assertContains(revealed, "Answer")
                    self.client.post(url, {"action": "next"})
                    self.assertIn(self.client.get(url).status_code, (200, 302))
