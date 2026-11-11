import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from video_study import metadata
from video_study.metadata import lemmatize_german

TEMP_DIR = Path(settings.BASE_DIR) / "temp"


class BuildLemmaCacheTests(SimpleTestCase):
    def test_build_creates_the_trie_cache_file(self):
        with tempfile.TemporaryDirectory(dir="temp") as cache_dir:
            with override_settings(LEMMA_CACHE_DIR=cache_dir):
                call_command("build_lemma_cache")

            self.assertTrue((Path(cache_dir) / "de.dic").exists())

    def test_check_passes_once_the_cache_exists(self):
        with tempfile.TemporaryDirectory(dir="temp") as cache_dir:
            with override_settings(LEMMA_CACHE_DIR=cache_dir):
                call_command("build_lemma_cache")
                call_command("build_lemma_cache", "--check")

    def test_check_fails_on_an_empty_directory(self):
        with tempfile.TemporaryDirectory(dir="temp") as cache_dir:
            with override_settings(LEMMA_CACHE_DIR=cache_dir):
                with self.assertRaises(CommandError):
                    call_command("build_lemma_cache", "--check")

    def test_build_creates_the_dutch_cache_and_check_verifies_it(self):
        with (
            tempfile.TemporaryDirectory(dir="temp") as cache_dir,
            override_settings(LEMMA_CACHE_DIR=cache_dir),
        ):
            call_command("build_lemma_cache", stdout=StringIO())
            self.assertTrue((Path(cache_dir) / "nl.dic").exists())
            call_command("build_lemma_cache", "--check", stdout=StringIO())

    def test_check_fails_when_only_the_dutch_cache_is_missing(self):
        with (
            tempfile.TemporaryDirectory(dir="temp") as cache_dir,
            override_settings(LEMMA_CACHE_DIR=cache_dir),
        ):
            call_command("build_lemma_cache", stdout=StringIO())
            (Path(cache_dir) / "nl.dic").unlink()
            with self.assertRaises(CommandError):
                call_command("build_lemma_cache", "--check", stdout=StringIO())

    def test_build_creates_the_spanish_cache_and_check_verifies_it(self):
        with (
            tempfile.TemporaryDirectory(dir="temp") as cache_dir,
            override_settings(LEMMA_CACHE_DIR=cache_dir),
        ):
            call_command("build_lemma_cache", stdout=StringIO())
            self.assertTrue((Path(cache_dir) / "es.dic").exists())
            call_command("build_lemma_cache", "--check", stdout=StringIO())

    def test_check_fails_when_only_the_spanish_cache_is_missing(self):
        with (
            tempfile.TemporaryDirectory(dir="temp") as cache_dir,
            override_settings(LEMMA_CACHE_DIR=cache_dir),
        ):
            call_command("build_lemma_cache", stdout=StringIO())
            (Path(cache_dir) / "es.dic").unlink()
            with self.assertRaises(CommandError):
                call_command("build_lemma_cache", "--check", stdout=StringIO())


class LemmaCacheFailClosedTests(SimpleTestCase):
    """`get_lemmatizer()` must never build the trie in-process at runtime
    when `LEMMA_CACHE_BUILD_ALLOWED` is false: a missing or corrupt cache
    file should degrade `lemmatize_german` to returning the token
    unchanged, not crash or build."""

    def setUp(self):
        super().setUp()
        self.cache_dir = TEMP_DIR / f"lemma_cache_test_{self._testMethodName}"
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
        self.cache_dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.cache_dir, ignore_errors=True)
        self._reset_lemmatizer()
        self.addCleanup(self._reset_lemmatizer)

    @staticmethod
    def _reset_lemmatizer():
        metadata._lemmatizer = None
        metadata._lemma_cache_unavailable_logged = False

    def test_missing_cache_returns_the_token_unchanged_and_logs_once(self):
        with override_settings(
            LEMMA_CACHE_DIR=str(self.cache_dir), LEMMA_CACHE_BUILD_ALLOWED=False
        ):
            with self.assertLogs("video_study.metadata", level="WARNING") as logs:
                result = lemmatize_german("Häusern")

        self.assertEqual(result, "Häusern")
        self.assertTrue(any("Lemma cache unavailable" in message for message in logs.output))
        self.assertFalse((self.cache_dir / "de.dic").exists())

    def test_corrupt_cache_fails_closed_and_check_command_errors(self):
        (self.cache_dir / "de.dic").write_bytes(b"not a marisa trie")

        with override_settings(
            LEMMA_CACHE_DIR=str(self.cache_dir), LEMMA_CACHE_BUILD_ALLOWED=False
        ):
            result = lemmatize_german("Häusern")
            self.assertEqual(result, "Häusern")

            with self.assertRaises(CommandError):
                call_command("build_lemma_cache", "--check")

    def test_cache_build_allowed_true_builds_in_process(self):
        with override_settings(LEMMA_CACHE_DIR=str(self.cache_dir), LEMMA_CACHE_BUILD_ALLOWED=True):
            result = lemmatize_german("Häusern")

        self.assertEqual(result, "Haus")
        self.assertTrue((self.cache_dir / "de.dic").exists())
