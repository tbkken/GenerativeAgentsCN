/* Read-only comparison of prepared source placements with the existing UI picker. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../../../..');
const picker = require(path.join(root, 'src/generative_agents/adapters/web/static/resources/spatial-picker.js'));
const roster = JSON.parse(fs.readFileSync(path.join(__dirname, 'roster-reference.json'), 'utf8'));
const index = JSON.parse(fs.readFileSync(path.join(__dirname, '../map/config/resources/index.json'), 'utf8'));
const map = index.resources.find(item => item.kind === 'map');
const model = picker.buildModel(map.definition);
const checks = roster.agents.map(agent => {
  const node = model.byPath.get(JSON.stringify(agent.initial_address));
  if (!node) throw new Error(`Missing address for ${agent.display_name}`);
  return {name: agent.display_name, key: agent.proposed_stable_key, address: agent.initial_address,
    original_coord: agent.initial_coord, picker_coord: node.coord,
    matches: JSON.stringify(agent.initial_coord) === JSON.stringify(node.coord)};
});
const report = {status: 'offline-source-ui-algorithm-comparison', count: checks.length,
  matching: checks.filter(item => item.matches).length,
  mismatches: checks.filter(item => !item.matches), checks,
  supported_exact_coordinate_ui: 'Experiment Agent JSON import, merge by agent_key; not the spatial tree picker.'};
fs.writeFileSync(path.join(__dirname, 'spawn-picker-comparison.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({count: report.count, matching: report.matching, mismatches: report.mismatches}, null, 2));
