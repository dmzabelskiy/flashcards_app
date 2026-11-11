import io
import json
from unittest.mock import ANY, patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.testutils import AuthedTestCase
from video_study.models import ChannelVideo, PodcastEpisode, TranscriptVideo, WordTranslation
from video_study.services import (
    KURZGESAGT_DE_FEED_URL,
    TranslationUnavailable,
    _russian_wiktionary_meanings,
    clean_translation_text,
    get_kurzgesagt_video_of_day,
    get_podcast_of_day,
    normalize_selection,
    parse_channel_feed,
    parse_podcast_posts,
    refresh_kurzgesagt_catalog,
    refresh_podcast_catalog,
    request_translation,
    translation_uses_target_script,
)
from video_study.testutils import DictionaryStubMixin, OfflineTranslationMixin


class RefreshMediaCatalogsStaleTests(TestCase):
    def test_if_stale_skips_a_freshly_synced_catalog(self):
        now = timezone.now()
        ChannelVideo.objects.create(
            channel_key="kurzgesagt-de",
            youtube_id="dQw4w9WgXcQ",
            title="Fresh",
            source_url="https://youtu.be/dQw4w9WgXcQ",
            synced_at=now,
        )
        PodcastEpisode.objects.create(
            source_url="https://14minuten.de/folge-1/",
            youtube_id="dQw4w9WgXcQ",
            title="Fresh",
            synced_at=now,
        )
        with (
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_kurzgesagt_catalog"
            ) as vid,
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_podcast_catalog"
            ) as pod,
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_channel_catalog",
                return_value=[],
            ),
        ):
            call_command("refresh_media_catalogs", "--if-stale", "24")
        vid.assert_not_called()
        pod.assert_not_called()

    def test_if_stale_refreshes_an_old_catalog(self):
        old = timezone.now() - timezone.timedelta(hours=48)
        ChannelVideo.objects.create(
            channel_key="kurzgesagt-de",
            youtube_id="dQw4w9WgXcQ",
            title="Old",
            source_url="https://youtu.be/dQw4w9WgXcQ",
            synced_at=old,
        )
        with (
            override_settings(VIDEO_STUDY_TRANSCRIPT_PREFETCH=True),
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_kurzgesagt_catalog",
                return_value=[],
            ) as vid,
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_podcast_catalog",
                return_value=[],
            ) as pod,
            patch(
                "video_study.management.commands.refresh_media_catalogs.prefetch_kurzgesagt_transcripts",
                return_value={"catalog_refreshed": True, "fetched": 0, "failed": 0, "remaining": 0},
            ) as prefetch,
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_channel_catalog",
                return_value=[],
            ),
            patch(
                "video_study.management.commands.refresh_media_catalogs.prefetch_channel_transcripts",
                return_value={"catalog_refreshed": True, "fetched": 0, "failed": 0, "remaining": 0},
            ),
        ):
            call_command("refresh_media_catalogs", "--if-stale", "24")
        vid.assert_called_once()
        pod.assert_called_once()  # no podcast rows exist → not fresh → refreshed
        prefetch.assert_called_once_with()

    def test_prefetch_step_is_skipped_when_the_setting_is_off(self):
        # VIDEO_STUDY_TRANSCRIPT_PREFETCH is false for the whole suite (see
        # config/test_runner.py), so this covers the command's default
        # under test without any override_settings.
        with (
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_kurzgesagt_catalog",
                return_value=[],
            ),
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_podcast_catalog",
                return_value=[],
            ),
            patch(
                "video_study.management.commands.refresh_media_catalogs.prefetch_kurzgesagt_transcripts"
            ) as prefetch,
            patch(
                "video_study.management.commands.refresh_media_catalogs.refresh_channel_catalog",
                return_value=[],
            ),
            patch(
                "video_study.management.commands.refresh_media_catalogs.prefetch_channel_transcripts"
            ) as nl_prefetch,
        ):
            stdout = io.StringIO()
            call_command("refresh_media_catalogs", "--if-stale", "24", stdout=stdout)
        prefetch.assert_not_called()
        nl_prefetch.assert_not_called()
        self.assertIn("Kurzgesagt transcript prefetch disabled; skipping.", stdout.getvalue())


class SelectionNormalizationTests(SimpleTestCase):
    def test_normalizes_a_multi_word_selection(self):
        self.assertEqual(
            normalize_selection("  Eine  nasse,\n unwirtliche Wüste  "),
            "eine nasse, unwirtliche wüste",
        )

    def test_rejects_a_selection_over_the_provider_byte_limit(self):
        with self.assertRaises(TranslationUnavailable):
            normalize_selection("ä" * 251)


class PhraseTranslationEndpointTests(DictionaryStubMixin, OfflineTranslationMixin, AuthedTestCase):
    @patch(
        "video_study.views.request_translation",
        return_value="негостеприимная пустыня",
    )
    def test_translates_and_caches_a_selected_phrase(self, request_translation):
        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps(
                {
                    "text": "Eine unwirtliche Wüste",
                    "source_language": "de",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["translation"], "негостеприимная пустыня")
        translation = WordTranslation.objects.get()
        self.assertEqual(translation.source_text, "eine unwirtliche wüste")
        request_translation.assert_called_once_with(
            "eine unwirtliche wüste",
            source_language="de",
            target_language="ru",
            source_form="eine unwirtliche wüste",
            budget_start=ANY,
        )

    @patch(
        "video_study.views.request_translation",
        return_value="негостеприимная пустыня",
    )
    def test_capitalised_phrase_hits_an_existing_lowercase_row(self, request_translation):
        WordTranslation.objects.create(
            source_text="eine unwirtliche wüste",
            source_language="de",
            target_language="ru",
            translated_text="негостеприимная пустыня",
        )

        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps(
                {
                    "text": "Eine unwirtliche Wüste",
                    "source_language": "de",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cached"])
        self.assertEqual(response.json()["translation"], "негостеприимная пустыня")
        request_translation.assert_not_called()


class TranslationQualityTests(DictionaryStubMixin, OfflineTranslationMixin, AuthedTestCase):
    noisy_translation = (
        "исполнять (поручение); исправлять (обязанности); совершать (обряд) "
        "{{пример|Sie erst einmal ihre Arbeit, dann können wir über Urlaub reden."
        "|перевод=Сначала свою работу, а потом поговорим об отпуске.}}"
    )

    def test_russian_translation_requires_cyrillic(self):
        self.assertFalse(translation_uses_target_script("NEPRIIATIE PSIKHOLOGICHESKOE", "ru"))
        self.assertTrue(translation_uses_target_script("отказ", "ru"))
        self.assertTrue(translation_uses_target_script("rechazo", "es"))

    def test_extracts_russian_wiktionary_meanings_for_german(self):
        wikitext = """
= {{-de-}} =
==== Значение ====
# [[отказ]]; [[отклонение]]; [[отвод]] {{пример||перевод=}}
# [[отражение]], [[отпор]]
=== Этимология ===
"""

        self.assertEqual(
            _russian_wiktionary_meanings(wikitext, "de"),
            ["отказ; отклонение; отвод", "отражение, отпор"],
        )

    def test_cleans_dictionary_examples_from_translation_text(self):
        self.assertEqual(
            clean_translation_text(self.noisy_translation, max_alternatives=3),
            "исполнять (поручение); исправлять (обязанности); совершать (обряд)",
        )

    def test_cleans_nested_wiki_templates_without_losing_meanings(self):
        translation = (
            "[[исполнять]] {{помета|книжн.}}; [[совершать|совершать]] "
            "{{пример|Arbeit {{выдел|verrichten}}|перевод=выполнять работу}}"
        )

        self.assertEqual(clean_translation_text(translation), "исполнять; совершать")

    @patch("video_study.views.request_translation")
    def test_cached_dictionary_markup_is_cleaned_and_repaired(self, request_translation_mock):
        # A pre-existing row keyed by the legacy casefolded lemma still hits
        # for a capitalised selection of the same word.
        cached = WordTranslation.objects.create(
            source_text="verrichten",
            source_language="de",
            target_language="ru",
            translated_text=self.noisy_translation,
        )

        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps({"text": "Verrichten", "source_language": "de"}),
            content_type="application/json",
        )

        expected = "исполнять (поручение); исправлять (обязанности); совершать (обряд)"
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["cached"])
        self.assertEqual(response.json()["translation"], expected)
        cached.refresh_from_db()
        self.assertEqual(cached.translated_text, expected)
        request_translation_mock.assert_not_called()

    @override_settings(
        VIDEO_STUDY_TRANSLATION_PROVIDERS=("deepl", "mymemory"),
    )
    @patch(
        "video_study.services._request_mymemory_translation",
        return_value="отказ",
    )
    @patch(
        "video_study.services._request_deepl_translation",
        return_value="OTKAZ",
    )
    def test_provider_chain_skips_non_cyrillic_russian(
        self,
        request_deepl,
        request_mymemory,
    ):
        translated = request_translation("Zurückweisung", "de", "ru")

        self.assertEqual(translated, "отказ")
        request_deepl.assert_called_once_with("zurückweisung", "de", "ru")
        request_mymemory.assert_called_once_with("zurückweisung", "de", "ru")

    @patch("video_study.views.request_translation", return_value="отказ")
    def test_invalid_cached_russian_is_replaced(self, request_translation_mock):
        # An invalid legacy row is never returned as a fallback: the
        # provider is still called, with the canonical (case-preserving)
        # lemma, and the canonical key is what gets written.
        WordTranslation.objects.create(
            source_text="zurückweisung",
            source_language="de",
            target_language="ru",
            translated_text="NEPRIIATIE PSIKHOLOGICHESKOE",
        )

        response = self.client.post(
            reverse("video_study:translate_word"),
            data=json.dumps(
                {
                    "text": "Zurückweisung",
                    "source_language": "de",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["translation"], "отказ")
        cached = WordTranslation.objects.get(source_text="Zurückweisung")
        self.assertEqual(cached.translated_text, "отказ")
        self.assertEqual(cached.provider, "auto")
        request_translation_mock.assert_called_once_with(
            "Zurückweisung",
            source_language="de",
            target_language="ru",
            source_form="Zurückweisung",
            budget_start=ANY,
        )


class TheaterModeMarkupTests(AuthedTestCase):
    def test_loaded_workspace_exposes_theater_controls_and_selection_help(self):
        TranscriptVideo.objects.create(
            youtube_id="dQw4w9WgXcQ",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            source_language="de",
            transcript=[
                {
                    "text": "Eine unwirtliche Wüste",
                    "start": 0,
                    "duration": 2,
                }
            ],
        )

        response = self.client.get(
            reverse("video_study:workspace"),
            {"video": "dQw4w9WgXcQ", "lang": "de"},
        )

        self.assertContains(response, 'id="theater-toggle"')
        self.assertContains(response, 'aria-pressed="true"')
        self.assertContains(response, "Exit theater")
        self.assertContains(response, 'class="video-theater-mode"')
        self.assertContains(response, "drag to select a phrase")
        self.assertContains(response, "css/video_study_theater.css")


CHANNEL_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <yt:videoId>dQw4w9WgXcQ</yt:videoId>
    <title>Warum ist der Himmel blau?</title>
    <published>2026-07-31T08:00:00+00:00</published>
    <link rel="alternate" href="https://www.youtube.com/watch?v=dQw4w9WgXcQ" />
    <media:group><media:thumbnail url="https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg" /></media:group>
  </entry>
</feed>"""


class ChannelFeedParsingTests(SimpleTestCase):
    def test_parses_video_metadata_from_the_public_atom_feed(self):
        videos = parse_channel_feed(CHANNEL_FEED)

        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["youtube_id"], "dQw4w9WgXcQ")
        self.assertEqual(videos[0]["title"], "Warum ist der Himmel blau?")
        self.assertEqual(
            videos[0]["thumbnail_url"],
            "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
        )
        self.assertIsNotNone(videos[0]["published_at"])


class ChannelCatalogTests(AuthedTestCase):
    @patch(
        "video_study.services.fetch_channel_videos",
        return_value=[
            {
                "youtube_id": "dQw4w9WgXcQ",
                "title": "Warum ist der Himmel blau?",
                "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
                "published_at": timezone.now(),
            }
        ],
    )
    def test_refresh_caches_channel_videos(self, fetch_channel_videos):
        refresh_kurzgesagt_catalog()

        video = ChannelVideo.objects.get()
        self.assertEqual(video.title, "Warum ist der Himmel blau?")
        self.assertEqual(video.channel_key, "kurzgesagt-de")
        fetch_channel_videos.assert_called_once_with(KURZGESAGT_DE_FEED_URL, name="Kurzgesagt")

    def test_daily_selection_is_stable_while_the_catalog_is_fresh(self):
        now = timezone.now()
        first = ChannelVideo.objects.create(
            channel_key="kurzgesagt-de",
            youtube_id="dQw4w9WgXcQ",
            title="First",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            synced_at=now,
        )
        ChannelVideo.objects.create(
            channel_key="kurzgesagt-de",
            youtube_id="abcdefghijk",
            title="Second",
            source_url="https://www.youtube.com/watch?v=abcdefghijk",
            synced_at=now,
        )

        selected_first = get_kurzgesagt_video_of_day(now=now)
        selected_second = get_kurzgesagt_video_of_day(now=now)

        self.assertIsNotNone(selected_first)
        self.assertEqual(selected_first.pk, selected_second.pk)
        self.assertIn(selected_first.pk, {first.pk, 2})

    def test_video_never_repeats_on_consecutive_days(self):
        import datetime

        now = timezone.now()
        for i in range(6):
            ChannelVideo.objects.create(
                channel_key="kurzgesagt-de",
                youtube_id=f"vid{i:08d}",
                title=f"Video {i}",
                source_url=f"https://www.youtube.com/watch?v=vid{i:08d}",
                synced_at=now,
            )
        picks = [
            get_kurzgesagt_video_of_day(now=now + datetime.timedelta(days=d)).pk for d in range(30)
        ]
        for a, b in zip(picks, picks[1:], strict=False):
            self.assertNotEqual(a, b)


PODCAST_POSTS = [
    {
        "title": {"rendered": "Folge 257 &#8211; Deutsch lernen &#038; Motivation"},
        "link": "https://14minuten.de/folge-257-deutsch-lernen-motivation/",
        "date_gmt": "2026-07-03T05:31:01",
        "content": {
            "rendered": (
                "<p>Niveau: mittel</p>"
                "<iframe src=https://www.youtube.com/embed/dQw4w9WgXcQ></iframe>"
            )
        },
        "excerpt": {"rendered": "<p>Strategien und Tricks zum Deutschlernen.</p>"},
        "_embedded": {
            "wp:featuredmedia": [{"source_url": "https://14minuten.de/wp-content/uploads/257.jpg"}]
        },
    }
]


PODCAST_TRANSCRIPT = [
    {"text": "Hello, curious human.", "start": 0.0, "duration": 2.4},
    {"text": "Let us begin.", "start": 2.4, "duration": 1.8},
]


class PodcastParsingTests(SimpleTestCase):
    def test_parses_public_episode_metadata_and_youtube_embed(self):
        episodes = parse_podcast_posts(PODCAST_POSTS)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["youtube_id"], "dQw4w9WgXcQ")
        self.assertEqual(episodes[0]["title"], "Folge 257 – Deutsch lernen & Motivation")
        self.assertEqual(episodes[0]["level"], "mittel")
        self.assertEqual(
            episodes[0]["thumbnail_url"],
            "https://14minuten.de/wp-content/uploads/257.jpg",
        )
        self.assertIsNotNone(episodes[0]["published_at"])


class PodcastCatalogTests(AuthedTestCase):
    def test_refresh_caches_public_episode_metadata(self):
        with patch(
            "video_study.services.fetch_podcast_posts",
            return_value=[
                {
                    "source_url": PODCAST_POSTS[0]["link"],
                    "youtube_id": "dQw4w9WgXcQ",
                    "title": "Folge 257",
                    "description": "Motivation",
                    "level": "mittel",
                    "thumbnail_url": "https://14minuten.de/257.jpg",
                    "published_at": timezone.now(),
                }
            ],
        ):
            refresh_podcast_catalog()

        episode = PodcastEpisode.objects.get()
        self.assertEqual(episode.title, "Folge 257")
        self.assertEqual(episode.youtube_id, "dQw4w9WgXcQ")
        self.assertEqual(episode.transcript, [])

    def test_daily_episode_is_stable_for_a_fresh_catalog(self):
        now = timezone.now()
        first = PodcastEpisode.objects.create(
            source_url="https://14minuten.de/folge-1/",
            youtube_id="dQw4w9WgXcQ",
            title="First",
            synced_at=now,
        )
        PodcastEpisode.objects.create(
            source_url="https://14minuten.de/folge-2/",
            youtube_id="abcdefghijk",
            title="Second",
            synced_at=now,
        )

        selected_first = get_podcast_of_day(now=now)
        selected_second = get_podcast_of_day(now=now)

        self.assertEqual(selected_first.pk, selected_second.pk)
        self.assertIn(selected_first.pk, {first.pk, 2})

    def test_episode_never_repeats_on_consecutive_days(self):
        import datetime

        now = timezone.now()
        for i in range(6):
            PodcastEpisode.objects.create(
                source_url=f"https://14minuten.de/folge-{i}/",
                youtube_id=f"pod{i:08d}",
                title=f"Folge {i}",
                synced_at=now,
            )
        picks = [get_podcast_of_day(now=now + datetime.timedelta(days=d)).pk for d in range(30)]
        for a, b in zip(picks, picks[1:], strict=False):
            self.assertNotEqual(a, b)


class PodcastWorkspaceTests(AuthedTestCase):
    def test_workspace_renders_cached_episode_and_transcript(self):
        episode = PodcastEpisode.objects.create(
            source_url="https://14minuten.de/folge-257-deutsch-lernen-motivation/",
            youtube_id="dQw4w9WgXcQ",
            title="Folge 257",
            transcript=PODCAST_TRANSCRIPT,
            synced_at=timezone.now(),
        )

        response = self.client.get(
            reverse("podcasts:workspace"),
            {"episode": episode.pk},
        )

        self.assertContains(response, "dQw4w9WgXcQ")
        self.assertContains(response, "podcast-transcript-data")
        self.assertContains(response, "Hello, curious human.")
        self.assertContains(response, "Podcast study")
        self.assertContains(response, "Exit theater")

    def test_workspace_lazily_fetches_selected_episode_transcript(self):
        episode = PodcastEpisode.objects.create(
            source_url="https://14minuten.de/folge-257/",
            youtube_id="dQw4w9WgXcQ",
            title="Folge 257",
            synced_at=timezone.now(),
        )
        with patch(
            "video_study.views.fetch_transcript",
            return_value=PODCAST_TRANSCRIPT,
        ) as fetch_transcript:
            response = self.client.get(
                reverse("podcasts:workspace"),
                {"episode": episode.pk},
            )

        self.assertEqual(response.status_code, 200)
        fetch_transcript.assert_called_once_with(
            "dQw4w9WgXcQ",
            languages=("de",),
        )
        self.assertEqual(
            PodcastEpisode.objects.get(pk=episode.pk).transcript,
            PODCAST_TRANSCRIPT,
        )


class PodcastSaveToGlobalDeckTests(AuthedTestCase):
    def test_workspace_with_selected_episode_renders_save_flow(self):
        episode = PodcastEpisode.objects.create(
            source_url="https://14minuten.de/folge-257-deutsch-lernen-motivation/",
            youtube_id="dQw4w9WgXcQ",
            title="Folge 257",
            transcript=PODCAST_TRANSCRIPT,
            synced_at=timezone.now(),
        )

        response = self.client.get(
            reverse("podcasts:workspace"),
            {"episode": episode.pk},
        )
        content = response.content.decode()

        self.assertContains(response, 'id="add-to-global"')
        self.assertContains(response, 'id="global-deck-status"')
        self.assertContains(
            response,
            f'data-add-to-global-url="{reverse("cards:add_to_global")}"',
        )
        self.assertContains(response, f'data-source-url="{episode.source_url}"')
        self.assertLess(
            content.index("js/podcast_study.js"),
            content.index("js/video_global_deck.js"),
        )

    def test_workspace_without_selected_episode_omits_save_flow(self):
        with patch("video_study.views.refresh_podcast_catalog"):
            response = self.client.get(reverse("podcasts:workspace"))

        self.assertNotContains(response, 'id="add-to-global"')
        self.assertNotContains(response, 'id="global-deck-status"')
        self.assertNotContains(response, "data-add-to-global-url")
        self.assertNotContains(response, "js/video_global_deck.js")
