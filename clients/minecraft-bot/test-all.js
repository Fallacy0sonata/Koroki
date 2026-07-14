'use strict';
// Runs every pure-logic test suite and reports a rollup. `npm test`.

const { execFileSync } = require('child_process');

const SUITES = [
  'test-perceive.js',
  'test-decide.js',
  'test-projects.js',
  'test-owner.js',
  'test-world-model.js',
  'test-combat.js',
  'test-social.js',
  'test-building.js',
  'test-explore.js',
  'test-foods.js',
  'test-inventory.js',
  'test-mining.js',
  'test-recipe.js',
  'test-verify.js',
  'test-motor.js',
  'test-rhythm.js',
  'test-verbs.js',
];

let failed = 0;
for (const s of SUITES) {
  try {
    const out = execFileSync(process.execPath, [s], { cwd: __dirname, encoding: 'utf8' });
    const last = out.trim().split('\n').pop();
    console.log(`✓ ${s.padEnd(22)} ${last}`);
  } catch (e) {
    failed++;
    console.log(`✗ ${s.padEnd(22)} FAILED`);
    if (e.stdout) console.log(e.stdout.toString().trim());
  }
}
console.log(failed ? `\n${failed} suite(s) FAILED` : '\nALL SUITES PASS');
process.exit(failed ? 1 : 0);
