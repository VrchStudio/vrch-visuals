# VRCH Visuals

Official free visuals by VRCH: 57 editable definitions and their original thumbnails,
with catalog presentation and audio choreography. The migration baseline is 1.0.0
for every visual. Each visual is an independently described resource; bundle publication is a delivery convenience.

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
Published v0 visual descriptors remain immutable. The v1 schema can describe a work separately from its rendering definition.

`export` writes 117 owned files in the existing Pages layout: 57 source definitions,
57 thumbnails, and `selection.json`, `audio-mapping-v2.json`, `order.json`. It checks
and stages all inputs first, preserves bytes and sets file permissions to 0644.
It does not build Pages, delete its files, change its runtime, or install on devices.
To prove equivalence, export into a clean Pages checkout at the commit recorded in
`tests/migration-baseline.json`, run its `build_catalog.py`, and check for zero diff.
The six engine fixtures remain owned by Pages and keep their positions in the
complete 63-entry order.

## Repository layout

- `visuals/<slug>/definition.json`: original SceneDefinition bytes.
- `visuals/<slug>/visual.json`: that resource's identity, version, publisher, tier, license, hash, provenance kind and thumbnail names.
- `visuals/<slug>/CHANGELOG.md`: that resource's version history.
- `visuals/<slug>/resource.json` in release bundles: generated complete resource descriptor, including every required file and digest.
- `thumbnails/`: only images referenced by these definitions or the selection catalog.
- `catalog/`: selection, audio choreography and complete order keyed by scene ID.
- `schemas/`: metadata schema and draft resource descriptor schema.
- `engine/`: a pinned, one-way copy of already public JavaScript. `ENGINE_SOURCE.json` records the Pages revision and every file digest.
- `fixtures/`: mathematically generated audio/pose inputs and a generated test image; no private test media.
- `tests/`: positive/negative contract checks and the migration identity baseline.

The engine whitelist is six JS files. `python3 tools/visuals.py engine-sync --pages-root <Pages-checkout> --ref <commit>` reports byte differences; add `--write` for an explicitly intended one-way refresh and commit it separately from artwork changes. Its source is maintained in Pages. The spec
is the existing public JS bundle, not a copied spec JSON tree; no Pages Python
validator or private documentation is included. Third-party notices in the copied
files remain in force. See [LICENSE](LICENSE) for the MIT scope and engine exclusion.

Only maintainers contribute in this first phase; external pull requests are not
accepted yet. Publisher, free tier, and execution trust are separate: the 22
`user.vp-*` definitions retain their original untrusted provenance. They do not gain
execution privileges because this is an official repository.

## Portable visuals

Beginning with 1.1.0, each release lists the individual .visual downloads alongside
the full library ZIP. Each single includes its original files, descriptor and license.
Given an official release manifest, compare a source checkout for that release with
a projection from its published archive:

```sh
python3 tools/visuals.py pack mosaic --release release.json --output-dir /tmp/from-source
python3 tools/visuals.py pack-from-bundle mosaic --release release.json --archive vrch-visuals-1.0.2.zip --output-dir /tmp/from-bundle
python3 tools/visuals.py verify /tmp/from-source/vrch.mosaic-1.0.1.visual --release release.json
```

The archive filename uses the work's version. The independent JavaScript reader checks
the deterministic ZIP layout, CRC, descriptor, definition identity and portable digest.
For offline candidate builds, pass `--history-dir` with official manifests and archives
in version-named subdirectories. A local `build --preview` can exercise richer v1 works;
the publishing workflow applies the current consumer compatibility checks.

## Verify and publish

```sh
npm ci
npx playwright install chromium
node tests/render_compare.cjs
python3 tools/visuals.py build --version 1.0.0
python3 tools/visuals.py extract pulse-ridges --archive dist/1.0.0/vrch-visuals-1.0.0.zip --output-dir /tmp/pulse-ridges
# After committing and pushing verified source, explicitly authorized publication:
python3 tools/visuals.py release --version 1.0.0
```

The renderer pins Playwright/Chromium, uses SwiftShader at the fixture recipe's
resolution, executes main/frame/coarse, warms 120 frames and compares fixed samples
under identical clock, seeded randomness, image, audio and synthetic tracking input.
First publication compares all 57 works against the pinned migration source. Later
runs compile every current work and compare hash-changed definitions with the latest
published source in the same engine. Known-change and bad-shader controls exercise the
checker. Portability lint detects the documented floating-point bit decoding and
explicit negative/unguarded trigonometric pow-base patterns; it is not a GLSL range prover.
Device performance remains a separate check.

ZIP entries use sorted paths, a fixed timestamp, no compression and mode 0644.
Per-resource descriptors retain their first publication commit. The index keeps all
bundle and resource versions. Same-version content changes are rejected; unchanged
resources reuse their descriptors. Each extracted visual includes its definition,
metadata, changelog, referenced thumbnails, resource descriptor and license, with
bundle-relative paths preserved. It requires neither the global catalog nor another
visual. Source definitions retain their original logical Pages thumbnail references;
resource consumers resolve actual files through resource.json.

The serialized Release workflow validates first, verifies draft assets, publishes,
checks anonymous downloads, and only then updates index/stable.json on latest main.
Retrying identical content is safe; published missing or different assets are refused,
and an older retry cannot replace a newer latest entry. Engine/tools/tests/index do not
enter the content bundle. Pages installation is a separate lock/sync operation.

No premium tier, entitlement or credit system is implemented here.
