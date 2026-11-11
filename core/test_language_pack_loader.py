import shutil
import tempfile
import textwrap
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.language_packs import PackError, load_packs

VALID = """
code: xx
name: Testish
articles: [a, b]
article_genders: [One, Two]
speech_locale: xx-XX
media:
  video_channel: some-channel
  podcast: true
"""


class PackLoaderTests(SimpleTestCase):
    def setUp(self):
        scratch = Path(settings.BASE_DIR) / "temp"
        scratch.mkdir(exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=scratch))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, code, text, folder=None):
        folder = self.root / (folder or code)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pack.yml").write_text(textwrap.dedent(text), encoding="utf-8")
        return folder / "pack.yml"

    def assert_fails(self, *fragments):
        with self.assertRaises(PackError) as caught:
            load_packs(self.root)
        for fragment in fragments:
            self.assertIn(fragment, str(caught.exception))

    def test_valid_pack_loads_every_attribute(self):
        self.write("xx", VALID)
        pack = load_packs(self.root)["xx"]
        self.assertEqual(pack.code, "xx")
        self.assertEqual(pack.name, "Testish")
        self.assertEqual(pack.articles, ("a", "b"))
        self.assertEqual(pack.article_genders, ("One", "Two"))
        self.assertEqual(pack.speech_locale, "xx-XX")
        self.assertEqual(pack.video_channel_key, "some-channel")
        self.assertEqual(pack.features, frozenset({"video", "podcast", "article_drill"}))

    def test_every_folder_is_loaded(self):
        self.write("xx", VALID)
        self.write("yy", VALID.replace("code: xx", "code: yy"))
        self.assertEqual(set(load_packs(self.root)), {"xx", "yy"})

    def test_features_are_derived(self):
        self.write(
            "xx",
            """
            code: xx
            name: Bare
            articles: []
            article_genders: []
            speech_locale: xx-XX
            media:
              video_channel: null
              podcast: false
            """,
        )
        pack = load_packs(self.root)["xx"]
        self.assertEqual(pack.features, frozenset())
        self.assertEqual(pack.video_channel_key, "")

    def test_missing_field_names_file_and_field(self):
        path = self.write("xx", VALID.replace("speech_locale: xx-XX\n", ""))
        self.assert_fails(str(path), "speech_locale", "missing")

    def test_missing_media_field_names_it(self):
        path = self.write("xx", VALID.replace("  podcast: true\n", ""))
        self.assert_fails(str(path), "media.podcast", "missing")

    def test_unknown_field_names_file_and_field(self):
        path = self.write("xx", VALID + "colour: blue\n")
        self.assert_fails(str(path), "colour", "not a known field")

    def test_wrong_kind_of_value_names_the_field(self):
        path = self.write("xx", VALID.replace("[a, b]", "der"))
        self.assert_fails(str(path), "articles", "list")
        path = self.write("xx", VALID.replace("podcast: true", "podcast: maybe"))
        self.assert_fails(str(path), "media.podcast", "true or false")

    def test_article_and_gender_counts_must_match(self):
        path = self.write("xx", VALID.replace("[One, Two]", "[One]"))
        self.assert_fails(str(path), "article_genders")

    def test_code_must_match_the_folder(self):
        path = self.write("xx", VALID, folder="zz")
        self.assert_fails(str(path), "code", "zz")

    def test_malformed_yaml_names_the_file(self):
        path = self.write("xx", "code: [unclosed\n")
        self.assert_fails(str(path), "YAML")

    def test_folder_without_pack_file_is_reported(self):
        (self.root / "xx").mkdir()
        self.assert_fails("pack.yml", "missing")

    def test_missing_languages_folder_is_reported(self):
        with self.assertRaises(PackError):
            load_packs(self.root / "nowhere")
