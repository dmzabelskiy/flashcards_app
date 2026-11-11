from pathlib import Path

from django.conf import settings
from django.urls import reverse

from cards.models import Card
from core.testutils import AuthedTestCase
from decks.models import Deck
from reviews.article_cards import ARTICLE_DRILL_TAG, article_seed_rows
from reviews.models import TrainerAttempt


class ArticleSeedTests(AuthedTestCase):
    def test_seed_contains_one_hundred_balanced_b1_b2_nouns(self):
        rows = article_seed_rows()
        counts = {article: 0 for article in ("der", "die", "das")}
        for row in rows:
            counts[row["front"].split()[0]] += 1

        self.assertEqual(len(rows), 100)
        self.assertEqual(counts, {"der": 34, "die": 33, "das": 33})

    def test_empty_article_start_populates_the_users_default_deck(self):
        response = self.client.get(reverse("reviews:drill_start", args=["articles"]))

        self.assertRedirects(response, reverse("reviews:drill", args=["articles"]))
        queue = self.client.session["article_drill_session"]["queue"]
        self.assertEqual(len(queue), 100)
        self.assertEqual(
            Card.objects.filter(deck__owner=self.user, tags=ARTICLE_DRILL_TAG).count(),
            100,
        )


class FlashcardFlowTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        self.deck = Deck.objects.create(
            name="German",
            language_mode=Deck.LanguageMode.DE_RU,
            owner=self.user,
        )
        self.card = Card.objects.create(
            deck=self.deck,
            front="der Tisch",
            back="стол",
            article="der",
            example_sentence="Der Tisch steht in der Küche.",
        )

    def test_initial_flashcard_contains_vertical_flip_hooks_and_hidden_answer_face(self):
        self.client.get(reverse("reviews:flashcards_start", args=["article"]))

        response = self.client.get(reverse("reviews:flashcards", args=["article"]))

        self.assertContains(response, 'class="flashcard-panel flashcard-3d"')
        self.assertContains(response, "data-flashcard-form")
        self.assertContains(response, "css/flashcards.css")
        self.assertContains(response, "js/flashcards.js")
        self.assertContains(
            response,
            'class="flashcard-face flashcard-face-front" data-flashcard-face="front" aria-hidden="false"',
        )
        self.assertContains(
            response,
            'class="flashcard-face flashcard-face-back" data-flashcard-face="back" aria-hidden="true" inert',
        )
        self.assertContains(response, 'value="show_answer"')
        self.assertNotContains(response, 'value="next"')
        self.assertContains(response, "data-flashcard-back")
        self.assertContains(response, 'value="back"')
        self.assertContains(response, 'aria-label="Back to previous card"')
        self.assertContains(response, 'aria-disabled="true"')

    def test_flashcard_styles_use_vertical_flip_and_fixed_reverse_surface(self):
        stylesheet = Path(settings.BASE_DIR, "static", "css", "flashcards.css").read_text()

        self.assertIn("perspective: 1200px", stylesheet)
        self.assertIn("transform-style: preserve-3d", stylesheet)
        self.assertIn("transition: transform 650ms ease", stylesheet)
        self.assertNotIn("transition: transform 350ms ease", stylesheet)
        self.assertIn(".flashcard-3d.is-returning .flashcard-3d-inner", stylesheet)
        self.assertIn("transition-duration: 350ms", stylesheet)
        self.assertIn("transform: rotateX(180deg)", stylesheet)
        self.assertIn("transform: rotateX(90deg)", stylesheet)
        self.assertIn("transition-duration: 325ms", stylesheet)
        self.assertIn(
            ".flashcard-face-back {\n  transform: rotateX(180deg);\n  background: var(--white);",
            stylesheet,
        )
        self.assertIn(".flashcard-back-button", stylesheet)
        self.assertIn("flex: 1 1 0", stylesheet)
        self.assertIn("@media (max-width: 420px)", stylesheet)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet)

    def test_flashcard_script_avoids_named_action_collision_and_delays_fallback(self):
        script = Path(settings.BASE_DIR, "static", "js", "flashcards.js").read_text()

        self.assertIn('form.getAttribute("action") || window.location.href', script)
        self.assertNotIn("fetch(form.action", script)
        self.assertIn("var flipDuration = 650", script)
        self.assertIn("var reverseFlipDuration = 350", script)
        self.assertIn("var remaining = prefersReducedMotion()", script)
        self.assertIn("window.setTimeout(nativeRevealSubmit, remaining)", script)
        self.assertIn('button.value === "next"', script)
        self.assertIn('card.classList.add("is-returning")', script)
        self.assertIn('card.classList.remove("is-revealed")', script)
        self.assertIn("window.setTimeout(nativeNextSubmit, delay)", script)
        self.assertIn("var disabled = isBusy || !available;", script)
        self.assertIn("var lastClickedButton = null", script)
        self.assertIn("event.submitter || lastClickedButton", script)
        self.assertIn("actionButtons[index].disabled = true", script)

    def test_flashcard_script_preloads_next_card_during_two_stage_reverse(self):
        script = Path(settings.BASE_DIR, "static", "js", "flashcards.js").read_text()

        self.assertIn("function nativeSubmit(actionName)", script)
        self.assertIn("action.value = actionName", script)
        self.assertIn('nativeSubmit("show_answer")', script)
        self.assertIn('nativeSubmit("next")', script)
        self.assertIn('card.classList.add("is-revealed")', script)
        self.assertIn("setFaceState(true)", script)
        self.assertIn('body.set("action", "show_answer")', script)
        self.assertIn('if (button.value === "next")', script)
        self.assertIn('if (card.dataset.revealed !== "true") return;', script)
        self.assertIn("var halfFlipDuration = 325", script)
        self.assertIn('card.classList.add("is-next-edge")', script)
        self.assertIn('card.classList.remove("is-revealed")', script)
        self.assertIn("window.setTimeout(onEdge, halfFlipDuration)", script)
        self.assertIn('body.set("action", "next")', script)
        self.assertIn("Promise.resolve(request)", script)
        self.assertIn('new DOMParser().parseFromString(html, "text/html")', script)
        self.assertIn("front.innerHTML = nextCard.front.innerHTML", script)
        self.assertIn("back.innerHTML = nextCard.back.innerHTML", script)
        self.assertIn("meta.innerHTML = nextCard.meta.innerHTML", script)
        self.assertIn("setShowAnswerButton(button, true)", script)
        self.assertIn("setFaceState(false)", script)
        self.assertIn('card.classList.add("is-next-complete")', script)
        self.assertIn("window.location.assign(nextResult.url)", script)
        self.assertIn("safeReload()", script)
        self.assertIn("window.DOMParser", script)
        self.assertIn("prefersReducedMotion()", script)
        self.assertIn("startReverseTransition(function ()", script)
        self.assertIn("scheduleNativeNextFallback()", script)
        self.assertIn("setFaceState(false)", script)
        self.assertIn('button.setAttribute("aria-busy", "true")', script)

    def test_flashcard_script_holds_edge_until_next_response_and_restores_focus(self):
        script = Path(settings.BASE_DIR, "static", "js", "flashcards.js").read_text()

        self.assertIn("var edgeReached = false", script)
        self.assertIn("if (!edgeReached || settledAtEdge) return", script)
        self.assertIn("if (!nextResult && !nextError) return", script)
        self.assertIn('card.classList.add("is-next-settling")', script)
        self.assertIn('card.classList.remove("is-next-edge")', script)
        self.assertIn("setShowAnswerButton(button, false)", script)
        self.assertIn("button.focus()", script)
        self.assertIn("button.disabled = isBusy", script)
        self.assertIn("window.location.reload()", script)

    def test_mobile_collapsed_shell_stays_full_width(self):
        stylesheet = Path(settings.BASE_DIR, "static", "css", "layout.css").read_text()

        self.assertIn(
            '.app-shell, .app-shell[data-collapsed="true"] { grid-template-columns: minmax(0, 1fr);',
            stylesheet,
        )
        self.assertIn(".app-main { min-width: 0; width: 100%; }", stylesheet)

    def test_revealed_flashcard_exposes_only_answer_face_and_keeps_context_header(self):
        url = reverse("reviews:flashcards", args=["article"])
        self.client.get(reverse("reviews:flashcards_start", args=["article"]))

        self.client.post(url, {"action": "show_answer"})
        response = self.client.get(url)

        self.assertTrue(self.client.session["flashcards_session"]["revealed"])
        self.assertContains(
            response,
            'class="flashcard-face flashcard-face-front" data-flashcard-face="front" aria-hidden="true" inert',
        )
        self.assertContains(
            response,
            'class="flashcard-face flashcard-face-back" data-flashcard-face="back" aria-hidden="false"',
        )
        self.assertContains(response, 'class="flashcard-back-question">Tisch</h2>')
        self.assertContains(response, "Answer")
        self.assertContains(response, 'value="back"')
        self.assertContains(response, 'value="next"')

    def test_back_action_is_shared_across_trainers_and_keeps_previous_answer_revealed(self):
        trainers = (
            ("article", {}),
            ("adjective_preposition", {}),
            ("verb_preposition", {}),
            ("irregular_verb", {}),
        )
        Card.objects.create(
            deck=self.deck,
            front="die Lampe",
            back="лампа",
            article="die",
        )

        for trainer, params in trainers:
            with self.subTest(trainer=trainer):
                start_url = reverse("reviews:flashcards_start", args=[trainer])
                flashcards_url = reverse("reviews:flashcards", args=[trainer])
                self.client.get(start_url, params)
                session = self.client.session
                flashcard_session = session["flashcards_session"]
                self.assertGreaterEqual(len(flashcard_session["queue"]), 2)
                flashcard_session["index"] = 1
                flashcard_session["revealed"] = False
                session.save()

                queue_before = list(flashcard_session["queue"])
                question = self.client.get(flashcards_url)
                self.assertContains(question, 'data-back-available="true"')
                self.assertNotContains(question, 'aria-disabled="true"')

                moved_back_from_question = self.client.post(
                    flashcards_url,
                    {"action": "back"},
                )
                self.assertRedirects(moved_back_from_question, flashcards_url)
                previous = self.client.session["flashcards_session"]
                self.assertEqual(previous["index"], 0)
                self.assertTrue(previous["revealed"])
                self.assertEqual(previous["queue"], queue_before)
                previous_answer = self.client.get(flashcards_url)
                self.assertContains(previous_answer, 'aria-hidden="false"')
                self.assertContains(previous_answer, 'value="next"')
                self.assertContains(previous_answer, 'aria-disabled="true"')

                session = self.client.session
                flashcard_session = session["flashcards_session"]
                flashcard_session["revealed"] = True
                session.save()

                revealed = self.client.get(flashcards_url)
                self.assertContains(revealed, 'value="back"')
                self.assertContains(revealed, 'value="next"')
                self.assertNotContains(
                    revealed,
                    'value="back" type="submit" aria-label="Back to previous card" disabled',
                )

                moved_back = self.client.post(flashcards_url, {"action": "back"})
                self.assertRedirects(moved_back, flashcards_url)
                updated = self.client.session["flashcards_session"]
                self.assertEqual(updated["index"], 0)
                self.assertTrue(updated["revealed"])
                self.assertEqual(updated["queue"], queue_before)
                previous = self.client.get(flashcards_url)
                self.assertContains(previous, 'aria-hidden="false"')
                self.assertContains(previous, 'value="back"')
                self.assertContains(previous, 'value="next"')
                self.assertContains(previous, 'aria-disabled="true"')

        self.assertEqual(TrainerAttempt.objects.count(), 0)

    def test_back_action_stays_at_first_card_and_does_not_reveal_or_track(self):
        flashcards_url = reverse("reviews:flashcards", args=["article"])
        self.client.get(reverse("reviews:flashcards_start", args=["article"]))

        before = self.client.session["flashcards_session"].copy()
        response = self.client.post(flashcards_url, {"action": "back"})

        self.assertRedirects(response, flashcards_url)
        after = self.client.session["flashcards_session"]
        self.assertEqual(after["index"], 0)
        self.assertFalse(after["revealed"])
        self.assertEqual(after["queue"], before["queue"])
        self.assertEqual(TrainerAttempt.objects.count(), 0)
        boundary_response = self.client.get(flashcards_url)
        self.assertContains(boundary_response, 'value="back"')
        self.assertContains(boundary_response, 'aria-disabled="true"')

        self.client.post(flashcards_url, {"action": "show_answer"})
        self.client.post(flashcards_url, {"action": "back"})
        boundary = self.client.session["flashcards_session"]
        self.assertEqual(boundary["index"], 0)
        self.assertTrue(boundary["revealed"])
        self.assertEqual(TrainerAttempt.objects.count(), 0)

    def test_non_javascript_post_reveal_and_next_remain_progressive_and_untracked(self):
        url = reverse("reviews:flashcards", args=["article"])
        self.client.get(reverse("reviews:flashcards_start", args=["article"]))

        reveal = self.client.post(url, {"action": "show_answer"})
        self.assertRedirects(reveal, url)
        self.assertTrue(self.client.session["flashcards_session"]["revealed"])

        advance = self.client.post(url, {"action": "next"})
        self.assertEqual(advance.status_code, 302)
        self.assertEqual(advance["Location"], url)
        complete = self.client.get(url)
        self.assertRedirects(complete, reverse("reviews:flashcards_complete", args=["article"]))
        self.assertFalse(TrainerAttempt.objects.exists())

    def test_flashcard_reveal_and_next_do_not_create_tracking_records(self):
        start = self.client.get(reverse("reviews:flashcards_start", args=["article"]))
        self.assertRedirects(start, reverse("reviews:flashcards", args=["article"]))
        self.assertEqual(self.client.session["flashcards_session"]["queue"], [self.card.pk])

        question = self.client.get(reverse("reviews:flashcards", args=["article"]))
        self.assertContains(question, "Tisch")
        self.assertContains(question, "Show answer")
        self.assertContains(question, 'data-flashcard-face="back" aria-hidden="true" inert')

        self.client.post(
            reverse("reviews:flashcards", args=["article"]),
            {"action": "show_answer"},
        )
        answer = self.client.get(reverse("reviews:flashcards", args=["article"]))
        self.assertContains(answer, "der Tisch")
        self.assertContains(answer, "Next card")

        self.client.post(
            reverse("reviews:flashcards", args=["article"]),
            {"action": "next"},
        )
        complete = self.client.get(reverse("reviews:flashcards_complete", args=["article"]))
        self.assertContains(complete, "1 cards reviewed")
        self.assertContains(complete, "Nothing was scored or saved")
        self.assertFalse(TrainerAttempt.objects.exists())

    def _save_retired_trainer_session(self, index, queue):
        session = self.client.session
        session["flashcards_session"] = {
            "trainer": "deutsch_online",
            "language_mode": "DE-RU",
            "queue": queue,
            "index": index,
            "revealed": False,
        }
        session.save()

    def test_saved_session_for_a_retired_trainer_is_discarded_mid_queue(self):
        self._save_retired_trainer_session(index=1, queue=["set-2:aufpreis", "set-1:bewerten"])

        response = self.client.get(reverse("reviews:flashcards", args=["deutsch_online"]))

        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertNotIn("flashcards_session", self.client.session)

    def test_saved_session_for_a_retired_trainer_is_discarded_when_queue_is_exhausted(self):
        self._save_retired_trainer_session(index=2, queue=["set-2:aufpreis", "set-1:bewerten"])

        response = self.client.get(reverse("reviews:flashcards_complete", args=["deutsch_online"]))

        self.assertRedirects(response, reverse("core:dashboard"))
        self.assertNotIn("flashcards_session", self.client.session)

    def test_long_front_on_an_article_card_uses_balanced_smaller_type(self):
        long_card = Card.objects.create(
            deck=self.deck,
            front="die Donaudampfschifffahrtsgesellschaft",
            back="пароходная компания",
            article="die",
        )
        self.client.get(reverse("reviews:flashcards_start", args=["article"]))
        session = self.client.session
        session["flashcards_session"]["queue"] = [long_card.pk]
        session["flashcards_session"]["index"] = 0
        session["flashcards_session"]["revealed"] = False
        session.save()

        response = self.client.get(reverse("reviews:flashcards", args=["article"]))

        self.assertContains(response, "Donaudampfschifffahrtsgesellschaft")
        self.assertContains(
            response,
            'class="flashcard-question flashcard-question--long"',
        )
        stylesheet = Path(settings.BASE_DIR, "static", "css", "flashcards.css").read_text()
        self.assertIn("text-wrap: balance", stylesheet)
        self.assertIn(".flashcard-face-front .flashcard-question--long", stylesheet)
        self.assertIn("font-size: clamp(1.875rem, 5.2vw, 3.4rem)", stylesheet)
