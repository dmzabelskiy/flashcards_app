"""A sentence-length drill prompt gets a smaller display style than a one-word prompt."""

import shutil
import tempfile
import textwrap
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.urls import reverse

from core.testutils import AuthedTestCase
from reviews import drills
from reviews.drill_specs import ROUND_FORMAT
from reviews.drills import registry_from_directory

PACK = """
code: de
name: Testland
articles: []
article_genders: []
speech_locale: es-ES
media: {video_channel: null, podcast: false}
"""

DRILL = """
slug: prompts
trainer: synth_prompts
label: Prompts
description: Prompts of every length.
round_size: 3
targets: [ru]
fields:
  - {key: answer, label: Answer, kind: typed, normalise: exact}
items:
  - id: short
    prompt: ir
    alternatives:
      - answer: [voy]
  - id: german-longest
    prompt: sich beschäftigen
    alternatives:
      - answer: [x]
  - id: form
    prompt: hablar · indefinido · vosotros
    alternatives:
      - answer: [hablasteis]
        reveal: {Translation: говорить}
  - id: sentence
    prompt: Mi hermana y yo vamos a la playa con nuestros amigos todos los ___.
    alternatives:
      - answer: [veranos]
        example: Mi hermana y yo vamos a la playa con nuestros amigos todos los veranos.
        reveal:
          Translation: Мы с сестрой ездим на пляж с нашими друзьями каждое лето.
          Why: повторяющееся действие → множественное число
"""

LONG = "drill-prompt-long"
LONG_FEEDBACK = "drill-feedback-long"


class LongPromptTests(AuthedTestCase):
    def setUp(self):
        super().setUp()
        scratch = Path(settings.BASE_DIR) / "temp"
        scratch.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(dir=scratch))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "de" / "drills").mkdir(parents=True)
        (root / "de" / "pack.yml").write_text(textwrap.dedent(PACK), encoding="utf-8")
        (root / "de" / "drills" / "prompts.yml").write_text(DRILL, encoding="utf-8")
        patcher = mock.patch.object(drills, "REGISTRY", registry_from_directory(root))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.url = reverse("reviews:drill", args=["prompts"])

    def page_for(self, *queue):
        session = self.client.session
        session["synth_prompts_drill_session"] = {
            "queue": list(queue),
            "index": 0,
            "seen": 0,
            "correct": 0,
            "last": None,
            "pending_correction": None,
            "pending_retirement": None,
            "round_format": ROUND_FORMAT,
        }
        session.save()
        return self.client.get(self.url)

    def test_a_sentence_prompt_renders_with_the_long_class(self):
        page = self.page_for("sentence")

        self.assertContains(page, LONG)
        self.assertContains(page, "Mi hermana y yo vamos a la playa")

    def test_a_verb_form_prompt_renders_with_the_long_class(self):
        self.assertContains(self.page_for("form"), LONG)

    def test_a_one_word_prompt_renders_without_it(self):
        self.assertNotContains(self.page_for("short"), LONG)

    def test_the_longest_german_prompt_renders_as_before(self):
        self.assertNotContains(self.page_for("german-longest"), LONG)

    def test_the_feedback_of_a_sentence_gets_the_long_class_and_keeps_its_reveals(self):
        self.page_for("sentence", "short")
        self.client.post(self.url, {"answer": "verano"})

        page = self.client.get(self.url)

        self.assertContains(page, LONG_FEEDBACK)
        self.assertContains(page, "Translation: Мы с сестрой ездим на пляж")
        self.assertContains(page, "Why: повторяющееся действие")
        self.assertContains(page, "Mi hermana y yo vamos a la playa con nuestros amigos")

    def test_the_feedback_of_a_one_word_prompt_has_no_long_class(self):
        self.page_for("short", "sentence")
        self.client.post(self.url, {"answer": "va"})

        self.assertNotContains(self.client.get(self.url), LONG_FEEDBACK)
