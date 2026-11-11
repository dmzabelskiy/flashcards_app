import csv

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from cards.forms import CardForm, CardSearchForm
from cards.models import Card
from core.language_modes import filter_cards_for_mode, filter_decks_for_mode, get_language_mode
from decks.models import Deck
from decks.services import get_global_deck


class CardBrowseView(ListView):
    model = Card
    template_name = "cards/card_list.html"
    context_object_name = "cards"
    paginate_by = 50

    def get_queryset(self):
        self.language_mode = get_language_mode(self.request)
        queryset = filter_cards_for_mode(
            Card.objects.for_user(self.request.user).select_related("deck"),
            self.language_mode,
        )
        self.form = CardSearchForm(self.request.GET)
        if not self.form.is_valid():
            return queryset

        data = self.form.cleaned_data
        queryset = queryset.search(data.get("q"))
        if data.get("deck"):
            queryset = queryset.filter(deck_id=data["deck"], deck__language_mode=self.language_mode)
        if data.get("tag"):
            queryset = queryset.filter(tags__icontains=data["tag"])
        if data.get("card_type"):
            queryset = queryset.filter(card_type=data["card_type"])
        if data.get("due_only"):
            queryset = queryset.filter(due_at__lte=timezone.now())
        if data.get("suspended") == "active":
            queryset = queryset.filter(is_suspended=False)
        elif data.get("suspended") == "suspended":
            queryset = queryset.filter(is_suspended=True)
        if data.get("leech"):
            queryset = queryset.leeches()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        language_mode = getattr(self, "language_mode", get_language_mode(self.request))
        context["form"] = getattr(self, "form", CardSearchForm(self.request.GET))
        context["decks"] = filter_decks_for_mode(
            Deck.objects.for_user(self.request.user), language_mode
        )
        return context


class LanguageModeCardMixin:
    def get_queryset(self):
        return filter_cards_for_mode(
            Card.objects.for_user(self.request.user).select_related("deck"),
            get_language_mode(self.request),
        )


class CardDetailView(LanguageModeCardMixin, DetailView):
    model = Card
    template_name = "cards/card_detail.html"


class CardCreateView(CreateView):
    model = Card
    form_class = CardForm
    template_name = "cards/card_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_mode"] = get_language_mode(self.request)
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        deck_id = self.request.GET.get("deck")
        if self.request.GET.get("global") == "1":
            initial["deck"] = get_global_deck(
                self.request.user,
                get_language_mode(self.request),
            ).pk
            return initial
        if (
            deck_id
            and filter_decks_for_mode(
                Deck.objects.for_user(self.request.user).filter(pk=deck_id),
                get_language_mode(self.request),
            ).exists()
        ):
            initial["deck"] = deck_id
        return initial


class CardUpdateView(LanguageModeCardMixin, UpdateView):
    model = Card
    form_class = CardForm
    template_name = "cards/card_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["language_mode"] = get_language_mode(self.request)
        kwargs["user"] = self.request.user
        return kwargs


class CardDeleteView(LanguageModeCardMixin, DeleteView):
    model = Card
    template_name = "cards/card_confirm_delete.html"
    success_url = reverse_lazy("cards:list")


def export_deck_csv(request, deck_id):
    deck = get_object_or_404(
        filter_decks_for_mode(Deck.objects.for_user(request.user), get_language_mode(request)),
        pk=deck_id,
    )
    response = HttpResponse(content_type="text/csv")
    filename = f"{slugify(deck.name) or 'deck'}-cards.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(
        [
            "front",
            "back",
            "example_sentence",
            "notes",
            "tags",
            "card_type",
            "due_at",
            "interval_days",
            "ease_factor",
            "review_count",
            "lapse_count",
        ]
    )
    for card in deck.cards.order_by("front"):
        writer.writerow(
            [
                card.front,
                card.back,
                card.example_sentence,
                card.notes,
                card.tags,
                card.card_type,
                card.due_at.isoformat(),
                card.interval_days,
                card.ease_factor,
                card.review_count,
                card.lapse_count,
            ]
        )
    return response
