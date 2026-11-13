'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
// Focused portability rules; this does not claim full GLSL range analysis.
function lint(source){
 const text=source.replace(/\/\*[\s\S]*?\*\//g,' ').replace(/\/\/[^\n]*/g,' '),issues=[];
 if(/\bpow\s*\(\s*(?:-\s*(?:\d|[a-zA-Z_])|(?:sin|cos)\s*\()/m.test(text))issues.push('pow-negative-base: use an explicitly nonnegative base');
 if(/\bmod\s*\(\s*floor\s*\([\s\S]{0,100}?\b(?:mask|bits|flags)\b[^;{}]*\/[^;{}]*\)\s*,\s*2(?:\.0)?\s*\)/i.test(text)||/\bfloor\s*\([^;{}]*\/\s*pow\s*\(\s*2(?:\.0)?\s*,/m.test(text))issues.push('float-bit-decode: use integer bit operations');
 return issues;
}
if(require.main===module){
 const root=path.resolve(__dirname,'..');let failures=[];const exemptions=JSON.parse(fs.readFileSync(path.join(root,'fixtures/shader-lint-exceptions.json')));const used=new Set();
 for(const slug of fs.readdirSync(path.join(root,'visuals'))){const d=JSON.parse(fs.readFileSync(path.join(root,'visuals',slug,'definition.json')));for(const stage of ['fragment','frame','coarse'])if(d[stage])for(const issue of lint(d[stage].source)){const key=slug+'/'+stage,e=exemptions[key];if(e&&issue.startsWith(e.rule+':')&&crypto.createHash('sha256').update(d[stage].source).digest('hex')===e.sha256){used.add(key);console.log('Reviewed migration exception:',key,e.expression);}else failures.push(key+': '+issue);}}
 for(const key of Object.keys(exemptions))if(!used.has(key))failures.push('Stale lint exception: '+key);
 if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}else console.log('Shader portability lint OK');
}
module.exports={lint};
