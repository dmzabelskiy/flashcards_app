from pathlib import Path

from django.conf import settings
from django.test import override_settings
from simplemma.strategies.dictionaries import TrieDictionaryFactory

from video_study import metadata

TEST_LEMMA_CACHE_DIR = Path(settings.BASE_DIR) / "temp" / "test_lemma_cache"


def build_test_lemma_cache():
    """Build the German and Dutch tries once into `TEST_LEMMA_CACHE_DIR`.

    Guarded by file existence so repeated test runs are instant; keeps
    tests off the repo's `lemma_cache/` and its shared build.
    """
    TEST_LEMMA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    factory = TrieDictionaryFactory(disk_cache_dir=str(TEST_LEMMA_CACHE_DIR))
    for lang, probe in (("de", "Haus"), ("nl", "huis")):
        cache_path = TEST_LEMMA_CACHE_DIR / f"{lang}.dic"
        if cache_path.exists():
            continue
        factory.get_dictionary(lang).get(probe)


def reset_lemmatizer():
    metadata._lemmatizer = None


class LemmaCacheTestMixin:
    """Point real-lemma tests at a prebuilt trie in `temp/`, fail-closed.

    Builds the trie once per test run, then overrides
    `LEMMA_CACHE_DIR`/`LEMMA_CACHE_BUILD_ALLOWED` so `get_lemmatizer()` uses
    `PrebuiltTrieDictionaryFactory` against that prebuilt cache — the same
    fail-closed code path production uses — instead of the repo's
    `lemma_cache/` and the module singleton other test runs may have built
    against different settings.
    """

    def setUp(self):
        super().setUp()
        build_test_lemma_cache()
        reset_lemmatizer()
        override = override_settings(
            LEMMA_CACHE_DIR=str(TEST_LEMMA_CACHE_DIR),
            LEMMA_CACHE_BUILD_ALLOWED=False,
        )
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(reset_lemmatizer)
