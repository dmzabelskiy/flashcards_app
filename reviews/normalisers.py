"""The answer normalisers a drill file can name.

Each takes the text a learner typed (or an accepted answer from the drill file)
and returns the form two answers are compared in.  They are the matching rules
of the drills, under the names a drill file refers to.
"""

import unicodedata


def normalise_german(value):
    value = value.strip().casefold()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    value = value.replace("ß", "ss")
    value = value.replace("(", " ").replace(")", " ")
    return " ".join(value.split())


def normalise_russian(value):
    value = value.strip().casefold().replace("ё", "е")
    return " ".join(value.strip(".,;:!?").split())


def normalise_translation(value):
    value = value.strip().casefold().replace("ё", "е")
    value = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    value = "".join(
        " " if unicodedata.category(character).startswith("P") else character for character in value
    )
    return " ".join(value.split())


def normalise_preposition(value):
    value = value.strip().casefold()
    value = value.replace("ae", "ä").replace("oe", "ö").replace("ue", "ü")
    value = value.replace("ß", "ss")
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def normalise_spanish(value):
    value = unicodedata.normalize("NFC", value).strip().casefold()
    return " ".join(value.strip("¿¡.,;:!? \t\n").split())


def normalise_exact(value):
    return value.strip().casefold()


NORMALISERS = {
    "german": normalise_german,
    "russian": normalise_russian,
    "translation": normalise_translation,
    "preposition": normalise_preposition,
    "spanish": normalise_spanish,
    "exact": normalise_exact,
}


def matches(chosen, accepted, normaliser):
    """Whether ``chosen`` equals any accepted answer once both are normalised."""
    normalised = normaliser(chosen)
    return bool(normalised and any(normalised == normaliser(answer) for answer in accepted))
