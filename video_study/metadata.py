import logging
import re

from django.conf import settings
from simplemma import Lemmatizer
from simplemma.strategies import DefaultStrategy
from simplemma.strategies.dictionaries import TrieDictionaryFactory
from simplemma.strategies.dictionaries.trie_dictionary_factory import BytesTrie, TrieWrapDict

from reviews.drills import REGISTRY

logger = logging.getLogger(__name__)

ARTICLE_TO_GENDER = {
    "der": "masculine",
    "die": "feminine",
    "das": "neuter",
}

WORD_PATTERN = re.compile(r"[A-Za-zÄÖÜäöüß]+(?:[-'][A-Za-zÄÖÜäöüß]+)*")
INDEFINITE_ARTICLES = {
    "eine": "die",
    "einer": "die",
    "einen": "der",
}
INFLECTED_DETERMINERS = {
    "der",
    "die",
    "das",
    "den",
    "dem",
    "des",
    "ein",
    "eine",
    "einen",
    "einem",
    "einer",
    "eines",
}
SUBJECT_PRONOUNS = {
    "ich",
    "du",
    "er",
    "sie",
    "es",
    "wir",
    "ihr",
    "man",
    "wer",
    "was",
    "das",
    "dieser",
    "diese",
    "dieses",
}
OBJECT_PRONOUNS = {"mich", "dich", "sich", "uns", "euch", "ihn", "ihm", "ihnen", "mir", "dir"}
# Forms of haben and werden only: "ist/sind/war" + participle is just as
# often the predicative adjective ("ist verrückt"), so sein is left out.
HABEN_WERDEN_FORMS = {
    "habe",
    "hast",
    "hat",
    "habt",
    "haben",
    "hatte",
    "hattest",
    "hatten",
    "hattet",
    "werde",
    "wirst",
    "wird",
    "werden",
    "werdet",
    "wurde",
    "wurdest",
    "wurden",
    "wurdet",
    "würde",
    "würden",
}


def _build_irregular_form_index():
    forms = {}
    drill = REGISTRY.by_slug("irregular-verbs")
    for item in drill.items if drill else ():
        infinitive = item.prompt
        for alternative in item.alternatives:
            for field in ("present", "preterite", "perfect"):
                for form in alternative.accepted(field, "ru"):
                    for token in WORD_PATTERN.findall(form.casefold()):
                        forms.setdefault(token, set()).add(infinitive)
    return {
        form: next(iter(infinitives))
        for form, infinitives in forms.items()
        if len(infinitives) == 1
    }


IRREGULAR_FORM_TO_INFINITIVE = _build_irregular_form_index()
IRREGULAR_FORM_TO_INFINITIVE.update(
    {
        "bin": "sein",
        "bist": "sein",
        "ist": "sein",
        "sind": "sein",
        "seid": "sein",
        "habe": "haben",
        "hast": "haben",
        "hat": "haben",
        "habt": "haben",
        "werde": "werden",
        "wirst": "werden",
        "wird": "werden",
        "werden": "werden",
        "werdet": "werden",
    }
)


class LemmaCacheUnavailable(RuntimeError):
    """The prebuilt lemma trie is missing or corrupt.

    Raised instead of falling back to `TrieDictionaryFactory`'s
    build-on-miss behaviour: building the trie peaks at ~350 MB, which
    would OOM-kill the 256 MB production machine.
    """


class PrebuiltTrieDictionaryFactory(TrieDictionaryFactory):
    """Load the disk-cached trie only; never build it in-process.

    Used at runtime when `settings.LEMMA_CACHE_BUILD_ALLOWED` is false (the
    production default): a missing or corrupt cache file raises
    `LemmaCacheUnavailable` instead of triggering an in-process build that
    would OOM-kill the 256 MB production machine or race with concurrent
    first requests.
    """

    def _get_dictionary_uncached(self, lang):
        cache_path = self._cache_dir / f"{lang}.dic"
        try:
            return TrieWrapDict(BytesTrie().load(cache_path))
        except Exception as exc:
            raise LemmaCacheUnavailable(
                f"Lemma cache unavailable for {lang!r}: {cache_path}"
            ) from exc


_lemmatizer = None
_lemma_cache_unavailable_logged = False


def get_lemmatizer():
    """Return the shared `simplemma.Lemmatizer`, built on first use.

    Uses a trie-backed dictionary (cached on disk at `settings.LEMMA_CACHE_DIR`)
    instead of simplemma's default in-memory dict, which costs ~150 MB
    resident and would OOM-kill the 256 MB production machine. When
    `settings.LEMMA_CACHE_BUILD_ALLOWED` is false, the factory never builds
    the trie itself; it only ever loads the prebuilt cache from disk.
    """
    global _lemmatizer
    if _lemmatizer is None:
        factory_cls = (
            TrieDictionaryFactory
            if settings.LEMMA_CACHE_BUILD_ALLOWED
            else PrebuiltTrieDictionaryFactory
        )
        _lemmatizer = Lemmatizer(
            lemmatization_strategy=DefaultStrategy(
                dictionary_factory=factory_cls(disk_cache_dir=settings.LEMMA_CACHE_DIR)
            )
        )
    return _lemmatizer


def _tokens(text):
    return WORD_PATTERN.findall(" ".join(str(text or "").split()))


def _article_from_noun_ending(word):
    word = word.casefold()
    if word.endswith(("chen", "lein", "ment", "um")):
        return "das"
    if word.endswith(
        ("ung", "heit", "keit", "schaft", "tion", "tät", "ik", "ie", "ur", "enz", "anz", "ei", "in")
    ):
        return "die"
    if word.endswith(("ling", "ismus", "or", "ist")):
        return "der"
    if word.endswith("us") and not word.endswith("aus"):
        return "der"
    return ""


def _article_from_context(word, context):
    tokens = _tokens(context)
    target = word.casefold()
    for index, token in enumerate(tokens):
        if token.casefold() != target or index == 0:
            continue
        preceding = tokens[index - 1].casefold()
        if preceding in {"der", "die", "das"}:
            return preceding
        if preceding in {"den", "dem", "des"}:
            return _article_from_noun_ending(word) or "der"
        if preceding in INDEFINITE_ARTICLES:
            return INDEFINITE_ARTICLES[preceding]
        if preceding in {"ein", "einem", "eines"}:
            return _article_from_noun_ending(word) or "der"
    return ""


def _parse_inflected_article(text):
    """Return (article, stem, surface_article) for a two-token article + noun
    selection, e.g. "den Antrag" or "die Kinder".

    Unlike `cards.articles.parse_leading_article`, this recognises inflected
    determiners (den, dem, des, einen, ...), but only reports a nominative
    `article` when the determiner alone makes it unambiguous; an inflected or
    case-ambiguous determiner (dem, des, ein, einer, eines, or "die" before a
    plural) yields an empty article rather than a guess.
    """
    tokens = text.split()
    if len(tokens) != 2:
        return "", "", ""
    determiner, stem = tokens
    determiner_cf = determiner.casefold()
    if determiner_cf not in INFLECTED_DETERMINERS:
        return "", "", ""
    if determiner_cf == "der":
        article = "der"
    elif determiner_cf == "das":
        article = "das"
    elif determiner_cf in {"den", "einen"}:
        article = "der"
    elif determiner_cf == "eine":
        article = "die"
    elif determiner_cf == "die":
        stem_lemma = lemmatize_german(stem)
        article = "die" if stem_lemma.casefold() == stem.casefold() else ""
    else:
        article = ""
    return article, stem, determiner_cf


ADJECTIVE_SUFFIXES = (
    "lich",
    "ig",
    "isch",
    "bar",
    "sam",
    "haft",
    "los",
    "voll",
    "arm",
    "reich",
)
PARTICIPLE_ADJECTIVE_SUFFIXES = ("enden", "endem", "endes", "ender", "ende", "end")


def _is_adjective_suffix_candidate(word):
    """A lowercase token ending in a common adjective/participle suffix.

    simplemma sometimes lemmatises a present-participle adjective (e.g.
    "vorübergehend") back to its verb infinitive ("vorübergehen"), which is
    wrong when the word was selected as an adjective, not a verb.
    """
    if not word[:1].islower():
        return False
    return word.casefold().endswith(ADJECTIVE_SUFFIXES + PARTICIPLE_ADJECTIVE_SUFFIXES)


def _participle_adjective_base(word):
    """Strip an inflected -end participle-adjective ending to its -end base.

    "vorübergehende" -> "vorübergehend"; a word already ending in "end" is
    returned unchanged.
    """
    lowered = word.casefold()
    for suffix in PARTICIPLE_ADJECTIVE_SUFFIXES:
        if lowered.endswith(suffix):
            return word[: len(word) - len(suffix) + 3]
    return word


def _likely_verb_context(word, source_form, context):
    tokens = [token.casefold() for token in _tokens(context)]
    target = word.casefold()
    subject_words = SUBJECT_PRONOUNS
    for index, token in enumerate(tokens):
        if token != target:
            continue
        previous = tokens[index - 1] if index else ""
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if previous in subject_words:
            return True
        if previous in {"habe", "haben", "hat", "ist", "sind", "war", "wurde", "wird"}:
            return True
        if index >= 2 and tokens[index - 2] in {
            "habe",
            "haben",
            "hat",
            "ist",
            "sind",
            "war",
            "wurde",
            "wird",
        }:
            return True
        if index == 0 and following in subject_words:
            return True
        if index >= 2 and tokens[index - 2] in {"der", "die", "das", "ein", "eine"}:
            return True
        if index == 0 and str(source_form or "").isupper():
            return following not in {"ist", "war", "wird"}
    return False


def _dictionary_lemma(token):
    """Return the lemma the configured dictionary holds for ``token``, or None.

    ``Lemmatizer.lemmatize`` returns unknown tokens unchanged, so it cannot
    tell a known headword from an invented one; simplemma has no public
    accessor for the dictionary, so reach through the private chain it does
    expose (the same one the tests inspect).
    """
    try:
        lookup = get_lemmatizer()._lemmatization_strategy._dictionary_lookup
    except LemmaCacheUnavailable:
        return None
    try:
        return lookup.get_lemma(token, "de")
    except LemmaCacheUnavailable:
        return None


def _participle_infinitive(word):
    """Return the infinitive behind a weak past participle, or "".

    simplemma lists many participles as headwords in their own right
    (adjective readings such as "betäubt", "enttäuscht"), so lemmatising
    them returns the participle unchanged. Rebuild the infinitive
    ("betäub-t" -> "betäuben", "verheirat-et" -> "verheiraten") and accept
    it only when the dictionary knows that form as a headword.
    """
    lowered = word.casefold()
    if not word[:1].islower() or not lowered.endswith("t"):
        return ""
    candidates = [word[:-1] + "en"]
    if lowered.endswith("et"):
        candidates.append(word[:-2] + "en")
    for candidate in candidates:
        if _dictionary_lemma(candidate) == candidate:
            return candidate
    return ""


def _participle_verb_context(word, context):
    """True when ``word`` sits where only a verb can in ``context``.

    Stricter than `_likely_verb_context`: sein is not a cue, because the
    predicative adjective ("ist verrückt") and the participle look the same
    there. A noun or object pronoun immediately before the word, or a form
    of haben/werden within two tokens, is unambiguous.
    """
    tokens = _tokens(context)
    target = word.casefold()
    for index, token in enumerate(tokens):
        if token.casefold() != target or index == 0:
            continue
        previous = tokens[index - 1]
        previous_cf = previous.casefold()
        if previous_cf in SUBJECT_PRONOUNS | OBJECT_PRONOUNS | HABEN_WERDEN_FORMS:
            return True
        if index >= 2 and tokens[index - 2].casefold() in HABEN_WERDEN_FORMS:
            return True
        if index >= 2 and previous[:1].isupper():
            # A capitalised word that is not sentence-initial is a noun: the
            # selection follows its object ("deine Neuronen betäubt").
            return True
    return False


def lemmatize_german_detailed(word):
    """Return ``(lemma, available)`` for a single German token, offline.

    ``available`` is false only when the lemma cache could not be read, in
    which case ``lemma`` is the token unchanged. See `lemmatize_german`.
    """
    token = " ".join(str(word or "").split()).strip()
    if not token or " " in token:
        return token, True
    casefolded = token.casefold()
    irregular = IRREGULAR_FORM_TO_INFINITIVE.get(casefolded)
    if irregular:
        if token[:1].isupper() and irregular[:1].islower():
            # A capitalised token is always a noun in German; a lowercase
            # irregular-verb match (e.g. "Unterschied" -> "unterscheiden",
            # "Gerät" -> "geraten") is a false positive from the verb-form
            # index, not the token's actual lemma.
            return token, True
        return irregular, True
    global _lemma_cache_unavailable_logged
    try:
        lemma = get_lemmatizer().lemmatize(token, lang="de")
    except LemmaCacheUnavailable:
        if not _lemma_cache_unavailable_logged:
            logger.warning(
                "Lemma cache unavailable; word capture will return tokens unlemmatized "
                "until the cache is rebuilt."
            )
            _lemma_cache_unavailable_logged = True
        return token, False
    # lower() keeps ß (casefold() would turn it into "ss" and misfire on Füßen).
    if "ss" in token.lower() and "ß" in lemma:
        lemma = lemma.replace("ß", "ss")
    if lemma.casefold() == casefolded:
        return token, True
    if token[:1].isupper() and lemma[:1].islower():
        return token, True
    return lemma, True


def lemmatize_german(word):
    """Return the dictionary form of a single German token, offline.

    A curated table of irregular forms wins over the statistical lemmatiser
    (``simplemma`` sometimes misses less common irregular conjugations). A
    multi-word input is returned unchanged: this is a single-token API.
    """
    return lemmatize_german_detailed(word)[0]


SEPARABLE_PARTICLES = frozenset(
    {
        "ab",
        "an",
        "auf",
        "aus",
        "bei",
        "ein",
        "fest",
        "fort",
        "her",
        "hin",
        "los",
        "mit",
        "nach",
        "vor",
        "weg",
        "weiter",
        "zu",
        "zurück",
        "zusammen",
    }
)
CLAUSE_BOUNDARY = re.compile(r"[,.!?;:]")


def _separable_infinitive(word, lemma, context):
    """Return particle + ``lemma`` when ``word``'s clause ends in a separable particle.

    Only the clause that contains ``word`` counts, and the combined form must
    be a headword in the lemma dictionary ("steht ... auf" -> "aufstehen";
    an invented "mitstehen" is rejected). The click carries no occurrence
    index, so a token repeated across clauses ("Er steht auf, und er steht
    noch.") can't be traced to the one the owner selected; returns "" rather
    than guess.
    """
    target = word.casefold()
    matching_clauses = []
    for clause in CLAUSE_BOUNDARY.split(context):
        tokens = [token.casefold() for token in _tokens(clause)]
        if target in tokens:
            matching_clauses.append(tokens)
    if len(matching_clauses) != 1:
        return ""
    tokens = matching_clauses[0]
    particle = tokens[-1]
    if particle in SEPARABLE_PARTICLES and tokens.index(target) < len(tokens) - 1:
        candidate = particle + lemma
        if _dictionary_lemma(candidate) == candidate:
            return candidate
    return ""


def suggest_word_metadata(word, context="", source_form=None, part_of_speech="", article=""):
    """Return save-ready metadata for a German word selected in a transcript."""
    word = " ".join(str(word or "").split()).strip()
    context = " ".join(str(context or "").split()).strip()
    source_form = " ".join(str(source_form or word).split()).strip()
    explicit_article, stem, surface_article = _parse_inflected_article(source_form)
    if surface_article:
        article = explicit_article
        word = stem
    article = str(article or "").strip().casefold()

    if " " in word:
        return {
            "word": word,
            "normalized_word": word,
            "article": "",
            "gender": "",
            "plural": "",
            "part_of_speech": "phrase",
            "lemmatizer_available": True,
        }

    lemma, lemmatizer_available = lemmatize_german_detailed(word)
    if surface_article and lemma[:1].islower():
        # An explicit article means `word` (the stem) is a noun, so its
        # dictionary form starts with a capital letter. `lemmatize_german`
        # only keeps the original token when the token itself was already
        # capitalised; a transcript stem can be lowercase, so capitalise it
        # here instead of trusting a lowercase lemma guess.
        lemma = word[:1].upper() + word[1:]

    if surface_article:
        is_noun = True
    elif lemma[:1].isupper():
        is_noun = True
        if article not in ARTICLE_TO_GENDER:
            if lemma.casefold() != word.casefold():
                article = _article_from_noun_ending(lemma)
            else:
                article = _article_from_context(word, context) or _article_from_noun_ending(lemma)
    else:
        is_noun = False

    is_adjective = False
    is_verb = False
    if not is_noun and _is_adjective_suffix_candidate(word):
        ends_with_participle_suffix = word.casefold().endswith(PARTICIPLE_ADJECTIVE_SUFFIXES)
        lemma_is_verb_infinitive = lemma.casefold() != word.casefold() and lemma.endswith(
            ("en", "n")
        )
        if ends_with_participle_suffix and lemma_is_verb_infinitive:
            base = _participle_adjective_base(word)
            if base[:-1].casefold() == lemma.casefold():
                lemma = base
                is_adjective = True
            else:
                # Suffix match is coincidental (e.g. "wende" ends in "ende"
                # but lemmatises to "wenden", not a "wendend" participle
                # base): an inflected verb form, not a participle adjective.
                is_verb = True
        elif (
            ends_with_participle_suffix
            and lemma.casefold() == word.casefold()
            and lemma.endswith("en")
        ):
            # simplemma considers this already a base form (e.g.
            # "vollenden", "beenden", "senden"): a genuine infinitive verb,
            # not a participle adjective.
            is_verb = True
        elif not lemma.endswith(("en", "n")):
            is_adjective = True
        else:
            # simplemma returned a verb infinitive for a word carrying an
            # adjective suffix; reject it and keep the selected token.
            lemma = word
            is_adjective = True

    if not (is_noun or is_adjective or is_verb) and lemma.casefold() == word.casefold():
        infinitive = _participle_infinitive(word)
        if infinitive and _participle_verb_context(word, context):
            lemma = infinitive
            is_verb = True

    if is_noun:
        part_of_speech = "noun"
    elif is_adjective:
        part_of_speech = "adjective"
    elif (
        is_verb
        or (lemma.casefold() != word.casefold() and lemma.endswith(("en", "n")))
        or _likely_verb_context(word, source_form, context)
    ):
        part_of_speech = "verb"
    else:
        part_of_speech = ""

    if part_of_speech == "verb":
        lemma = _separable_infinitive(word, lemma, context) or lemma

    return {
        "word": lemma,
        "normalized_word": lemma,
        "article": article,
        "gender": ARTICLE_TO_GENDER.get(article, ""),
        "plural": "",
        "part_of_speech": part_of_speech,
        "lemmatizer_available": lemmatizer_available,
    }
