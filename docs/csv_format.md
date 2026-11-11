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

