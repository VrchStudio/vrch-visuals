"""Deterministic bundles, immutable visual versions and serialized GitHub publication."""
import copy
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


def descriptors(root, index, commit):
    meta=v.metadata(root)
    definitions={slug:v.read(root/f'visuals/{slug}/definition.json') for slug in meta}
    result=json.loads(subprocess.check_output(['node',str(root/'tools/check_engine.cjs')],input=json.dumps(list(definitions.values())),text=True))
    requirements={row['id']:row['requires'] for row in result['results']}
    resources=[]
    for slug,m in meta.items():
        d=definitions[slug]
        files=[{'role':role,'path':name,'sha256':v.sha(root/name),'size':(root/name).stat().st_size} for role,name in [('definition',f'visuals/{slug}/definition.json'),('metadata',f'visuals/{slug}/visual.json'),('changelog',f'visuals/{slug}/CHANGELOG.md')]+[('thumbnail','thumbnails/'+name) for name in m['thumbnails']]]
        fingerprint=hashlib.sha256(encoded({'metadata':m,'files':files})).hexdigest()
        history=index['visuals'].get(m['id'],{}).get('versions',{})
        old=history.get(m['version'])
        if old:
            v.require(old['fingerprint']==fingerprint, f'{slug}: published version changed without a version bump')
            resources.append(old)
            continue
        if history:
            v.require(version_key(m['version'])>max(map(version_key,history)), slug+': visual version must advance')
        v.require(f"## [{m['version']}]" in (root/f'visuals/{slug}/CHANGELOG.md').read_text(), slug+': missing per-visual changelog entry')
        base=d['provenance'].get('base_scene_ref')
        derived=[{'key':'visual:'+base['scene_id'],'digest':{'algorithm':'jcs-sha256','value':base['content_hash'].removeprefix('sha256:')}}] if base else []
        descriptor={'schema_version':0,'kind':'visual','id':m['id'],'publisher':m['publisher'],'name':d['title'],'version':m['version'],'digest':{'algorithm':'jcs-sha256','value':m['content_hash'].removeprefix('sha256:')},'tier':m['tier'],'license':m['license'],'requires':requirements[m['id']],'files':files,'provenance':{'repository':REPOSITORY,'commit':commit,'derived_from':derived},'signatures':[], 'fingerprint':fingerprint}
        v.Draft202012Validator(v.read(root/'schemas/resource-descriptor.v0.schema.json')).validate(descriptor)
        resources.append(descriptor)
    return resources


def build(version, root=v.ROOT, output=None, index=None, commit=None):
    version_key(version);v.check(root)
    index=index if index is not None else load_index(root)
    commit=commit or command('git','rev-parse','HEAD',cwd=root)
    old=index['versions'].get(version)
    source_commit=old['source_commit'] if old else commit
    output=output or root/'dist'/version;output.mkdir(parents=True,exist_ok=True)
    resources=descriptors(root,index,source_commit)
    files=bundle_files(root)
    slugs={m['id']:slug for slug,m in v.metadata(root).items()}
    for descriptor in resources:
        files[f'visuals/{slugs[descriptor["id"]]}/resource.json']=encoded(descriptor)
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
    if old:
        v.require(old['archive']==release['archive'], 'Published bundle version has different bytes')
    save(output/'release.json',release)
    record={'tag':tag,'source_commit':source_commit,'archive':release['archive'],'release':{'url':base+'release.json','sha256':v.sha(output/'release.json')}}
    if old:v.require(old==record,'Published release metadata differs')
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
    return result


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
    releases=json.loads(gh('api',f'repos/{REPOSITORY}/releases?per_page=100'))
    existing=next((r for r in releases if r['tag_name']==release['tag']),None)
    if not existing:
        refs=json.loads(gh('api',f'repos/{REPOSITORY}/git/matching-refs/tags/{release["tag"]}'))
        v.require(not any(r['ref']=='refs/tags/'+release['tag'] for r in refs),'Tag exists without a release')
        request=package.parent/'request.json';save(request,{'tag_name':release['tag'],'target_commitish':commit,'draft':True,'name':'VRCH Visuals '+version,'body':'Official free visual library '+version+'. See the bundled changelog and license.'})
        existing=json.loads(gh('api',f'repos/{REPOSITORY}/releases','--method','POST','--input',str(request)))
    if existing['draft']:v.require(existing['target_commitish']==commit,'Draft points to another source commit')
    for file in (package,package.parent/'release.json'):
        asset=next((a for a in existing['assets'] if a['name']==file.name),None)
        if asset_action(asset,file,existing['draft'])=='upload':gh('release','upload',release['tag'],str(file),'--repo',REPOSITORY)
    # Re-read uploaded assets before publication; never trust upload success alone.
    receipt=json.loads(gh('api',f'repos/{REPOSITORY}/releases/{existing["id"]}'))
    for file in (package,package.parent/'release.json'):
        asset=next((a for a in receipt['assets'] if a['name']==file.name),None)
        v.require(asset is not None,'Draft asset missing');asset_action(asset,file,False)
    if receipt['draft']:gh('release','edit',release['tag'],'--repo',REPOSITORY,'--draft=false','--latest=false')
    download(release['archive']['url'],release['archive']['sha256'],release['archive']['size'])
    download(record['release']['url'],record['release']['sha256'])
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
