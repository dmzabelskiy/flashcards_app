from django.contrib import admin

from cards.models import Card


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ["front", "deck", "deck__owner", "card_type", "due_at", "is_suspended"]
    list_filter = ["deck__owner", "card_type", "is_suspended"]
    search_fields = ["front", "back", "example_sentence", "notes", "tags"]
