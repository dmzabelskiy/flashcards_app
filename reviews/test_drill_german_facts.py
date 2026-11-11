"""What is true of the German drill files: the stored ids, the matching rules.

The id fingerprints guard stored progress: ``TrainerAttempt`` and
``DrillItemProgress`` keep these ids, so a drill file may add items but must
not rename or drop one without a migration of the history.
"""

import hashlib

from django.test import SimpleTestCase
from django.urls import reverse

from cards.models import Card
from core.language_packs import GERMAN
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.drill_specs import spec_for
from reviews.drills import REGISTRY
from reviews.normalisers import (
    NORMALISERS,
    matches,
    normalise_exact,
    normalise_german,
    normalise_preposition,
    normalise_russian,
    normalise_translation,
)


def fingerprint(drill):
    ids = "\n".join(sorted(item.id for item in drill.items))
    return hashlib.sha1(ids.encode()).hexdigest()[:12]


class GermanDrillIdTests(SimpleTestCase):
    KNOWN = {
        "adjective-prepositions": ("adjective_preposition", 75, "dc1c409e752d"),
        "verb-prepositions": ("verb_preposition", 35, "6dc3e993dc20"),
        "irregular-verbs": ("irregular_verb", 217, "78c659291759"),
    }

    def test_the_item_ids_are_the_ones_stored_in_progress_and_history(self):
        for slug, (trainer, count, expected) in self.KNOWN.items():
            with self.subTest(drill=slug):
                drill = REGISTRY.by_slug(slug)
                self.assertEqual(drill.trainer, trainer)
                self.assertEqual(drill.pack, "de")
                self.assertEqual(len(drill.items), count)
                self.assertEqual(fingerprint(drill), expected)

    def test_the_german_pack_offers_its_known_drills_in_this_order(self):
        # More drills may be added; these four keep their relative order.
        known = ["articles", "adjective-prepositions", "verb-prepositions", "irregular-verbs"]
        slugs = [d.slug for d in REGISTRY.drills_by_pack["de"]]

        self.assertEqual([slug for slug in slugs if slug in known], known)
        self.assertEqual(GERMAN.articles, ("der", "die", "das"))

    def test_irregular_verbs_have_russian_only_and_so_are_not_offered_in_spanish(self):
        drill = REGISTRY.by_slug("irregular-verbs")
        self.assertEqual(drill.targets, ("ru",))
        self.assertIn(drill, REGISTRY.for_mode("DE-RU"))
        self.assertNotIn(drill, REGISTRY.for_mode("DE-ES"))
        self.assertNotIn(drill, REGISTRY.for_mode("NL-EN"))

    def test_alternate_forms_and_corrected_typos_of_the_irregular_verbs_are_kept(self):
        drill = REGISTRY.by_slug("irregular-verbs")

        def forms(item_id, field):
            return drill.item(item_id).alternatives[0].accepted(field, "ru")

        self.assertEqual(forms("backen", "preterite"), ("buk", "backte"))
        self.assertEqual(forms("senden", "perfect"), ("hat gesandt", "hat gesendet"))
        self.assertEqual(forms("geschehen", "preterite"), ("geschah",))
        self.assertEqual(forms("tun", "perfect"), ("hat getan",))
        self.assertEqual(forms("verbringen", "perfect"), ("hat verbracht",))

    def test_verb_prepositions_have_russian_and_spanish_and_ten_prepositions(self):
        drill = REGISTRY.by_slug("verb-prepositions")
        for target in ("ru", "es"):
            self.assertEqual(len(drill.items_for(target)), 35)
        self.assertEqual(len(drill.pool("preposition", "ru")), 10)
        self.assertEqual(drill.pool("preposition", "ru"), drill.pool("preposition", "es"))
        self.assertEqual(drill.field("preposition").kind, "pool_choice")
        self.assertEqual(drill.field("preposition").distractors, 2)

    def test_adjective_items_list_their_own_pair_first_then_their_siblings(self):
        drill = REGISTRY.by_slug("adjective-prepositions")

        def pairs(item_id):
            return [
                (a.accepted("preposition", "ru")[0], a.accepted("case", "ru")[0])
                for a in drill.item(item_id).alternatives
            ]

        self.assertEqual(pairs("aergerlich-auf"), [("auf", "A"), ("über", "A")])
        self.assertEqual(pairs("aergerlich-ueber"), [("über", "A"), ("auf", "A")])
        self.assertEqual(pairs("zufrieden-mit"), [("mit", "D")])
        siblings = [i for i in drill.items if len(i.alternatives) > 1]
        self.assertEqual(len(siblings), 10)


class GermanNormaliserTests(SimpleTestCase):
    def test_german_accepts_ascii_umlauts_case_and_parentheses(self):
        self.assertEqual(normalise_german("  Fährt (AB) "), normalise_german("faehrt ab"))
        self.assertEqual(normalise_german("schloss"), normalise_german("Schloß"))
        self.assertNotEqual(normalise_german("fährt"), normalise_german("fahrt"))

    def test_russian_ignores_yo_case_and_trailing_punctuation(self):
        self.assertEqual(normalise_russian(" Всё. "), normalise_russian("все"))
        self.assertNotEqual(normalise_russian("мыть"), normalise_russian("мыть сь"))

    def test_translation_ignores_accents_and_punctuation_anywhere(self):
        self.assertEqual(
            normalise_translation("Dar las gracias!"), normalise_translation("dar las gracias")
        )
        self.assertEqual(normalise_translation("participár"), normalise_translation("participar"))
        self.assertEqual(
            normalise_translation("подавать, заявление"),
            normalise_translation("подавать заявление"),
        )

    def test_preposition_accepts_the_ascii_spelling_of_umlauts(self):
        for typed in ("über", "ueber", "UEBER", " uber "):
            self.assertEqual(normalise_preposition(typed), normalise_preposition("über"), typed)
        self.assertNotEqual(normalise_preposition("an"), normalise_preposition("auf"))

    def test_exact_is_strip_plus_casefold(self):
        self.assertEqual(normalise_exact("  Auf "), "auf")
        self.assertNotEqual(normalise_exact("über"), normalise_exact("ueber"))

    def test_every_name_a_drill_file_may_use_is_registered(self):
        self.assertEqual(
            set(NORMALISERS),
            {"german", "russian", "translation", "preposition", "spanish", "exact"},
        )

    def test_an_empty_answer_never_matches(self):
        for normaliser in NORMALISERS.values():
            self.assertFalse(matches("", ["a", ""], normaliser))
            self.assertFalse(matches("  ", ["a"], normaliser))


class GermanAnswerTests(AuthedTestCase):
    """The German rules that the contract cannot know, through the real pages."""

    def setUp(self):
        super().setUp()
        self.context = {
            "user": self.user,
            "language_mode": "DE-RU",
            "target": "ru",
            "target_label": "Russian",
        }

    def grade(self, slug, item_id, post, session=None):
        spec = spec_for(REGISTRY.by_slug(slug))
        item = spec.item_for(item_id, self.context)
        return spec.check(item, post, session or {}, self.context)

    def test_an_adjective_with_two_pairs_accepts_either_pair_but_not_a_mix(self):
        for preposition in ("auf", "über", "ueber"):
            self.assertTrue(
                self.grade(
                    "adjective-prepositions",
                    "aergerlich-auf",
                    {"preposition": preposition, "case": "A"},
                ).is_correct
            )
        wrong = self.grade(
            "adjective-prepositions", "aergerlich-auf", {"preposition": "über", "case": "D"}
        )
        self.assertFalse(wrong.is_correct)
        self.assertEqual(wrong.feedback["accepted"], ["auf · Akkusativ", "über · Akkusativ"])

    def test_adjective_feedback_shows_the_example_of_the_matched_pair(self):
        result = self.grade(
            "adjective-prepositions", "aergerlich-auf", {"preposition": "über", "case": "A"}
        )

        self.assertEqual(
            result.feedback["example"], "Andi war ärgerlich über seine schlechte Leistung."
        )

    def test_the_perfect_form_needs_its_auxiliary(self):
        base = {"present": "fährt ab", "preterite": "fuhr ab", "translation": "уезжать"}

        right = self.grade("irregular-verbs", "abfahren", {**base, "perfect": "ist abgefahren"})
        bare = self.grade("irregular-verbs", "abfahren", {**base, "perfect": "abgefahren"})

        self.assertTrue(right.is_correct)
        self.assertFalse(bare.is_correct)
        self.assertEqual([r["correct"] for r in bare.feedback["rows"]], [True, True, False, True])

    def test_verb_preposition_translations_follow_the_mode_and_show_the_case(self):
        session = {"choices": {"teilnehmen-an": {"preposition": ["an", "auf", "bei"]}}}
        post = {"translation": "участвовать", "preposition": "an"}

        ru = self.grade("verb-prepositions", "teilnehmen-an", post, session)
        self.context.update(language_mode="DE-ES", target="es", target_label="Spanish")
        es = self.grade("verb-prepositions", "teilnehmen-an", post, session)

        self.assertTrue(ru.is_correct)
        self.assertFalse(es.is_correct)
        self.assertEqual(ru.feedback["reveal"], [["Kasus", "Dativ"]])
        self.assertEqual(es.feedback["rows"][0]["label"], "Spanish")

    def test_every_verb_with_a_preposition_reveals_its_case(self):
        drill = REGISTRY.by_slug("verb-prepositions")
        cases = {}
        for item in drill.items:
            for alternative in item.alternatives:
                with self.subTest(item=item.id):
                    shown = dict(alternative.reveal)
                    self.assertIn(shown.get("Kasus"), ("Akkusativ", "Dativ"))
                    cases.setdefault(shown["Kasus"], item.id)

        self.assertEqual(set(cases), {"Akkusativ", "Dativ"})

    def test_the_case_of_a_verb_is_shown_in_the_generic_feedback(self):
        drill = REGISTRY.by_slug("verb-prepositions")
        first = {}
        for item in drill.items:
            first.setdefault(dict(item.alternatives[0].reveal)["Kasus"], item.id)
        spec = spec_for(drill)
        key = "verb_preposition_drill_session"
        step = reverse("reviews:drill", args=["verb-prepositions"])

        for case, item_id in first.items():
            with self.subTest(case=case, item=item_id):
                self.client.get(reverse("reviews:drill_start", args=["verb-prepositions"]))
                prepared = {}
                spec.prepare(prepared, [spec._item(drill.item(item_id))], self.context)
                session = self.client.session
                session[key].update(queue=[item_id, item_id], index=0, **prepared)
                session.save()

                self.client.post(step, {"translation": "zzz", "preposition": "zzz"})
                page = self.client.get(step)

                self.assertContains(page, f"Kasus: {case}")


class OldSessionShapeTests(AuthedTestCase):
    """What an old round never does: touch the article drill or a drill the mode lacks.

    The old data-drill rounds themselves are dropped; that is in
    ``test_drill_old_rounds.py``.
    """

    def _write(self, key, payload):
        session = self.client.session
        session[key] = payload
        session.save()

    def _round(self, *queue, **extra):
        return {"queue": list(queue), "index": 0, "seen": 0, "correct": 0, "last": None, **extra}

    def test_an_old_round_for_a_drill_the_mode_no_longer_offers_is_left_alone(self):
        self._write("irregular_verb_drill_session", self._round("abfahren"))
        session = self.client.session
        session["language_mode"] = Deck.LanguageMode.DE_ES
        session.save()

        response = self.client.get(reverse("reviews:drill", args=["irregular-verbs"]))

        self.assertRedirects(response, reverse("reviews:drills"), fetch_redirect_response=False)

    def test_an_old_article_round_still_resumes(self):
        deck = Deck.objects.create(name="RU", language_mode="DE-RU", owner=self.user)
        card = Card.objects.create(deck=deck, front="der Tisch", back="стол", article="der")
        self._write("article_drill_session", self._round(card.pk))

        page = self.client.get(reverse("reviews:drill", args=["articles"]))

        self.assertEqual(page.status_code, 200)
