"""Deterministic bundles, immutable visual versions and serialized GitHub publication."""
import copy
import io
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import urllib.request
import zipfile

import visuals as v
import visual_packages as vp

REPOSITORY = 'VrchStudio/vrch-visuals'
VERSION = re.compile(r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$')


def encoded(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(data))


def command(*args, cwd=v.ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def gh(*args):
    return command('gh', *args)


def version_key(version):
    v.require(bool(VERSION.fullmatch(version)), 'Expected stable x.y.z version')
    return tuple(map(int, version.split('.')))


def empty_index():
    return {'schema_version': 1, 'latest': None, 'versions': {}, 'visuals': {}}


def load_index(root=v.ROOT):
    path=root/'index/stable.json'
    return v.read(path) if path.exists() else empty_index()


def bundle_files(root):
    files={}
    for folder in ('visuals', 'thumbnails', 'catalog', 'schemas'):
        for path in sorted((root/folder).rglob('*')):
            if path.is_file():
                relative=path.relative_to(root).as_posix()
                files[relative]=v.local_file(root,relative).read_bytes()
    for name in ('LICENSE','CHANGELOG.md'):
        files[name]=v.local_file(root,name).read_bytes()
    return files


def required_bump(before, after):
    old={p['key']:p for p in before['parameters']};new={p['key']:p for p in after['parameters']}
    if set(old)!=set(new) or any(old[k]['type']!=new[k]['type'] for k in old):return 0
    if any(old[k]['default']!=new[k]['default'] for k in old):return 1
    return 2


def descriptors(root, index, commit):
    meta=v.metadata(root)
    definitions={slug:v.read(root/f'visuals/{slug}/definition.json') for slug in meta}
    result=json.loads(subprocess.check_output(['node',str(root/'tools/check_engine.cjs')],input=json.dumps(list(definitions.values())),text=True))
    requirements={row['id']:row['requires'] for row in result['results']}
    resources=[]
    for slug,m in meta.items():
        d=definitions[slug]
        if m['schema_version'] == 1:
            files = vp.v1_files(root, slug)
        else:
            files=[{'role':role,'path':name,'sha256':v.sha(root/name),'size':(root/name).stat().st_size} for role,name in [('definition',f'visuals/{slug}/definition.json'),('metadata',f'visuals/{slug}/visual.json'),('changelog',f'visuals/{slug}/CHANGELOG.md')]+[('thumbnail','thumbnails/'+name) for name in m['thumbnails']]]
        fingerprint=hashlib.sha256(encoded({'metadata':m,'files':files})).hexdigest()
        history=index['visuals'].get(v.visual_id(m),{}).get('versions',{})
        old=history.get(m['version'])
        if old:
            v.require(old['fingerprint']==fingerprint, f'{slug}: published version changed without a version bump')
            resources.append(old)
            continue
        if history:
            previous_version=max(history,key=version_key)
            v.require(version_key(m['version'])>version_key(previous_version), slug+': visual version must advance')
            previous=history[previous_version]
            previous_path=next(f['path'] for f in previous['files'] if f['role']=='definition')
            before=json.loads(command('git','show',previous['provenance']['commit']+':'+previous_path,cwd=root))
            level=required_bump(before,d)
            if m['schema_version'] == 1 and level == 2:
                passive = {'metadata', 'thumbnail', 'changelog', 'license'}
                before_files = {f['path']:f['sha256'] for f in previous['files'] if f['role'] not in passive}
                after_files = {f['path']:f['sha256'] for f in files if f['role'] not in passive}
                if before_files != after_files: level = 1
            v.require(version_key(m['version'])[:level+1]>version_key(previous_version)[:level+1], slug+': parameter compatibility requires '+('major' if level==0 else 'minor' if level==1 else 'patch'))
        v.require(f"## [{m['version']}]" in (root/f'visuals/{slug}/CHANGELOG.md').read_text(), slug+': missing per-visual changelog entry')
        base=d['provenance'].get('base_scene_ref')
        derived=[{'key':'visual:'+base['scene_id'],'digest':{'algorithm':'jcs-sha256','value':base['content_hash'].removeprefix('sha256:')}}] if base else []
        descriptor={'schema_version':m['schema_version'],'kind':'visual','id':v.visual_id(m),'publisher':m['publisher'],'name':m.get('title',d['title']),'version':m['version'],'digest':{'algorithm':'jcs-sha256','value':m['content_hash'].removeprefix('sha256:')},'tier':m['tier'],'license':m['license'],'requires':requirements[d['scene_id']],'files':files,'provenance':{'repository':REPOSITORY,'commit':commit,'derived_from':derived},'signatures':[], 'fingerprint':fingerprint}
        if m['schema_version'] == 1:
            entries = {vp.portable_path(f['path'],slug):v.local_file(root,f['path']).read_bytes() for f in files}
            descriptor['package_digest'] = vp.package_digest(entries)
            descriptor['program'] = {'scene_id': d['scene_id'], 'content_hash': m['content_hash'],
                                     'abi': d['runtime']['abi'], 'abi_version': d['runtime']['abi_version']}
            descriptor['requires'] = dict(descriptor['requires'])
            descriptor['requires']['sections'] = {section: 1 for section, file in vp.SECTIONS.items()
                                                  if file in entries and section != 'controls'}
            if 'controls.json' in entries: descriptor['requires']['advisory_sections'] = {'controls': 1}
        schema = 'resource-descriptor.v' + str(m['schema_version']) + '.schema.json'
        v.Draft202012Validator(v.read(root/'schemas'/schema)).validate(descriptor)
        resources.append(descriptor)
    return resources


def historical_builtin_refs(index, version, files, resources, root, history_dir):
    refs = set()
    def collect(descriptors, read_file):
        definitions = []
        hashes = []
        for item in descriptors:
            if item['publisher'] != 'vrch':
                continue
            relevant = {f['role']: f for f in item['files'] if f['role'] in ('definition', 'metadata')}
            documents = {}
            for role, entry in relevant.items():
                data = read_file(entry['path'])
                v.require(vp.stamp(data) == {k: entry[k] for k in ('size', 'sha256')}, 'Historical bytes differ')
                documents[role] = json.loads(data)
            meta, definition = documents['metadata'], documents['definition']
            v.require(meta['publisher'] == 'vrch' and meta['provenance']['kind'] == definition['provenance']['kind'],
                      'Historical provenance differs')
            if definition['provenance']['kind'] == 'builtin':
                definitions.append(definition)
                hashes.append('sha256:' + item['digest']['value'])
        if definitions:
            rows = json.loads(subprocess.check_output(['node', str(root/'tools/check_engine.cjs')],
                              input=json.dumps(definitions), text=True))['results']
            for row, expected in zip(rows, hashes, strict=True):
                v.require(row['content_hash'] == expected, 'Historical definition hash differs')
                refs.add((row['id'], expected))

    for ver, record in sorted(index['versions'].items(), key=lambda pair: version_key(pair[0])):
        if version_key(ver) >= version_key(version):
            continue
        def asset(key):
            item = record[key]
            name = item['url'].rsplit('/', 1)[-1]
            cached = history_dir / ver / name if history_dir else None
            if cached and cached.is_file():
                data = cached.read_bytes()
                v.require(vp.sha(data) == item['sha256'], 'Historical reference differs: ' + name)
                return data
            return download(item['url'], item['sha256'], item.get('size'))
        manifest = json.loads(asset('release'))
        with zipfile.ZipFile(io.BytesIO(asset('archive'))) as archive:
            collect(manifest['resources'], archive.read)
    collect(resources, files.__getitem__)
    return [{'scene_id': sid, 'content_hash': digest} for sid, digest in sorted(refs)]


def build(version, root=v.ROOT, output=None, index=None, commit=None, history_dir=None, preview=False):
    version_key(version);v.check(root, preview=preview)
    index=index if index is not None else load_index(root)
    commit=commit or command('git','rev-parse','HEAD',cwd=root)
    old=index['versions'].get(version)
    source_commit=old['source_commit'] if old else commit
    output=output or root/'dist'/version;output.mkdir(parents=True,exist_ok=True)
    resources=descriptors(root,index,source_commit)
    files=bundle_files(root)
    slugs={v.visual_id(m):slug for slug,m in v.metadata(root).items()}
    for descriptor in resources:
        files[f'visuals/{slugs[descriptor["id"]]}/resource.json']=encoded(descriptor)
    if version_key(version) >= (1, 1, 0):
        # Keep the original scene catalog for existing consumers. Extra works have
        # their own identity and only appear in the portable library.
        kinds = {r['id']: r for r in resources}
        only_works = {identifier for identifier, r in kinds.items() if r['schema_version'] == 1 and
                      (r['program']['scene_id'] != identifier or r['requires']['sections'])}
        selection = json.loads(files['catalog/selection.json'])
        if only_works or any('visual_id' in item for item in selection['items']):
            order = json.loads(files['catalog/order.json'])
            for kind in ('selection', 'order'):
                files[f'catalog/works/{kind}.json'] = files[f'catalog/{kind}.json']
            rows = []
            for selected in selection['items']:
                row = dict(selected)
                identifier = row.pop('visual_id', None) or row.get('scene_id')
                if identifier not in only_works:
                    row['scene_id'] = identifier
                    rows.append(row)
            selection['items'] = rows
            files['catalog/selection.json'] = encoded(selection)
            files['catalog/order.json'] = encoded({identifier: i for i, identifier in enumerate(
                identifier for identifier in sorted(order, key=order.__getitem__) if identifier not in only_works)})
    name=f'vrch-visuals-{version}.zip';package=output/name
    with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_STORED) as archive:
        for path,data in sorted(files.items()):
            info=zipfile.ZipInfo(path,(1980,1,1,0,0,0));info.create_system=3;info.external_attr=(stat.S_IFREG|0o644)<<16
            archive.writestr(info,data)
    tag='vrch-visuals-v'+version;base=f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
    release={'schema_version':1,'package':'vrch-visuals','version':version,'tag':tag,'source_commit':source_commit,
             'archive':{'name':name,'url':base+name,'sha256':v.sha(package),'size':package.stat().st_size},
             'engine':v.read(root/'engine/ENGINE_SOURCE.json'),
             'files':{path:{'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)} for path,data in sorted(files.items())},
             'managed_paths':{**v.owned_files(root), 'LICENSE':'static/scenes/v1/LICENSE.txt'},'resources':resources}
    if version_key(version) >= (1, 1, 0):
        release['downloads'] = vp.attachments(output, root, release, package)
        release['trusted_history'] = historical_builtin_refs(index, version, files, resources, root, history_dir)
    if old:
        v.require(old['archive']==release['archive'], 'Published bundle version has different bytes')
    save(output/'release.json',release)
    record={'tag':tag,'source_commit':source_commit,'archive':release['archive'],'release':{'url':base+'release.json','sha256':v.sha(output/'release.json')}}
    if old:v.require(old==record,'Published release metadata differs')
    if 'downloads' in release:
        update_index(index, release, record)
        subprocess.run(['node', str(root/'tools/verify_visual.mjs'), str(output/'release.json'), str(output)], check=True)
    return package,release,record


def update_index(index, release, record):
    result=copy.deepcopy(index);version=release['version']
    old=result['versions'].get(version)
    v.require(not old or old==record,'Immutable bundle index mismatch')
    result['versions'][version]=record
    result['latest']=max(result['versions'],key=version_key)
    for descriptor in release['resources']:
        item=result['visuals'].setdefault(descriptor['id'],{'latest':descriptor['version'],'versions':{}})
        old=item['versions'].get(descriptor['version'])
        v.require(not old or old==descriptor,'Immutable visual history mismatch')
        item['versions'][descriptor['version']]=descriptor
        item['latest']=max(item['versions'],key=version_key)
    if 'downloads' in release:
        archives = result.setdefault('package_archives', {})
        for identifier, download_record in release['downloads'].items():
            item_version = download_record['version']
            stamps = {key: download_record[key] for key in ('sha256', 'size', 'package_digest')}
            history = archives.setdefault(identifier, {})
            v.require(item_version not in history or history[item_version] == stamps,
                      'Immutable .visual changed: ' + identifier + ' ' + item_version)
            history[item_version] = stamps
    return result


def release_notes(release):
    body = 'Official free visual library ' + release['version'] + '. See the bundled changelog and license.'
    if 'downloads' in release:
        links = ['- [' + identifier + ' ' + d['version'] + '](' + d['url'] + ')'
                 for identifier, d in sorted(release['downloads'].items())]
        body += '\n\n## Download an individual visual\n\n' + '\n'.join(links)
    return body


def download(url, digest, size=None):
    data=urllib.request.urlopen(url,timeout=60).read()
    v.require(hashlib.sha256(data).hexdigest()==digest and (size is None or len(data)==size),'Public download mismatch: '+url)
    return data


def asset_action(asset, file, draft):
    if asset:
        v.require(asset.get('digest')=='sha256:'+v.sha(file) and asset['size']==file.stat().st_size,'Existing asset differs: '+file.name)
        return 'reuse'
    v.require(draft,'Published release is missing its expected asset: '+file.name)
    return 'upload'


def publish(version):
    v.require(os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('GITHUB_REPOSITORY')==REPOSITORY,'Use release to dispatch the serialized workflow')
    commit=command('git','rev-parse','HEAD');command('git','fetch','origin','main');command('git','merge-base','--is-ancestor',commit,'origin/main')
    try:index=json.loads(command('git','show','origin/main:index/stable.json'))
    except subprocess.CalledProcessError:index=empty_index()
    package,release,record=build(version,index=index,commit=commit)
    update_index(index,release,record)
    singles = [package.parent / entry['url'].rsplit('/', 1)[-1] for entry in release.get('downloads', {}).values()]
    assets = [package, package.parent/'release.json', *singles]
    notes = release_notes(release)
    releases=json.loads(gh('api',f'repos/{REPOSITORY}/releases?per_page=100'))
    existing=next((r for r in releases if r['tag_name']==release['tag']),None)
    if not existing:
        refs=json.loads(gh('api',f'repos/{REPOSITORY}/git/matching-refs/tags/{release["tag"]}'))
        v.require(not any(r['ref']=='refs/tags/'+release['tag'] for r in refs),'Tag exists without a release')
        request=package.parent/'request.json';save(request,{'tag_name':release['tag'],'target_commitish':commit,'draft':True,'name':'VRCH Visuals '+version,'body':notes})
        existing=json.loads(gh('api',f'repos/{REPOSITORY}/releases','--method','POST','--input',str(request)))
    if existing['draft']:v.require(existing['target_commitish']==commit,'Draft points to another source commit')
    def listed_assets():
        pages=json.loads(gh('api','--paginate','--slurp',
                            f'repos/{REPOSITORY}/releases/{existing["id"]}/assets?per_page=100'))
        return {asset['name']:asset for page in pages for asset in page}
    present=listed_assets()
    for file in assets:
        if asset_action(present.get(file.name), file, existing['draft'])=='upload':
            gh('release','upload',release['tag'],str(file),'--repo',REPOSITORY)
    # Check the actual server-side assets even on a retry.
    receipt=json.loads(gh('api',f'repos/{REPOSITORY}/releases/{existing["id"]}'))
    present=listed_assets()
    v.require(set(present)=={file.name for file in assets},'Release asset set differs')
    for file in assets:
        asset_action(present[file.name],file,False)
    if singles:
        v.require(receipt['body'].replace('\r\n', '\n') == notes, 'Release download directory differs')
        for file in singles:
            asset = present[file.name]
            raw = subprocess.check_output(['gh', 'api', '-H', 'Accept: application/octet-stream',
                                           f'repos/{REPOSITORY}/releases/assets/{asset["id"]}'])
            v.require(raw == file.read_bytes(), 'Uploaded .visual differs: ' + file.name)
    if receipt['draft']:gh('release','edit',release['tag'],'--repo',REPOSITORY,'--draft=false','--latest=false')
    download(release['archive']['url'],release['archive']['sha256'],release['archive']['size'])
    download(record['release']['url'],record['release']['sha256'])
    for entry in release.get('downloads', {}).values():
        download(entry['url'], entry['sha256'], entry['size'])
    # Rebase the index write on latest main. An older retry never moves latest backwards.
    command('git','fetch','origin','main');command('git','checkout','-B','visuals-index-publish','origin/main')
    updated=update_index(load_index(),release,record);save(v.ROOT/'index/stable.json',updated)
    if command('git','status','--porcelain','--','index/stable.json'):
        command('git','config','user.name','github-actions[bot]');command('git','config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
        command('git','add','--','index/stable.json');command('git','commit','-m','release: index visuals '+version);command('git','push','origin','HEAD:main')
    return {'version':version,'archive_sha256':release['archive']['sha256'],'index':'published'}


def dispatch(version):
    version_key(version);v.require(not command('git','status','--porcelain'),'Commit source changes first')
    commit=command('git','rev-parse','HEAD');command('git','fetch','origin','main');command('git','merge-base','--is-ancestor',commit,'origin/main')
    gh('workflow','run','release.yml','--repo',REPOSITORY,'--ref','main','-f','version='+version,'-f','source_ref='+commit)
    return {'workflow':'release.yml','source_ref':commit,'version':version}


def extract(package, slug, destination):
    """Take one complete resource from a bundle, without the global index or other resources."""
    import tempfile
    destination=destination.resolve()
    v.require(bool(re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',slug)), 'Invalid resource slug')
    with zipfile.ZipFile(package) as archive, tempfile.TemporaryDirectory(prefix='visual-resource-') as temp:
        descriptor_path=f'visuals/{slug}/resource.json'
        descriptor=json.loads(archive.read(descriptor_path))
        v.Draft202012Validator(v.read(v.ROOT/'schemas/resource-descriptor.v0.schema.json')).validate(descriptor)
        paths={item['path']:item for item in descriptor['files']}
        v.require(f'visuals/{slug}/definition.json' in paths,'Resource definition missing')
        for name in [*paths,descriptor_path,'LICENSE']:
            relative=Path(name)
            v.require(not relative.is_absolute() and '..' not in relative.parts and '\\' not in name,'Unsafe resource path')
            data=archive.read(name)
            if name in paths:v.require(hashlib.sha256(data).hexdigest()==paths[name]['sha256'] and len(data)==paths[name]['size'],'Resource file digest mismatch')
            target=Path(temp)/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        import shutil
        for source in Path(temp).rglob('*'):
            if source.is_file():
                target=destination/source.relative_to(temp)
                v.require(not target.is_symlink() and target.resolve().is_relative_to(destination),'Unsafe extraction destination')
        for source in Path(temp).rglob('*'):
            if source.is_file():
                target=destination/source.relative_to(temp);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);target.chmod(0o644)
    return {'id':descriptor['id'],'version':descriptor['version'],'files':len(paths)+2,'destination':str(destination)}


def engine_sync(pages_root, ref, write=False, root=v.ROOT):
    commit=command('git','rev-parse',ref+'^{commit}',cwd=pages_root)
    files={name:subprocess.check_output(['git','show',commit+':static/scripts/'+name],cwd=pages_root) for name in sorted(v.ENGINE_FILES)}
    source=v.read(root/'engine/ENGINE_SOURCE.json')
    source['commit']=commit
    source['files']={name:{'source':'static/scripts/'+name,'sha256':hashlib.sha256(data).hexdigest()} for name,data in files.items()}
    changed=[name for name,data in files.items() if (root/'engine'/name).read_bytes()!=data]
    if write:
        for name,data in files.items():(root/'engine'/name).write_bytes(data)
        save(root/'engine/ENGINE_SOURCE.json',source)
    return {'pages_commit':commit,'changed_files':changed,'written':write}
