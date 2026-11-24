// Read the canonical published .visual ZIP without extracting it into a filesystem.
// The per-release record and historical LICENSE reference come from the caller.
const utf8 = new TextDecoder('utf-8', { fatal: true });
const encoder = new TextEncoder();
const crcTable = Uint32Array.from({length: 256}, (_, v) => {
  for (let i = 0; i < 8; i++) v = (v >>> 1) ^ ((v & 1) ? 0xedb88320 : 0);
  return v >>> 0;
});
function check(ok, message) { if (!ok) throw new Error(message); }
function equal(a,b) { return a.length === b.length && a.every((v,i) => v === b[i]); }
function order(a,b) {
  for (let i=0; i<Math.min(a.length,b.length); i++) if (a[i] !== b[i]) return a[i]-b[i];
  return a.length-b.length;
}
function pathOK(name) {
  const parts=name.split('/');
  return !/[:\\\x00-\x1f\x7f]/.test(name) && parts.length<=6 && parts.every(p=>p && p!=='.' && p!=='..');
}
export async function hexDigest(bytes) {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return Array.from(digest, v => v.toString(16).padStart(2,'0')).join('');
}
export function readCanonicalZip(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = n=>view.getUint16(n,true), u32 = n=>view.getUint32(n,true);
  const end=bytes.length-22;
  check(end>=0 && u32(end)===0x06054b50 && u32(end+4)===0 && u16(end+20)===0,'Invalid ZIP footer');
  const count=u16(end+10), directory=u32(end+16);
  check(count && count<=512 && u16(end+8)===count && directory+u32(end+12)===end,'Invalid ZIP directory');
  let pos=directory, local=0, total=0, previous;
  const entries=new Map(), folded=new Set();
  for(let i=0;i<count;i++){
    check(pos+46<=end && u32(pos)===0x02014b50,'Invalid central header');
    const length=u16(pos+28), flags=u16(pos+8), size=u32(pos+24), crc=u32(pos+16);
    check(pos+46+length<=end && u16(pos+4)===0x0314 && u16(pos+6)===20 &&
      u16(pos+10)===0 && u16(pos+12)===0 && u16(pos+14)===33 && u32(pos+20)===size &&
      u32(pos+30)===0 && u32(pos+34)===0 && u32(pos+38)===0x81a40000 && u32(pos+42)===local,'Noncanonical ZIP entry');
    const rawName=bytes.subarray(pos+46,pos+46+length), name=utf8.decode(rawName);
    check(pathOK(name) && equal(rawName,encoder.encode(name)) && !folded.has(name.toLowerCase()) &&
      (!previous || order(previous,rawName)<0) && flags===(rawName.some(v=>v>=128)?2048:0),'Invalid ZIP name or order');
    folded.add(name.toLowerCase()); previous=rawName;
    total+=size;
    check(total<=64*1024*1024 && size<=(name.endsWith('.json')?256*1024:32*1024*1024),'ZIP size limit');
    check(local+30+length+size<=directory && u32(local)===0x04034b50 &&
      u16(local+4)===20 && u16(local+6)===flags && u16(local+8)===0 &&
      u16(local+10)===0 && u16(local+12)===33 && u32(local+14)===crc &&
      u32(local+18)===size && u32(local+22)===size && u16(local+26)===length &&
      u16(local+28)===0 && equal(rawName,bytes.subarray(local+30,local+30+length)),'Local ZIP header mismatch');
    const start=local+30+length, data=bytes.subarray(start,start+size);
    let sum=0xffffffff;
    for(const value of data) sum=(sum>>>8)^crcTable[(sum^value)&255];
    check(((sum^0xffffffff)>>>0)===crc,'ZIP CRC mismatch');
    entries.set(name,data); local=start+size; pos+=46+length;
  }
  check(pos===end && local===directory,'Unexpected ZIP payload');
  return entries;
}

export async function verifyVisual(bytes, { descriptor, descriptorFile, license, download, hashDefinition }) {
  const entries=readCanonicalZip(bytes);
  const json=name=>JSON.parse(utf8.decode(entries.get(name)));
  const meta=json('visual.json'), def=json('definition.json'), raw=entries.get('resource.json');
  check(raw && raw.length===descriptorFile.size && await hexDigest(raw)===descriptorFile.sha256,'Descriptor bytes differ');
  check(await hashDefinition(json('resource.json'))===await hashDefinition(descriptor),'Manifest descriptor differs');
  const id=meta.schema_version===0?meta.id:meta.visual_id;
  check([0,1].includes(meta.schema_version) && descriptor.schema_version===meta.schema_version &&
    descriptor.id===id && descriptor.version===meta.version && descriptor.publisher===meta.publisher &&
    descriptor.license===meta.license && descriptor.tier===meta.tier && descriptor.kind==='visual','Descriptor identity differs');
  check(meta.provenance.kind===def.provenance.kind && await hashDefinition(def)===meta.content_hash &&
    'sha256:'+descriptor.digest.value===meta.content_hash && descriptor.digest.algorithm==='jcs-sha256','Definition hash differs');
  const definitionFiles=descriptor.files.filter(f=>f.role==='definition');
  const match=definitionFiles.length===1 && /^visuals\/([a-z0-9]+(?:-[a-z0-9]+)*)\/definition\.json$/.exec(definitionFiles[0].path);
  check(match,'Missing resource definition');
  const prefix='visuals/'+match[1]+'/';
  const expected=new Map();
  for(const f of descriptor.files){
    const name=f.path.startsWith(prefix)?f.path.slice(prefix.length):f.path;
    check((f.path.startsWith(prefix)||f.path==='LICENSE'||/^thumbnails\/[^/]+$/.test(f.path)) &&
      pathOK(name) && !expected.has(name) && name!=='resource.json','Invalid descriptor file');
    expected.set(name,f);
  }
  if(meta.schema_version===0){
    check(id===def.scene_id && license && !expected.has('LICENSE'),'Missing historical LICENSE reference');
    expected.set('LICENSE',license);
  }else{
    check(descriptor.program.scene_id===def.scene_id && descriptor.program.content_hash===meta.content_hash,'Program reference differs');
  }
  for(const name of ['visual.json','definition.json','CHANGELOG.md','LICENSE',...meta.thumbnails.map(n=>'thumbnails/'+n)])
    check(expected.has(name),'Required file missing: '+name);
  check(entries.size===expected.size+1,'Package file count differs');
  const lines=[];
  for(const [name,data] of entries){
    if(name==='resource.json') continue;
    const f=expected.get(name), hash=await hexDigest(data);
    check(f && data.length===f.size && hash===f.sha256,'File differs: '+name);
    if(!name.startsWith('signatures/')) lines.push(name+'\0'+hash+'\0'+data.length+'\n');
  }
  const package_digest=await hexDigest(encoder.encode(lines.join('')));
  const result={sha256:await hexDigest(bytes),size:bytes.length,package_digest};
  if(meta.schema_version===1) check(package_digest===descriptor.package_digest,'Descriptor package digest differs');
  if(download) check(Object.keys(result).every(k=>result[k]===download[k]) && download.version===meta.version,'Download record differs');
  return result;
}
