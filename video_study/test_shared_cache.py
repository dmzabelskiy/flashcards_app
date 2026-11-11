from core.testutils import AuthedTestCase, create_user
from video_study.models import WordTranslation


class SharedCacheTests(AuthedTestCase):
    def test_translation_cache_is_not_per_user(self):
        WordTranslation.objects.create(
            source_text="Haus", source_language="de", target_language="ru", translated_text="дом"
        )
        # A second user resolves the SAME cached row — no per-user duplication, no owner field.
        create_user("other")
        row = WordTranslation.objects.get(source_text="Haus")
        self.assertEqual(row.translated_text, "дом")
        self.assertFalse(hasattr(row, "owner"))
