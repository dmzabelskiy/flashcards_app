import re

from cards.articles import parse_leading_article
from video_study.nl.lemma import lemmatize_dutch
from video_study.nl.wiktionary import FOUND, lookup_nl_dictionary
from video_study.normalization import CaptureResolution

DUTCH_ARTICLES = ("de", "het")
NL_ARTICLE_TO_GENDER = {"de": "common", "het": "neuter"}

WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
CLAUSE_BOUNDARY = re.compile(r"[,.!?;:]")
SEPARABLE_PARTICLES = frozenset(
    {
        "op",
        "af",
        "aan",
        "uit",
        "in",
        "mee",
        "terug",
        "weg",
        "over",
        "door",
        "na",
        "voor",
        "toe",
        "bij",
        "om",
        "samen",
        "tegen",
        "vast",
        "thuis",
    }
)
DIMINUTIVE_SUFFIXES = ("je", "tje", "pje", "etje")
DE_SUFFIXES = ("heid", "ing", "tie", "teit", "schap")
DETERMINERS = frozenset(
    {
        "de",
        "het",
        "een",
        "deze",
        "die",
        "dit",
        "dat",
        "mijn",
        "je",
        "jouw",
        "zijn",
        "haar",
        "ons",
        "onze",
        "hun",
        "uw",
        "geen",
    }
)


def _tokens(text):
    return [token.casefold() for token in WORD.findall(text)]


def _clause_tokens_for(surface, context):
    """Return the single clause's tokens containing ``surface``, or None when ambiguous/absent.

    Only the clause that contains ``surface`` counts, and the click carries
    no occurrence index, so a token repeated across clauses can't be traced
    to the one the owner selected.
    """
    target = surface.casefold()
    matching_clauses = [
        tokens
        for tokens in (_tokens(clause) for clause in CLAUSE_BOUNDARY.split(context))
        if target in tokens
    ]
    if len(matching_clauses) != 1:
        return None
    return matching_clauses[0]


def _preceded_by_determiner(surface, context):
    """A determiner or possessive right before the selected word means it is a noun."""
    tokens = _clause_tokens_for(surface, context)
    if tokens is None:
        return False
    target_index = tokens.index(surface.casefold())
    return target_index > 0 and tokens[target_index - 1] in DETERMINERS


def _separable_candidate(surface, candidate, context):
    """Return particle + ``candidate`` when ``surface``'s clause ends in a separable particle.

    A determiner or possessive right before the selected word means it is a
    noun, not the selected verb, so the probe is skipped.
    """
    tokens = _clause_tokens_for(surface, context)
    if tokens is None:
        return ""
    target = surface.casefold()
    target_index = tokens.index(target)
    if target_index > 0 and tokens[target_index - 1] in DETERMINERS:
        return ""
    particle = tokens[-1]
    if particle in SEPARABLE_PARTICLES and target_index < len(tokens) - 1:
        return particle + candidate
    return ""


def _suffix_article(word):
    lowered = word.casefold()
    if lowered.endswith(DIMINUTIVE_SUFFIXES):
        return "het"
    if lowered.endswith(DE_SUFFIXES):
        return "de"
    return ""


def resolve_capture_nl(text, context, budget_start=None):
    """Return the finished card fields for a Dutch word selected in a transcript.

    ``text`` must be the selection as the owner clicked it. ``context`` is
    used in memory to choose the lemma and is never sent anywhere; only
    ``text`` or a lemma derived from it is ever sent to Wiktionary.
    """
    text = " ".join(str(text or "").split()).strip()
    context = " ".join(str(context or "").split()).strip()
    explicit_article, stem = parse_leading_article(text, DUTCH_ARTICLES)
    if " " in stem:
        # "de"/"het" followed by more than one word is still a phrase: only
        # an article directly on a single noun is a noun selection.
        return CaptureResolution(
            lemma=text,
            article="",
            plural="",
            part_of_speech="phrase",
            example_sentence="",
            dictionary_status="skipped",
            trustworthy=True,
            translation="",
        )

    surface = stem.lower()
    candidate, available = lemmatize_dutch(surface)

    result = None
    if candidate != surface and surface.endswith(DIMINUTIVE_SUFFIXES):
        # simplemma strips a lexicalised diminutive back to its base noun
        # ("broodje" -> "brood"); try the surface itself first and keep it
        # only when Wiktionary confirms it as its own lemma, not a redirect.
        diminutive_attempt = lookup_nl_dictionary(surface, surface, budget_start=budget_start)
        if diminutive_attempt.status == FOUND and diminutive_attempt.lemma == surface:
            result = diminutive_attempt

    if result is None and _preceded_by_determiner(surface, context):
        # simplemma can lemmatise a noun straight past its determiner into a
        # verb ("bel" -> "bellen"); try the surface itself first and keep it
        # only when Wiktionary confirms it as its own noun lemma, not a
        # redirect or a same-spelled verb ("huizen" is both the plural of
        # "huis" and its own verb lemma; the determiner rules out the verb).
        determiner_attempt = lookup_nl_dictionary(surface, surface, budget_start=budget_start)
        if (
            determiner_attempt.status == FOUND
            and determiner_attempt.lemma == surface
            and determiner_attempt.part_of_speech == "noun"
        ):
            result = determiner_attempt

    if result is None:
        particle_candidate = _separable_candidate(surface, candidate, context)
        if particle_candidate:
            attempt = lookup_nl_dictionary(particle_candidate, surface, budget_start=budget_start)
            if attempt.status == FOUND and attempt.lemma == particle_candidate:
                result = attempt
    if result is None:
        result = lookup_nl_dictionary(candidate, surface, budget_start=budget_start)

    if result.status == FOUND:
        is_noun = result.part_of_speech == "noun"
        article = (result.article if is_noun else "") or explicit_article
        return CaptureResolution(
            lemma=result.lemma,
            article=article,
            plural=result.plural if is_noun else "",
            part_of_speech=result.part_of_speech,
            example_sentence="",
            dictionary_status=FOUND,
            trustworthy=True,
            translation=result.glosses,
        )

    part_of_speech = "verb" if candidate != surface and candidate.endswith("en") else ""
    return CaptureResolution(
        lemma=candidate,
        article=explicit_article or _suffix_article(surface),
        plural="",
        part_of_speech=part_of_speech,
        example_sentence="",
        dictionary_status=result.status,
        trustworthy=available,
        translation="",
    )
