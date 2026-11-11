from core.language_packs import pack_for_mode
from decks.models import Deck

LANGUAGE_MODE_SESSION_KEY = "language_mode"


def normalize_language_mode(value):
    if value in Deck.LanguageMode.values:
        return value
    return Deck.LanguageMode.DE_RU


def get_language_mode(request):
    if request is None:
        return Deck.LanguageMode.DE_RU
    return normalize_language_mode(request.session.get(LANGUAGE_MODE_SESSION_KEY))


def set_language_mode(request, value):
    if value not in Deck.LanguageMode.values:
        raise ValueError(f"Unsupported language mode: {value}")
    request.session[LANGUAGE_MODE_SESSION_KEY] = value
    return value


def get_language_mode_options(active_mode):
    active_mode = normalize_language_mode(active_mode)
    return [
        {
            "value": value,
            "label": get_target_language_label(value),
            "description": f"{pack_for_mode(value).name} → {get_target_language_label(value)}",
            "is_active": value == active_mode,
        }
        for value in Deck.LanguageMode.values
    ]


def get_source_language(language_mode):
    return pack_for_mode(normalize_language_mode(language_mode)).code


def get_target_language(language_mode):
    language_mode = normalize_language_mode(language_mode)
    return language_mode.rsplit("-", 1)[-1].lower()


def get_target_language_label(language_mode):
    language_mode = normalize_language_mode(language_mode)
    return Deck.LanguageMode(language_mode).label


def filter_decks_for_mode(queryset, language_mode):
    return queryset.filter(language_mode=normalize_language_mode(language_mode))


def filter_cards_for_mode(queryset, language_mode):
    return queryset.filter(deck__language_mode=normalize_language_mode(language_mode))


def language_mode_context(request):
    language_mode = get_language_mode(request)
    pack = pack_for_mode(language_mode)
    return {
        "language_mode": language_mode,
        "language_mode_label": get_target_language_label(language_mode),
        "language_mode_options": get_language_mode_options(language_mode),
        "target_language": get_target_language(language_mode),
        "target_language_label": get_target_language_label(language_mode),
        "language_mode_css_class": f"mode-{language_mode.lower()}",
        "source_language": pack.code,
        "source_language_label": pack.name,
        "features": pack.features,
    }
