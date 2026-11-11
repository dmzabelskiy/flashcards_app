from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from cards.models import GLOBAL_DECK_RETIRE_STREAK
from core.language_modes import filter_decks_for_mode, get_language_mode
from decks.forms import DeckForm
from decks.models import Deck


class LanguageModeDeckMixin:
    def get_queryset(self):
        queryset = Deck.objects.for_user(self.request.user)
        return filter_decks_for_mode(queryset, get_language_mode(self.request))


class DeckListView(LanguageModeDeckMixin, ListView):
    model = Deck
    template_name = "decks/deck_list.html"
    context_object_name = "decks"


class DeckDetailView(LanguageModeDeckMixin, DetailView):
    model = Deck
    template_name = "decks/deck_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["global_deck_retire_streak"] = GLOBAL_DECK_RETIRE_STREAK
        return context


class DeckCreateView(CreateView):
    model = Deck
    form_class = DeckForm
    template_name = "decks/deck_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        form.instance.language_mode = get_language_mode(self.request)
        return super().form_valid(form)


class DeckUpdateView(LanguageModeDeckMixin, UpdateView):
    model = Deck
    form_class = DeckForm
    template_name = "decks/deck_form.html"


class DeckDeleteView(LanguageModeDeckMixin, DeleteView):
    model = Deck
    template_name = "decks/deck_confirm_delete.html"
    success_url = reverse_lazy("decks:list")
