"""Portable files in an official .visual and their deterministic archive identity."""
import hashlib
import io
import json
import re
import stat
import tempfile
import zipfile
from pathlib import Path

from visuals import require

SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
VISUAL_ID = re.compile(r'(?:vrch|user|local)\.[a-z0-9][a-z0-9.-]{0,62}')
MAX_SIZE = 64 * 1024 * 1024
MAX_ENTRY = 32 * 1024 * 1024
MAX_COUNT = 512


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stamp(data):
    return {'size': len(data), 'sha256': sha(data)}


def valid_path(name):
    parts = name.split('/')
    require(not any(c in name for c in ('\\', ':')) and not re.search(r'[\x00-\x1f\x7f]', name)
            and len(parts) <= 6 and all(p not in ('', '.', '..') for p in parts),
            'Unsafe portable path: ' + repr(name))
    return name


def portable_path(name, slug):
    require(bool(SLUG.fullmatch(slug)), 'Invalid visual slug')
    prefix = f'visuals/{slug}/'
    if name.startswith(prefix):
        return valid_path(name[len(prefix):])
    require(name == 'LICENSE' or re.fullmatch(r'thumbnails/[^/]+', name),
            'Descriptor contains a path outside its resource: ' + name)
    return valid_path(name)


def package_digest(entries):
    digest = hashlib.sha256()
    for name, data in sorted(entries.items()):
        if name != 'resource.json' and not name.startswith('signatures/'):
            digest.update(f'{name}\0{sha(data)}\0{len(data)}\n'.encode('utf-8'))
    return digest.hexdigest()


def check_limits(entries):
    require(len(entries) <= MAX_COUNT and sum(map(len, entries.values())) <= MAX_SIZE, 'Package too large')
    require(len({name.casefold() for name in entries}) == len(entries), 'Duplicate portable path')
    for name, data in entries.items():
        valid_path(name)
        require(len(data) <= (256 * 1024 if name.endswith('.json') else MAX_ENTRY), 'Entry too large: ' + name)


def zip_bytes(entries):
    check_limits(entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
    try:
        temporary.write_bytes(data)
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def project(slug, descriptor, raw_descriptor, read_file, license_ref):
    """read_file may read the checkout or an independently verified release archive."""
    require(json.loads(raw_descriptor) == descriptor, 'Descriptor differs from release manifest')
    entries = {'resource.json': raw_descriptor}
    version = descriptor['schema_version']
    require(version in (0, 1) and descriptor['kind'] == 'visual', 'Unsupported resource descriptor')
    for item in descriptor['files']:
        name = portable_path(item['path'], slug)
        require(name not in entries, 'Duplicate descriptor path: ' + name)
        data = read_file(item['path'])
        require(stamp(data) == {k: item[k] for k in ('size', 'sha256')}, 'Descriptor file differs: ' + name)
        entries[name] = data
    if version == 0:
        require(license_ref is not None, 'reference_unavailable: shared LICENSE')
        require('LICENSE' not in entries, 'Unexpected v0 license entry')
        entries['LICENSE'] = read_file('LICENSE')
        require(stamp(entries['LICENSE']) == license_ref, 'Shared LICENSE differs from release')
    require({'visual.json', 'definition.json', 'CHANGELOG.md', 'LICENSE'} <= entries.keys(), 'Incomplete visual')
    meta = json.loads(entries['visual.json'])
    definition = json.loads(entries['definition.json'])
    require(meta['schema_version'] == version, 'Metadata and descriptor schema differ')
    identity = meta['id'] if version == 0 else meta['visual_id']
    require(VISUAL_ID.fullmatch(identity) and len(identity) <= 64 and identity == descriptor['id']
            and meta['version'] == descriptor['version'], 'Visual identity differs')
    require(meta['publisher'] == descriptor['publisher'] and meta['license'] == descriptor['license']
            and meta['tier'] == descriptor['tier'] and meta['provenance']['kind'] == definition['provenance']['kind'],
            'Visual metadata differs')
    require(descriptor['digest'] == {'algorithm': 'jcs-sha256', 'value': meta['content_hash'].removeprefix('sha256:')},
            'Definition digest differs')
    thumbnails = {'thumbnails/' + name for name in meta['thumbnails']}
    require(thumbnails <= entries.keys(), 'Missing listed thumbnail')
    if version == 0:
        require(identity == definition['scene_id'] and set(entries) == thumbnails | {
            'definition.json', 'visual.json', 'resource.json', 'CHANGELOG.md', 'LICENSE'}, 'Invalid v0 collection')
    else:
        require(descriptor['program']['scene_id'] == definition['scene_id'] and
                descriptor['program']['content_hash'] == meta['content_hash'] and
                descriptor['package_digest'] == package_digest(entries), 'Invalid program or package digest')
    check_limits(entries)
    return entries


def slug_for(descriptor):
    paths = [f['path'] for f in descriptor['files'] if f['role'] == 'definition']
    match = re.fullmatch(r'visuals/([^/]+)/definition\.json', paths[0]) if len(paths) == 1 else None
    require(match and SLUG.fullmatch(match[1]), 'Invalid definition location')
    return match[1]


def resource_map(release):
    by_slug = {slug_for(item): item for item in release['resources']}
    require(len(by_slug) == len(release['resources']), 'Duplicate resource slug')
    return by_slug


def from_source(root, release, slug, descriptor):
    from releases import encoded
    from visuals import local_file
    raw = encoded(descriptor)
    require(stamp(raw) == release['files'][f'visuals/{slug}/resource.json'], 'Historical descriptor bytes differ')
    return project(slug, descriptor, raw, lambda name: local_file(root, name).read_bytes(), release['files']['LICENSE'])


def from_archive(archive, release, slug, descriptor):
    raw = archive.read(f'visuals/{slug}/resource.json')
    require(stamp(raw) == release['files'][f'visuals/{slug}/resource.json'], 'Archived descriptor bytes differ')
    return project(slug, descriptor, raw, archive.read, release['files']['LICENSE'])


def attachments(folder, root, release, bundle):
    """Build source and bundle projections independently; do not publish a partial set."""
    from releases import REPOSITORY
    downloads = {}
    with zipfile.ZipFile(bundle) as archive:
        for slug, resource in resource_map(release).items():
            source = from_source(root, release, slug, resource)
            data = zip_bytes(source)
            other = from_archive(archive, release, slug, resource)
            require(zip_bytes(other) == data, 'Source and archive projections differ: ' + slug)
            name = f'{resource["id"]}-{resource["version"]}.visual'
            atomic_write(folder / name, data)
            downloads[resource['id']] = {
                'version': resource['version'],
                'url': f'https://github.com/{REPOSITORY}/releases/download/{release["tag"]}/{name}',
                **stamp(data), 'package_digest': package_digest(source),
            }
    return downloads


SECTIONS = {'look':'look.json', 'treatment':'treatment.json', 'controls':'controls.json', 'media':'media/index.json'}


def v1_files(root, slug):
    from visuals import local_file
    base = root/'visuals'/slug
    roles = {'definition.json':'definition', 'visual.json':'metadata', 'CHANGELOG.md':'changelog', 'LICENSE':'license',
             **{name:section for section,name in SECTIONS.items()}}
    files = []
    for path in sorted(base.rglob('*')):
        require(not path.is_symlink(), 'Symlink in visual source')
        if path.is_dir():
            continue
        relative = valid_path(path.relative_to(base).as_posix())
        name = path.relative_to(root).as_posix()
        role = roles.get(relative, {'thumbnails':'thumbnail','media':'media'}.get(relative.split('/')[0], 'cargo'))
        files.append({'role':role, 'path':name, **stamp(local_file(root,name).read_bytes())})
    if not (base/'LICENSE').exists():
        files.append({'role':'license', 'path':'LICENSE', **stamp(local_file(root,'LICENSE').read_bytes())})
    return files


def check_sections(root, slug, meta, definition):
    from visuals import Draft202012Validator, local_file, read
    folder = root/'visuals'/slug
    params = {param['key'] for param in definition['parameters']}
    for name in meta['thumbnails']:
        local_file(folder, valid_path('thumbnails/'+name))
    for section, name in SECTIONS.items():
        if not (folder/name).exists():
            continue
        doc = read(local_file(folder,name))
        schema = ('media-index' if section == 'media' else section)+'.v1.schema.json'
        Draft202012Validator(read(root/'schemas'/schema)).validate(doc)
        if section == 'controls':
            for group in [doc.get('simple', []), *[page['params'] for page in doc.get('pages', [])]]:
                require(set(group) <= params, 'Unknown control parameter')
        elif section == 'look':
            require(set(doc.get('params', {})) <= params, 'Unknown look parameter')
        elif section == 'media':
            from PIL import Image
            ids, slots = set(), set()
            for item in doc['items']:
                require(item['id'] not in ids and not slots.intersection(item['slots']), 'Duplicate media ID or slot')
                ids.add(item['id'])
                slots.update(item['slots'])
                require(max(slots) < definition['runtime']['max_sources'], 'Unavailable source slot')
                path = local_file(folder, valid_path(item['path']))
                require(path.stat().st_size <= 8*1024*1024, 'Source image too large')
                with Image.open(path) as image:
                    require(image.format in ('PNG','WEBP','JPEG') and max(image.size) <= 4096, 'Invalid source image')
                    image.verify()
