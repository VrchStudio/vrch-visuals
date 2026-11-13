#!/usr/bin/env python3
"""Check the free visual library or export its owned sources into a Pages layout."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
PAGES_PRESETS = 'skills/vrch-shader-editor-v2/presets'
ENGINE_FILES = frozenset(('three.min.js', 'vrch_pages_scene_spec.js', 'vrch_pages_scene_contract.js',
                          'vrch_pages_scene_signal_bus.js', 'vrch_pages_scene_source_binding.js',
                          'vrch_pages_scene_runtime.js'))


def read(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'{path}: duplicate JSON key {key}')
            value[key] = item
        return value
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_file(root, relative):
    path = PurePosixPath(relative)
    require(not path.is_absolute() and '..' not in path.parts and '\\' not in relative, 'Unsafe path: ' + relative)
    target = root / relative
    require(target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(root.resolve()), 'Missing or unsafe file: ' + relative)
    return target


def owned_files(root=ROOT):
    """The only public-library files exported; Pages' six fixtures and unused images stay untouched."""
    files = {f'presets/definitions/{p.name}': f'{PAGES_PRESETS}/definitions/{p.name}'
             for p in sorted((root / 'presets/definitions').glob('*.definition.json'))}
    files.update({f'catalog/{name}': f'{PAGES_PRESETS}/{name}'
                  for name in ('selection.json', 'audio-mapping-v2.json', 'order.json')})
    files.update({f'thumbnails/{p.name}': f'static/scenes/v1/thumbnails/{p.name}'
                  for p in sorted((root / 'thumbnails').iterdir()) if p.is_file()})
    return files


def check(root=ROOT):
    engine = read(root / 'engine/ENGINE_SOURCE.json')
    require(set(engine['files']) == ENGINE_FILES, 'Engine whitelist changed')
    require({p.name for p in (root / 'engine').iterdir()} == ENGINE_FILES | {'ENGINE_SOURCE.json'}, 'Unexpected engine files')
    for name, item in engine['files'].items():
        require(sha(local_file(root, 'engine/' + name)) == item['sha256'], 'Engine digest mismatch: ' + name)
    metadata = read(root / 'visuals.json')
    Draft202012Validator(read(root / 'schemas/visuals.schema.json')).validate(metadata)
    baseline = read(root / 'tests/migration-baseline.json')
    definitions = {}
    for p in sorted((root / 'presets/definitions').iterdir()):
        require(p.name.endswith('.definition.json'), 'Unexpected definition file: ' + p.name)
        local_file(root, 'presets/definitions/' + p.name)
        definitions[p.name.removesuffix('.definition.json')] = read(p)
    require(set(definitions) == set(metadata['visuals']), 'Definition/metadata set differs')
    result = subprocess.run(['node', str(root / 'tools/check_engine.cjs')], input=json.dumps(list(definitions.values())), text=True, capture_output=True)
    require(result.returncode == 0, result.stderr.strip())
    engine_result = json.loads(result.stdout)
    checked = {row['id']: row for row in engine_result['results']}
    schemas = engine_result['schemas']
    registry = Registry().with_resources((value['$id'], Resource.from_contents(value)) for value in schemas.values())
    validator = Draft202012Validator(schemas['scene-definition.schema.json'], registry=registry, format_checker=FormatChecker())
    selection = read(root / 'catalog/selection.json')
    order = read(root / 'catalog/order.json')
    audio = read(root / 'catalog/audio-mapping-v2.json')
    ids = {d['scene_id'] for d in definitions.values()}
    require(len(ids) == len(definitions), 'Duplicate scene_id')
    retained = set(baseline['pages_retained_ids'])
    require(not ids & retained and set(order) == ids | retained, 'Order must cover the library and all six Pages fixtures')
    require(all(type(i) is int for i in order.values()) and sorted(order.values()) == list(range(len(order))), 'Order positions must be unique and contiguous')
    collections = [item['id'] for item in selection['collections']]
    groups = [item['id'] for item in selection['groups']]
    families = [item['id'] for item in selection['families']]
    require(len(collections) == len(set(collections)) and len(groups) == len(set(groups)) and len(families) == len(set(families)), 'Duplicate selection grouping')
    selected = set()
    thumbnail_refs = {sid: set() for sid in ids}
    for item in selection['items']:
        sid = item['scene_id']
        require(sid in ids and sid not in selected, 'Unknown/duplicate selected visual: ' + sid)
        selected.add(sid)
        require(item['collection'] in collections, 'Unknown collection')
        require('group' not in item or item['group'] in groups, 'Unknown group')
        require('family' not in item or item['family'] in families, 'Unknown family')
        if item.get('thumbnail'):
            thumbnail_refs[sid].add(item['thumbnail'])
    expected_thumbnails = set()
    by_id = {d['scene_id']: d for d in definitions.values()}
    for slug, definition in definitions.items():
        validator.validate(definition)
        meta = metadata['visuals'][slug]
        sid = definition['scene_id']
        require(meta['id'] == sid, slug + ': metadata ID differs')
        require(meta['content_hash'] == checked[sid]['content_hash'], slug + ': JCS hash differs')
        kind = definition['provenance']['kind']
        require(meta['provenance']['kind'] == kind, slug + ': provenance differs from metadata')
        require(sid not in baseline['visuals'] or baseline['visuals'][sid]['provenance_kind'] == kind, slug + ': pinned provenance changed')
        if definition['catalog']['thumbnail_ref']:
            thumbnail_refs[sid].add(definition['catalog']['thumbnail_ref'])
        names = set()
        for ref in thumbnail_refs[sid]:
            require(ref.startswith('scenes/v1/thumbnails/'), slug + ': thumbnail must use the Pages path')
            name = ref.removeprefix('scenes/v1/thumbnails/')
            require('/' not in name, 'Thumbnail must have a flat filename')
            local_file(root, 'thumbnails/' + name)
            names.add(name)
        require(names == set(meta['thumbnails']), slug + ': thumbnail metadata differs')
        expected_thumbnails.update(names)
    require(expected_thumbnails == {p.name for p in (root / 'thumbnails').iterdir()}, 'Unreferenced or missing thumbnails')
    require(audio['schema_version'] == audio['audio_contract_version'] == 2, 'Unsupported audio choreography')
    audio_ids = set()
    expected_audio = {sid for sid, d in by_id.items() if 'product-baseline' in d['catalog']['roles'] and any(s['id'].startswith('audio.') for s in d['signals'])}
    for row in audio['entries']:
        sid = row['scene_id']
        require(sid in ids and sid not in audio_ids, 'Unknown/duplicate choreography visual')
        audio_ids.add(sid)
        definition = by_id[sid]
        require([s for s in definition['signals'] if s['id'].startswith('audio.')] == [audio['signal_definitions'][key] for key in row['signal_ids']], sid + ': choreography signals differ')
        require([b for b in definition['default_bindings'] if b['signal_id'].startswith('audio.')] == row['bindings'], sid + ': choreography bindings differ')
        require(len(row['simple_controls']) <= 4, sid + ': too many simple controls')
    require(audio_ids == expected_audio, 'Audio choreography coverage differs')
    descriptor_schema = read(root / 'schemas/resource-descriptor.v0.schema.json')
    Draft202012Validator.check_schema(descriptor_schema)
    for slug, definition in definitions.items():
        meta = metadata['visuals'][slug]
        files = [('definition', f'presets/definitions/{slug}.definition.json')] + [('thumbnail', 'thumbnails/' + name) for name in meta['thumbnails']]
        descriptor = {'schema_version': 0, 'kind': 'visual', 'id': meta['id'], 'publisher': meta['publisher'], 'name': definition['title'], 'version': meta['version'], 'tier': meta['tier'], 'license': meta['license'], 'digest': {'algorithm': 'jcs-sha256', 'value': meta['content_hash'].removeprefix('sha256:')}, 'requires': checked[meta['id']]['requires'], 'files': [{'role': role, 'path': name, 'sha256': sha(root / name), 'size': (root / name).stat().st_size} for role, name in files], 'provenance': {'repository': 'VrchStudio/vrch-visuals', 'derived_from': []}, 'signatures': []}
        Draft202012Validator(descriptor_schema).validate(descriptor)
    return {'definitions': len(definitions), 'thumbnails': len(expected_thumbnails), 'order': len(order), 'selected': len(selected), 'choreography': len(audio_ids), 'export_files': len(owned_files(root))}


def export(destination, root=ROOT):
    result = check(root)
    destination = destination.resolve()
    require(not destination.is_relative_to(root.resolve()) and not root.resolve().is_relative_to(destination), 'Export destination overlaps the source repository')
    # Validate and stage all bytes before touching the destination. Export never deletes Pages-owned files.
    with tempfile.TemporaryDirectory(prefix='vrch-visuals-export-') as temporary:
        staged = Path(temporary)
        for source, target in owned_files(root).items():
            path = staged / target
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_file(root, source), path)
            path.chmod(0o644)
        for path in staged.rglob('*'):
            if path.is_file():
                target = destination / path.relative_to(staged)
                require(not target.is_symlink() and target.resolve().is_relative_to(destination), 'Unsafe export target: ' + str(target))
        for path in staged.rglob('*'):
            if path.is_file():
                target = destination / path.relative_to(staged)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                target.chmod(0o644)
    return dict(result, destination=str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    command = commands.add_parser('export')
    command.add_argument('--output-dir', required=True, type=Path, help='Pages root or empty staging directory')
    args = parser.parse_args()
    try:
        result = check() if args.command == 'check' else export(args.output_dir)
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(json.dumps(dict(ok=True, **result), indent=2))


if __name__ == '__main__':
    main()
