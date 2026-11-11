import re
from dataclasses import dataclass

TRANSLATION_SEPARATOR_CHARS = ";,\n/"
PARENTHETICAL_RE = re.compile(r"\([^()]*\)")
EDGE_PUNCTUATION_RE = re.compile(r"^[\s\"'“”‘’.,;:!?()]+|[\s\"'“”‘’.,;:!?()]+$")
BRACKETED_TAG_RE = re.compile(r"\[[^\]\n]{1,30}\]")
CYRILLIC_WORD_RE = re.compile(r"^[а-яё]+$")


@dataclass(frozen=True)
class AnswerCheck:
    is_correct: bool
    had_typo: bool = False
    matched_answer: str = ""
    different_form: bool = False


def normalize_answer(value):
    value = EDGE_PUNCTUATION_RE.sub("", value or "")
    return re.sub(r"\s+", " ", value).strip().casefold()


UMLAUT_FOLDS = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


def normalize_german_answer(value):
    normalized = normalize_answer(value)
    for umlaut, folded in UMLAUT_FOLDS:
        normalized = normalized.replace(umlaut, folded)
    return normalized


def _split_top_level(text, separator_chars):
    parts = []
    current = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(depth - 1, 0)
            current.append(char)
        elif depth == 0 and char in separator_chars:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _strip_parenthetical_notes(text):
    stripped = PARENTHETICAL_RE.sub("", text)
    return re.sub(r"\s+", " ", stripped).strip()


def clean_example_sentence(value):
    without_tags = BRACKETED_TAG_RE.sub("", value or "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in without_tags.splitlines()]
    return "\n".join(lines).strip()


def accepted_translations(back):
    text = back or ""
    options = [text, *_split_top_level(text, TRANSLATION_SEPARATOR_CHARS)]

    accepted = []
    seen = set()
    for raw_option in options:
        option = raw_option.strip()
        normalized = normalize_answer(option)
        if normalized and normalized not in seen:
            accepted.append(option)
            seen.add(normalized)

        without_parens = _strip_parenthetical_notes(option)
        if without_parens != option:
            normalized_stripped = normalize_answer(without_parens)
            if normalized_stripped and normalized_stripped not in seen:
                accepted.append(without_parens)
                seen.add(normalized_stripped)

    return accepted


def _is_one_replacement(left, right):
    if len(left) != len(right):
        return False
    differences = sum(
        1 for left_char, right_char in zip(left, right, strict=True) if left_char != right_char
    )
    return differences == 1


def _is_adjacent_swap(left, right):
    if len(left) != len(right):
        return False
    differences = [
        index
        for index, (left_char, right_char) in enumerate(zip(left, right, strict=True))
        if left_char != right_char
    ]
    if len(differences) != 2:
        return False
    first, second = differences
    return second == first + 1 and left[first] == right[second] and left[second] == right[first]


def _is_allowed_typo(answer, candidate):
    return _is_one_replacement(answer, candidate) or _is_adjacent_swap(answer, candidate)


REFLEXIVE_SUFFIXES = ("ся", "сь")
VERB_ENDINGS = (
    "ывать",
    "ивать",
    "нуть",
    "вать",
    "ать",
    "ять",
    "ить",
    "еть",
    "уть",
    "ыть",
    "оть",
)
ADJECTIVE_ENDINGS = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие")


def _levenshtein(left, right):
    if left == right:
        return 0
    previous_row = list(range(len(right) + 1))
    for row_index, left_char in enumerate(left, start=1):
        current_row = [row_index]
        for column_index, right_char in enumerate(right, start=1):
            current_row.append(
                min(
                    current_row[column_index - 1] + 1,
                    previous_row[column_index] + 1,
                    previous_row[column_index - 1] + (left_char != right_char),
                )
            )
        previous_row = current_row
    return previous_row[-1]


def _strip_reflexive(word):
    for suffix in REFLEXIVE_SUFFIXES:
        if word.endswith(suffix):
            return word[: -len(suffix)], True
    return word, False


def _classify_word_form(word):
    stem, is_reflexive = _strip_reflexive(word)
    if stem.endswith(("ть", "ти")):
        for ending in sorted(VERB_ENDINGS, key=len, reverse=True):
            if stem.endswith(ending):
                return "verb", stem[: -len(ending)], is_reflexive
    for ending in ADJECTIVE_ENDINGS:
        if stem.endswith(ending):
            return "adjective", stem[: -len(ending)], is_reflexive
    if stem.endswith(("о", "е")):
        return "adverb", stem[:-1], is_reflexive
    return None


def _word_classes_compatible(left_class, right_class):
    if left_class == right_class == "verb":
        return True
    if left_class == right_class == "adjective":
        return True
    return {left_class, right_class} == {"adjective", "adverb"}


def _different_form_match(normalized_answer, normalized_candidate):
    answer_word = normalized_answer.replace("ё", "е")
    candidate_word = normalized_candidate.replace("ё", "е")
    if not CYRILLIC_WORD_RE.fullmatch(answer_word) or not CYRILLIC_WORD_RE.fullmatch(
        candidate_word
    ):
        return False

    answer_form = _classify_word_form(answer_word)
    candidate_form = _classify_word_form(candidate_word)
    if answer_form is None or candidate_form is None:
        return False

    answer_class, answer_stem, answer_reflexive = answer_form
    candidate_class, candidate_stem, candidate_reflexive = candidate_form
    if answer_reflexive != candidate_reflexive:
        return False
    if not _word_classes_compatible(answer_class, candidate_class):
        return False
    if len(answer_stem) < 4 or len(candidate_stem) < 4:
        return False
    return _levenshtein(answer_stem, candidate_stem) <= 2


def _check_against_candidates(answer, candidates, normalizer, allow_different_form=False):
    normalized_answer = normalizer(answer)
    if not normalized_answer:
        return AnswerCheck(is_correct=False)

    for candidate in candidates:
        normalized_candidate = normalizer(candidate)
        if normalized_answer == normalized_candidate:
            return AnswerCheck(is_correct=True, matched_answer=candidate.strip())

    for candidate in candidates:
        normalized_candidate = normalizer(candidate)
        if _is_allowed_typo(normalized_answer, normalized_candidate):
            return AnswerCheck(is_correct=True, had_typo=True, matched_answer=candidate.strip())

    if allow_different_form:
        for candidate in candidates:
            normalized_candidate = normalizer(candidate)
            if _different_form_match(normalized_answer, normalized_candidate):
                return AnswerCheck(
                    is_correct=True, different_form=True, matched_answer=candidate.strip()
                )

    return AnswerCheck(is_correct=False)


def check_answer(answer, back):
    return _check_against_candidates(
        answer, accepted_translations(back), normalize_answer, allow_different_form=True
    )


def is_correct_answer(answer, back):
    return check_answer(answer, back).is_correct


def accepted_german_forms(card):
    candidates = [card.display_front, card.front, card.noun_stem]

    accepted = []
    seen = set()
    for candidate in candidates:
        normalized = normalize_german_answer(candidate)
        if normalized and normalized not in seen:
            accepted.append(candidate.strip())
            seen.add(normalized)
    return accepted


def check_german_answer(answer, card):
    return _check_against_candidates(answer, accepted_german_forms(card), normalize_german_answer)
