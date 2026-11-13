'use strict';
// Use the public browser contract, including its JCS implementation. No Pages Python dependency.
const fs = require('node:fs'), path = require('node:path');
const root = path.resolve(__dirname, '..');
const spec = require(path.join(root, 'engine/vrch_pages_scene_spec.js'));
const contract = require(path.join(root, 'engine/vrch_pages_scene_contract.js'));
const documents = spec.documents;
const enabled = new Set(documents['signal-bus.json'].signals.filter(s => s.enabled === true).map(s => s.id));
enabled.add(documents['audio-feature-v2.json'].spectrum_history.signal_id);
const runtime = fs.readFileSync(path.join(root, 'engine/vrch_pages_scene_runtime.js'), 'utf8');
enabled.add(/const TILE_MUSIC_SIGNAL = '([^']+)'/.exec(runtime)[1]);
const analysisKinds = new Set(Object.keys(documents['frame-analysis.json'].analyzers));
(async () => {
  const definitions = JSON.parse(fs.readFileSync(0, 'utf8'));
  const results = [];
  for (const definition of definitions) {
    contract.validateDefinition(definition, {trusted: definition.provenance.kind === 'builtin'});
    for (const signal of definition.signals) {
      if (!enabled.has(signal.id)) throw new Error(definition.scene_id + ': disabled or unknown signal ' + signal.id);
    }
    for (const requirement of definition.analysis_requirements || []) {
      if (!analysisKinds.has(requirement.kind)) throw new Error(definition.scene_id + ': unknown analysis ' + requirement.kind);
    }
    results.push({id: definition.scene_id, content_hash: await contract.sha256Ref(definition), requires: {
      abi: definition.runtime.abi + '@' + definition.runtime.abi_version,
      stages: ['main', ...(definition.frame ? ['frame'] : []), ...(definition.coarse ? ['coarse'] : [])],
      sources: [definition.runtime.min_sources, definition.runtime.max_sources],
      signals: definition.signals.map(s => s.id), analysis: (definition.analysis_requirements || []).map(r => r.kind)
    }});
  }
  // Schemas are extracted in memory from the public JS bundle, never copied as engine JSON files.
  process.stdout.write(JSON.stringify({results, schemas: spec.schemas}));
})().catch(error => { console.error(error.message); process.exitCode = 1; });
