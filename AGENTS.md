# Working on VRCH Visuals

- Keep existing definition IDs, provenance kinds and source bytes through the migration. Source hash changes affect saved visual matching; do not normalize JSON formatting casually.
- All current visuals are free and MIT licensed. Engine copies are excluded from that grant; preserve their notices. Never copy private documentation, credentials, private addresses or external test media here.
- Definitions live in presets/definitions; metadata lives in visuals.json. Derive runtime requirements from definitions. Do not hand-maintain a second capability registry.
- Run python3 tools/visuals.py check and python3 -m unittest discover -s tests -v before committing. Keep focused tests for export preservation and rejected input.
- Keep the complete order.json, including the six Pages-owned fixtures. Export owns only its enumerated source/catalog/thumbnail paths; it never edits those fixtures or unrelated images.
- Engine changes are one-way copies from an identified Pages commit using the explicit JS whitelist; update ENGINE_SOURCE.json and keep engine updates separate from artwork changes. Do not copy Pages Python validators or spec JSON files.
- Preserve unrelated changes and stage explicit files. Never commit virtual environments, screenshots, temporary exports or build output.
- Maintain the per-visual sections of CHANGELOG.md. Preserve version 1.0.0 for the migration baseline; later content changes require intentional versioning before release.
- Source push is not release publication, Pages synchronization or device acceptance. No release or device operation without the corresponding user authorization.
- Maintainer-only first phase; external pull requests are not accepted yet.
