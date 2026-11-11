from unittest.mock import patch

from video_study.dictionary import DictionaryResult
from video_study.testutils_lemma import LemmaCacheTestMixin


class OfflineTranslationMixin(LemmaCacheTestMixin):
    """Provide the prebuilt lemma cache for offline translation tests."""


class DictionaryStubMixin:
    """Stub the Wiktionary lookup so view tests never reach the network.

    Set ``dictionary_result`` on the class or in a test to change the answer.
    """

    dictionary_result = DictionaryResult(status="missing")

    def setUp(self):
        super().setUp()
        patcher = patch(
            "video_study.normalization.lookup_dictionary",
            side_effect=lambda *args, **kwargs: self.dictionary_result,
        )
        self.lookup_dictionary = patcher.start()
        self.addCleanup(patcher.stop)
