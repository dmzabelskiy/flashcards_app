from django.contrib import admin

from decks.models import Deck


@admin.register(Deck)
class DeckAdmin(admin.ModelAdmin):
    list_display = ["name", "language_mode", "owner", "is_archived", "created_at", "updated_at"]
    search_fields = ["name", "description"]
    list_filter = ["language_mode", "is_archived", "owner"]
