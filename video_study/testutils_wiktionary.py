"""Hand-written Wiktionary API payloads for the dictionary parser tests.

Each page holds only the markup the parsers read (language section, headings,
head and pointer templates, gloss and example lines); the wording is written
for these tests. `DE_PAGES` and `NL_PAGES` map a page title to its wikitext.
"""

import json


def tpl(name, *args):
    return "{{" + "|".join((name, *args)) + "}}"


def api_body(title, wikitext):
    """The JSON bytes of a successful `action=parse` response."""
    return json.dumps({"parse": {"title": title, "pageid": 1, "wikitext": wikitext}}).encode()


def de_section(wortart, extra="", genus="", plural="", examples=(), pointer=None, image=False):
    heading = f"=== {tpl('Wortart', wortart, 'Deutsch')}{extra} ==="
    lines = [heading]
    if genus:
        # An image parameter is followed by positional arguments (`mini`, `1`).
        picture = "\n|Bild=Beispielbild.jpg|mini|1" if image else ""
        lines.append(
            f"{{{{Deutsch Substantiv Übersicht\n|Genus={genus}{picture}"
            f"\n|Nominativ Plural={plural}\n}}}}"
        )
    if examples:
        lines += [tpl("Beispiele")] + [f":[{n}] {text}" for n, text in enumerate(examples, 1)]
    if pointer:
        lines.append(tpl("Grundformverweis " + pointer[0], pointer[1]))
    return "\n".join(lines) + "\n"


def de_page(title, *sections):
    return f"== {title} ({tpl('Sprache', 'Deutsch')}) ==\n" + "\n".join(sections)


def nl_pos(pos, head, *glosses, level=3, subheadings=()):
    """A part-of-speech section; `subheadings` are level-five headings placed
    between the head line and the gloss lines."""
    mark = "=" * level
    subs = "".join(f"====={title}=====\n\n" for title in subheadings)
    return f"{mark}{pos}{mark}\n{head}\n\n{subs}" + "".join(f"# {gloss}\n" for gloss in glosses)


def nl_ety(number, *sections):
    return f"===Etymology {number}===\n" + "\n".join(sections)


def nl_page(*sections, before=""):
    """A page whose Dutch section may follow the sections of other languages."""
    return before + "==Dutch==\n\n" + "\n".join(sections)


QUOTE = "„Ein Zitat mit Quelle.“<ref>{{Literatur|Titel=Beispiel}}</ref>"
FORM_OF = tpl("head", "nl", "verb form")
# An invented entry in another language that sorts before Dutch.
AFRIKAANS_FIRST = (
    "==Afrikaans==\n\n"
    + nl_pos("Noun", tpl("af-noun"), "an invented [[sense]] of no use here")
    + "\n"
)

DE_PAGES = {
    "Haus": de_page(
        "Haus",
        de_section(
            "Substantiv",
            ", " + tpl("n"),
            "n",
            "Häuser",
            [QUOTE, "Unser ''Haus'' hat ein rotes Dach."],
            image=True,
        ),
    ),
    "Häusern": de_page(
        "Häusern",
        de_section("Substantiv", f", {tpl('n')}, {tpl('Wortart', 'Toponym', 'Deutsch')}"),
        de_section("Deklinierte Form", pointer=("Dekl", "Haus")),
    ),
    "gegangen": de_page("gegangen", de_section("Partizip II", pointer=("Konj", "gehen"))),
    "gehen": de_page(
        "gehen",
        de_section(
            "Verb",
            ", " + tpl("unreg."),
            examples=[QUOTE, "Wir ''gehen'' am Sonntag spazieren.", "Er ist früh ''gegangen.''"],
        ),
    ),
    "aufstehen": de_page(
        "aufstehen",
        de_section(
            "Verb", ", " + tpl("unreg."), examples=["Sie ist heute zeitig ''aufgestanden.''"]
        ),
    ),
    "schnell": de_page(
        "schnell",
        de_section("Adjektiv", examples=["Wir nehmen den ''schnelleren'' Weg."]),
        de_section("Konjugierte Form", pointer=("Konj", "schnellen")),
    ),
}

NL_PAGES = {
    "huis": nl_page(
        nl_ety(
            1,
            nl_pos(
                "Noun",
                tpl("nl-noun", "n", "-zen", "+"),
                "a [[dwelling]], home; [[abode]]",
                "a [[family]] line, such as a dynasty",
                "a [[chamber]] of a parliament",
                "a protective [[casing]]",
                level=4,
                subheadings=("Declension",),
            ),
        ),
        nl_ety(
            2, nl_pos("Verb", FORM_OF, tpl("infl of", "nl", "huizen", "", "bare-verb"), level=4)
        ),
    ),
    "huizen": nl_page(
        nl_ety(1, nl_pos("Verb", tpl("nl-verb"), "to [[reside]] somewhere", level=4)),
        nl_ety(
            2,
            nl_pos("Noun", tpl("head", "nl", "noun form"), tpl("plural of", "nl", "huis"), level=4),
        ),
    ),
    "boom": nl_page(
        nl_ety(1, nl_pos("Noun", tpl("nl-noun", "m", "-en", "+,-ken"), "[[tree]]", level=4)),
        nl_ety(2, nl_pos("Noun", tpl("nl-noun", "m", "-s", "+"), "a sudden [[surge]]", level=4)),
        before=AFRIKAANS_FIRST,
    ),
    "fiets": nl_page(
        nl_ety(1, nl_pos("Noun", tpl("nl-noun", "f", "-en", "+"), "[[bicycle]]", level=4)),
        nl_ety(
            2, nl_pos("Verb", FORM_OF, tpl("infl of", "nl", "fietsen", "", "bare-verb"), level=4)
        ),
    ),
    "kind": nl_page(
        nl_pos(
            "Noun",
            tpl("nl-noun", "n", "-eren,kinders", "+,-ertje"),
            "[[child]], [[kid]], a young person",
            "a [[son]] or [[daughter]], of any age",
        )
    ),
    "bel": nl_page(
        nl_ety(1, nl_pos("Noun", tpl("nl-noun", "f", "-en", "+"), "[[bell]]", level=4)),
        nl_ety(
            2, nl_pos("Verb", FORM_OF, tpl("infl of", "nl", "bellen", "", "bare-verb"), level=4)
        ),
    ),
    "bellen": nl_page(
        nl_ety(
            1,
            nl_pos(
                "Verb",
                tpl("nl-verb"),
                tpl("lb", "nl", "intransitive") + " to [[ring]] a bell",
                tpl("lb", "nl", "transitive") + " to [[telephone]]",
                level=4,
            ),
        ),
        nl_ety(
            2,
            nl_pos("Noun", tpl("head", "nl", "noun form"), tpl("plural of", "nl", "bel"), level=4),
        ),
    ),
    "opbellen": nl_page(nl_pos("Verb", tpl("nl-verb"), "to [[telephone]] someone (by phone)")),
    "lopen": nl_page(
        nl_ety(
            1,
            nl_pos(
                "Verb",
                tpl("nl-verb"),
                tpl("lb", "nl", "intransitive") + " to [[walk]]",
                tpl("lb", "nl", "intransitive") + " to [[run]]",
                tpl("lb", "nl", "intransitive") + " to [[stretch]], {{gloss|to extend}}",
                tpl("lb", "nl", "intransitive") + " to [[leak]]",
                level=4,
                subheadings=("Conjugation", "Derived terms"),
            ),
        ),
        nl_ety(
            2,
            nl_pos("Noun", tpl("head", "nl", "noun form"), tpl("plural of", "nl", "loop"), level=4),
        ),
    ),
    "liep": nl_page(
        nl_pos("Verb", FORM_OF, tpl("infl of", "nl", "lopen", "", "s", "past", "ind")),
        "==Serbo-Croatian==\n\n"
        + nl_pos("Adjective", tpl("sh-adj"), "{{obsolete spelling of|sh|lijep}}"),
    ),
    "gelopen": nl_page(
        nl_ety(
            1,
            nl_pos(
                "Verb",
                tpl("nl-verb"),
                tpl("lb", "nl", "obsolete") + " to go on [[foot]]",
                tpl("lb", "nl", "obsolete") + " to [[hurry]] along",
                level=4,
            ),
        ),
        nl_ety(
            2,
            nl_pos(
                "Participle",
                tpl("nl-past-ptc"),
                tpl("past participle of", "nl", "lopen"),
                tpl("past participle of", "nl", "gelopen"),
                level=4,
            ),
        ),
    ),
    "kinderen": nl_page(
        nl_pos("Noun", tpl("head", "nl", "noun form"), tpl("plural of", "nl", "kind"))
    ),
    "meisje": nl_page(
        nl_pos("Noun", tpl("nl-noun-dim"), tpl("diminutive of", "nl", "meid")),
        nl_pos(
            "Noun",
            tpl("nl-noun-dim-tant"),
            "[[girl]]\n#: "
            + tpl("uxi", "nl", "Het '''meisje''' fietst.", "The '''girl''' cycles."),
            "[[girlfriend]]",
        ),
    ),
    "mooi": nl_page(
        nl_pos(
            "Adjective",
            tpl("nl-adj"),
            "pleasing to the [[eye]], [[attractive]]",
            "[[pleasant]] or [[agreeable]]",
        ),
        before=AFRIKAANS_FIRST,
    ),
    "snel": nl_page(
        nl_ety(
            1,
            nl_pos(
                "Adjective", tpl("nl-adj", "sneller"), "covering ground in little [[time]]", level=4
            ),
        ),
        nl_ety(
            2, nl_pos("Verb", FORM_OF, tpl("infl of", "nl", "snellen", "", "bare-verb"), level=4)
        ),
    ),
    # A proper noun is not an ordinary entry, so this page has nothing to parse.
    "Amsterdam": nl_page(
        nl_pos("Proper noun", tpl("nl-proper noun", "?"), "a [[city]] in the Netherlands")
    )
    + "\n==Afrikaans==\n\n"
    + nl_pos("Proper noun", tpl("af-proper noun"), "a [[city]]"),
}
