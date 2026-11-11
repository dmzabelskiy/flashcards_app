from django.contrib import admin

from video_study.models import DictionaryEntry, TranscriptVideo, WordTranslation


@admin.register(TranscriptVideo)
class TranscriptVideoAdmin(admin.ModelAdmin):
    list_display = ("youtube_id", "source_language", "updated_at")
    search_fields = ("youtube_id", "source_url")


@admin.register(WordTranslation)
class WordTranslationAdmin(admin.ModelAdmin):
    list_display = (
        "source_text",
        "translated_text",
        "source_language",
        "target_language",
        "updated_at",
    )
    search_fields = ("source_text", "translated_text")


@admin.register(DictionaryEntry)
class DictionaryEntryAdmin(admin.ModelAdmin):
    list_display = ("headword", "status", "lemma", "article", "plural", "updated_at")
    search_fields = ("headword", "lemma")
