import fs from 'node:fs';
import path from 'node:path';
import contract from '../engine/vrch_pages_scene_contract.js';
import {readCanonicalZip,verifyVisual} from './visual_package_reader.mjs';

const [manifest, target] = process.argv.slice(2);
if (!manifest || !target) throw new Error('Expected a verified release manifest and a .visual file or directory');
const release = JSON.parse(fs.readFileSync(manifest, 'utf8'));
const batch=fs.statSync(target).isDirectory();
const singleBytes=batch ? null : fs.readFileSync(target);
const singleMeta=batch ? null : JSON.parse(new TextDecoder().decode(readCanonicalZip(singleBytes).get('visual.json')));
let count=0;
for (const descriptor of release.resources) {
  const name=descriptor.id+'-'+descriptor.version+'.visual';
  if(!batch && (descriptor.id !== (singleMeta.visual_id ?? singleMeta.id) || descriptor.version !== singleMeta.version)) continue;
  const source=descriptor.files.find(f=>f.role==='definition').path;
  const descriptorFile=release.files[path.posix.join(path.posix.dirname(source),'resource.json')];
  const bytes=singleBytes ?? fs.readFileSync(path.join(target,name));
  await verifyVisual(bytes, {
    descriptor, descriptorFile, license:release.files.LICENSE,
    download:release.downloads?.[descriptor.id], hashDefinition:contract.sha256Ref,
  });
  if(descriptor.schema_version===1){
    const files=readCanonicalZip(bytes);
    const get=name=>JSON.parse(new TextDecoder().decode(files.get(name)));
    const def=get('definition.json'), trusted=def.provenance.kind==='builtin' && release.trusted_history.some(
      ref=>ref.scene_id===def.scene_id && ref.content_hash==='sha256:'+descriptor.digest.value);
    contract.validateDefinition(def,{trusted});
    if(files.has('look.json')){
      const look=get('look.json'),hasAudio=def.signals.some(s=>s.id.startsWith('audio.'));
      const audio=def.signals.some(s=>s.id.startsWith('audio.v2.')) ?
        {contract_version:2,enabled:hasAudio,strength:1,band_gains:{low:1,mid:1,high:1}} : {enabled:hasAudio,gain:1};
      const instance={schema_version:1,instance_id:'visual:'+descriptor.id,
        scene_ref:{scene_id:def.scene_id,content_hash:'sha256:'+descriptor.digest.value},
        params:{...Object.fromEntries(def.parameters.map(p=>[p.key,p.default])),...look.params},
        bindings:look.bindings ?? def.default_bindings,modulation_master:look.modulation_master ?? {audio},
        quality_mode:look.quality_mode ?? 'auto'};
      contract.normalizeInstance(def,instance,{trusted});
    }
  }
  count++;
}
if(!count || (batch && count!==release.resources.length)) throw new Error('No matching release entries');
console.log(JSON.stringify({verified:count}));
