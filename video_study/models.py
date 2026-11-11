from django.db import models


class TranscriptVideo(models.Model):
    youtube_id = models.CharField(max_length=11)
    source_url = models.URLField()
    source_language = models.CharField(max_length=12, default="en")
    transcript = models.JSONField(default=list)
    caption_origin = models.CharField(
        max_length=16,
        default="unknown",
        choices=[
            ("unknown", "Cached English captions"),
            ("manual", "English captions"),
            ("generated", "English auto-generated captions"),
            ("translated", "English auto-translated from Dutch by YouTube"),
        ],
    )
    caption_language = models.CharField(max_length=12, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["youtube_id", "source_language"],
                name="unique_video_transcript_language",
            )
        ]

    def __str__(self):
        return f"{self.youtube_id} ({self.source_language})"


class ChannelVideo(models.Model):
    channel_key = models.CharField(max_length=64)
    youtube_id = models.CharField(max_length=11)
    title = models.CharField(max_length=240)
    source_url = models.URLField()
    thumbnail_url = models.URLField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField()

    class Meta:
        ordering = ["-published_at", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel_key", "youtube_id"],
                name="unique_channel_video",
            )
        ]

    def __str__(self):
        return self.title


class PodcastEpisode(models.Model):
    source_url = models.URLField(unique=True)
    youtube_id = models.CharField(max_length=11, blank=True)
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    level = models.CharField(max_length=32, blank=True)
    thumbnail_url = models.URLField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    transcript = models.JSONField(default=list)
    synced_at = models.DateTimeField()

    class Meta:
        ordering = ["-published_at", "title"]

    def __str__(self):
        return self.title


class WordTranslation(models.Model):
    source_text = models.CharField(max_length=500)
    source_language = models.CharField(max_length=12, default="en")
    target_language = models.CharField(max_length=12, default="ru")
    translated_text = models.CharField(max_length=1000)
    provider = models.CharField(max_length=32, default="mymemory")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["source_text"]
        constraints = [
            models.UniqueConstraint(
                fields=["source_text", "source_language", "target_language"],
                name="unique_word_translation_language_pair",
            )
        ]

    def __str__(self):
        return f"{self.source_text} → {self.translated_text}"


class DictionaryEntry(models.Model):
    """Shared, non-personal cache of parsed German Wiktionary pages."""

    headword = models.CharField(max_length=200, unique=True)
    status = models.CharField(max_length=16)
    lemma = models.CharField(max_length=200, blank=True)
    base_form = models.CharField(max_length=200, blank=True)
    article = models.CharField(max_length=3, blank=True)
    plural = models.CharField(max_length=120, blank=True)
    part_of_speech = models.CharField(max_length=16, blank=True)
    example_sentence = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["headword"]

    def __str__(self):
        return f"{self.headword} ({self.status})"


class NlDictionaryEntry(models.Model):
    """Shared, non-personal cache of parsed English Wiktionary pages for Dutch words."""

    headword = models.CharField(max_length=200, unique=True)
    status = models.CharField(max_length=16)
    lemma = models.CharField(max_length=200, blank=True)
    base_form = models.CharField(max_length=200, blank=True)
    article = models.CharField(max_length=3, blank=True)
    plural = models.CharField(max_length=120, blank=True)
    part_of_speech = models.CharField(max_length=16, blank=True)
    glosses = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["headword"]

    def __str__(self):
        return f"{self.headword} ({self.status})"
