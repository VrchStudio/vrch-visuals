# VRCH Visuals

Official free visuals by VRCH: 57 editable definitions and their original thumbnails,
with catalog presentation and audio choreography. The migration baseline is 1.0.0
for every visual. No release has been published yet.

## Check and export

Use Python 3.10+ and Node.js 22:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r tools/requirements.txt
python3 tools/visuals.py check
python3 -m unittest discover -s tests -v
python3 tools/visuals.py export --output-dir /tmp/visuals-pages-export
```

`check` validates definitions with the public JavaScript contract and its bundled
schemas, recomputes JCS SHA-256, checks enabled signals and analysis kinds, catalog
references, metadata, thumbnail ownership and the pinned migration provenance kinds.
The resource descriptor schema is draft **0**; it is not a published release manifest.

`export` writes 117 owned files in the existing Pages layout: 57 source definitions,
57 thumbnails, and `selection.json`, `audio-mapping-v2.json`, `order.json`. It checks
and stages all inputs first, preserves bytes and sets file permissions to 0644.
It does not build Pages, delete its files, change its runtime, or install on devices.
To prove equivalence, export into a clean Pages checkout at the commit recorded in
`tests/migration-baseline.json`, run its `build_catalog.py`, and check for zero diff.
The six engine fixtures remain owned by Pages and keep their positions in the
complete 63-entry order.

## Repository layout

- `presets/definitions/`: original SceneDefinition files; filenames are stable.
- `visuals.json`: version, publisher, MIT license, free tier, JCS hash, provenance kind and thumbnail names, keyed by slug.
- `thumbnails/`: only images referenced by these definitions or the selection catalog.
- `catalog/`: selection, audio choreography and complete order keyed by scene ID.
- `schemas/`: metadata schema and draft resource descriptor schema.
- `engine/`: a pinned, one-way copy of already public JavaScript. `ENGINE_SOURCE.json` records the Pages revision and every file digest.
- `fixtures/`: mathematically generated audio/pose inputs and a generated test image; no private test media.
- `tests/`: positive/negative contract checks and the migration identity baseline.

The engine whitelist is six JS files. Its source is maintained in Pages. The spec
is the existing public JS bundle, not a copied spec JSON tree; no Pages Python
validator or private documentation is included. Third-party notices in the copied
files remain in force. See [LICENSE](LICENSE) for the MIT scope and engine exclusion.

Only maintainers contribute in this first phase; external pull requests are not
accepted yet. Publisher, free tier, and execution trust are separate: the 22
`user.vp-*` definitions retain their original untrusted provenance. They do not gain
execution privileges because this is an official repository.

Future work: deterministic release packaging, immutable release/index handling,
SwiftShader compilation and same-engine visual comparisons, and Pages lock/sync
integration. No premium tier, entitlement or credit system is implemented here.
