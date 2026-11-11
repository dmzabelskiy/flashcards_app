# CSV Format

Required columns:

- `front`
- `back`

Optional columns:

- `example_sentence`
- `notes`
- `tags`
- `card_type`

Supported card types:

- `vocabulary`
- `translation`
- `grammar`
- `cloze`
- `custom`

Example:

```csv
front,back,example_sentence,notes,tags,card_type
der Tisch,table; masculine; plural: die Tische,Der Tisch steht im Zimmer.,noun furniture,vocabulary
Ich interessiere mich ___ Biologie.,fuer,Ich interessiere mich fuer Biologie.,preposition,grammar
```

A `vocabulary` card whose front starts with a lower-case article of the deck's language, followed by a space and at least one more word, is stored with that article set, so the article drill can use it: `der Alltag` in a German deck, `het huis` in a Dutch one. A front that ends in `.`, `!` or `?`, starts with a capitalised article, or is only an article gets none. The front is stored unchanged.

The importer strips surrounding whitespace, skips empty rows, reports invalid rows, and creates valid cards as due immediately.

A row whose front, after trimming, equals the front of a card already in the deck (archived or not), or of an earlier row of the same file, is not imported. The comparison is exact and case-sensitive: `Essen` and `essen` are different cards. The import page reports how many rows were already in the deck, so importing the same file twice adds nothing the second time. This holds in every mode.

## Card sets

Card sets that ship with the app are CSV files in this format, in `card_sets/<mode>/`. The `ES-RU` global deck starts with `card_sets/ES-RU/spanish_a2_starter.csv`: when an account opens a page that needs that deck and the deck holds no card at all (archived cards count), the file is imported into it through the importer, so nouns get their article. A deck that holds any card is never seeded, and no other mode has a starter set; the mapping from mode to file is `STARTER_SETS` in `decks/services.py`. The starter file is 100 rows copied unchanged from the full list, `spanish_a1_a2_vocabulary.csv`, which can be imported into the same deck afterwards: the 100 words already there are skipped. A missing or unreadable starter file is logged and leaves the deck empty.
