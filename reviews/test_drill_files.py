"""Reading drill files: a malformed one fails at load and names the file and field."""

import shutil
import tempfile
import textwrap
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.language_packs import load_packs
from reviews.drills import DrillError, load_drill, load_registry

VALID = """
slug: demo
trainer: demo_trainer
label: Demo
description: A demo drill.
round_size: 2
targets: [ru]
fields:
  - {key: word, label: Word, kind: typed, normalise: exact}
  - key: mark
    label: Mark
    kind: choice
    options: [{value: A, label: Alpha}, {value: B, label: Beta}]
  - {key: tag, label: Tag, kind: pool_choice}
items:
  - id: one
    prompt: One
    alternatives:
      - word: [uno]
        mark: [A]
        tag: [t]
        example: Un ejemplo.
        reveal: {Note: shown}
"""

PACK = """
code: {code}
name: Testish
articles: []
article_genders: []
speech_locale: xx-XX
media: {{video_channel: null, podcast: false}}
"""


class DrillFileTests(SimpleTestCase):
    def setUp(self):
        scratch = Path(settings.BASE_DIR) / "temp"
        scratch.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=scratch))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, text, name="demo.yml", pack="xx"):
        folder = self.root / pack / "drills"
        folder.mkdir(parents=True, exist_ok=True)
        (self.root / pack / "pack.yml").write_text(PACK.format(code=pack), encoding="utf-8")
        path = folder / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    def fails(self, text, *fragments):
        path = self.write(text)
        with self.assertRaises(DrillError) as caught:
            load_drill(path, "xx")
        self.assertIn(str(path), str(caught.exception))
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))

    def test_a_valid_file_loads_every_attribute(self):
        drill = load_drill(self.write(VALID), "xx")

        self.assertEqual((drill.slug, drill.trainer, drill.label), ("demo", "demo_trainer", "Demo"))
        self.assertEqual((drill.round_size, drill.targets, drill.order), (2, ("ru",), 100))
        self.assertEqual([f.kind for f in drill.fields], ["typed", "choice", "pool_choice"])
        self.assertEqual(drill.field("mark").options, (("A", "Alpha"), ("B", "Beta")))
        self.assertEqual(drill.field("tag").distractors, 2)
        self.assertEqual(drill.field("tag").normalise, "exact")
        alternative = drill.item("one").alternatives[0]
        self.assertEqual(alternative.accepted("word", "ru"), ("uno",))
        self.assertEqual(alternative.example, "Un ejemplo.")
        self.assertEqual(alternative.reveal, (("Note", "shown"),))

    def test_malformed_files_name_the_file_and_the_field(self):
        cases = {
            "unknown top-level field": (VALID + "colour: red\n", "colour"),
            "missing slug": (VALID.replace("slug: demo\n", ""), "slug"),
            "bad slug": (VALID.replace("slug: demo", "slug: Not A Slug"), "slug"),
            "the built-in slug": (VALID.replace("slug: demo", "slug: articles"), "slug"),
            "reserved trainer": (VALID.replace("demo_trainer", "video"), "trainer"),
            "long trainer": (VALID.replace("demo_trainer", "t" * 33), "trainer"),
            "zero round size": (VALID.replace("round_size: 2", "round_size: 0"), "round_size"),
            "targets not a list": (VALID.replace("[ru]", "ru"), "targets"),
            "unknown kind": (VALID.replace("kind: typed", "kind: voice"), "fields[0].kind"),
            "unknown normaliser": (
                VALID.replace("normalise: exact", "normalise: loud"),
                "fields[0].normalise",
            ),
            "typed without normaliser": (
                VALID.replace(", normalise: exact", ""),
                "fields[0].normalise",
            ),
            "duplicate field key": (VALID.replace("key: mark", "key: word"), "fields"),
            "choice without options": (
                VALID.replace(
                    "    options: [{value: A, label: Alpha}, {value: B, label: Beta}]\n", ""
                ),
                "fields[1].options",
            ),
            "options on a typed field": (
                VALID.replace("kind: typed", "kind: typed, options: [a, b]"),
                "fields[0].options",
            ),
            "distractors on a typed field": (
                VALID.replace("kind: typed", "kind: typed, distractors: 2"),
                "fields[0].distractors",
            ),
            "answer that is no option": (
                VALID.replace("mark: [A]", "mark: [C]"),
                "items[0].alternatives[0].mark",
            ),
            "alternative missing a field": (
                VALID.replace("        tag: [t]\n", ""),
                "alternatives[0].tag",
            ),
            "alternative with an unknown key": (
                VALID.replace("tag: [t]", "tag: [t]\n        colour: [red]"),
                "alternatives[0].colour",
            ),
            "answer that is not text": (
                VALID.replace("word: [uno]", "word: [no, 5]"),
                "items[0].alternatives[0].word",
            ),
            "yaml booleans are not strings": (
                VALID.replace("word: [uno]", "word: [no]"),
                "alternatives[0].word",
            ),
            "empty answer list": (VALID.replace("word: [uno]", "word: []"), "alternatives[0].word"),
            "empty target map": (VALID.replace("word: [uno]", "word: {}"), "alternatives[0].word"),
            "numeric id": (VALID.replace("id: one", "id: 1"), "items[0].id"),
            "no alternatives": (
                VALID.replace(
                    "    alternatives:\n      - word: [uno]\n        mark: [A]\n        tag: [t]\n        example: Un ejemplo.\n        reveal: {Note: shown}\n",
                    "    alternatives: []\n",
                ),
                "items[0].alternatives",
            ),
            "no items": (VALID[: VALID.index("items:")] + "items: []\n", "items"),
            "reveal not a map": (
                VALID.replace("reveal: {Note: shown}", "reveal: [Note]"),
                "reveal",
            ),
        }
        for name, (text, fragment) in cases.items():
            with self.subTest(name):
                self.fails(text, fragment)

    def test_a_repeated_item_id_is_named(self):
        item = VALID[VALID.index("  - id: one") :]
        self.fails(VALID + item, "items[1].id", "one")

    def test_a_file_that_is_not_yaml_or_not_a_mapping_is_named(self):
        self.fails("slug: [unclosed", "YAML")
        self.fails("- just\n- a list\n", "mapping")

    def test_per_target_answers_may_differ_per_alternative_field(self):
        text = VALID.replace("word: [uno]", "word: {ru: [один], es: [uno]}")
        drill = load_drill(self.write(text), "xx")

        alternative = drill.item("one").alternatives[0]
        self.assertEqual(alternative.accepted("word", "ru"), ("один",))
        self.assertEqual(alternative.accepted("word", "es"), ("uno",))
        self.assertIsNone(alternative.accepted("word", "en"))
        self.assertEqual(drill.item("one").alternatives_for("en"), ())

    def test_the_registry_orders_a_pack_by_order_then_file_name(self):
        self.write(
            VALID.replace("slug: demo", "slug: second").replace("demo_trainer", "t_two"), "a.yml"
        )
        self.write(
            VALID.replace("slug: demo", "slug: first").replace("demo_trainer", "t_one")
            + "order: 5\n",
            "b.yml",
        )

        registry = load_registry(self.root, load_packs(self.root))

        self.assertEqual([d.slug for d in registry.drills_by_pack["xx"]], ["first", "second"])

    def test_a_slug_or_trainer_may_not_be_shared_between_packs(self):
        self.write(VALID, pack="xx")
        self.write(VALID, pack="yy")

        with self.assertRaises(DrillError) as caught:
            load_registry(self.root, load_packs(self.root))

        self.assertIn("slug 'demo' is also used by", str(caught.exception))

        self.write(VALID.replace("slug: demo", "slug: other"), pack="yy")
        (self.root / "yy" / "drills" / "demo.yml").unlink()
        self.write(VALID.replace("slug: demo", "slug: other"), name="other.yml", pack="yy")
        with self.assertRaises(DrillError) as caught:
            load_registry(self.root, load_packs(self.root))
        self.assertIn("trainer 'demo_trainer' is also used by", str(caught.exception))
