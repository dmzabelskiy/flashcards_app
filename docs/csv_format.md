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

The importer strips surrounding whitespace, skips empty rows, reports invalid rows, and creates valid cards as due immediately.

