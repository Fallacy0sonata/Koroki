'use strict';
// Owner co-op channel — command parser tests (pure, no server).

const assert = require('assert');
const { parseOwnerCommand } = require('./owner');

let pass = 0;
function eq(text, wantKind, wantTarget) {
  const got = parseOwnerCommand(text);
  const kind = got && got.kind;
  assert.strictEqual(kind, wantKind, `"${text}": got ${JSON.stringify(got)}, want kind=${wantKind}`);
  if (wantTarget !== undefined) {
    assert.strictEqual(got.target, wantTarget, `"${text}": got target ${got.target}, want ${wantTarget}`);
  }
  console.log(`  ok  "${text}" -> ${kind}${wantTarget !== undefined ? ` ${got.target}` : ''}`);
  pass++;
}

// follow
eq('follow me', 'follow');
eq('koroki come with me', 'follow');
eq('stick with me', 'follow');
// release (beats stop/follow)
eq('stop following', 'release');
eq('ok you can do your own thing', 'release');
eq("you're free now", 'release');
// come (not follow)
eq('come here', 'come');
eq('get over here', 'come');
eq('over here koroki', 'come');
// wait / stop (bare)
eq('wait', 'wait');
eq('stay here', 'wait');
eq('stop', 'wait');
// negation / cancel a task -> release (not the opposite intent)
eq('stop mining', 'release');
eq("don't follow me", 'release');
eq('stop gathering', 'release');
eq('never mind', 'release');
// gather with material
eq('gather wood', 'gather', 'log');
eq('get me some stone', 'gather', 'stone');
eq('chop some trees', 'gather', 'log');
eq('mine iron', 'gather', 'iron_ore');
eq('go get coal', 'gather', 'coal');
// generic mine (no material)
eq("let's mine", 'mine');
eq('go dig for a bit', 'mine');
// build
eq('build a shelter', 'build');
eq('make us a house', 'build');
// non-commands -> null
assert.strictEqual(parseOwnerCommand('nice weather huh'), null); console.log('  ok  chit-chat -> null'); pass++;
assert.strictEqual(parseOwnerCommand(''), null); console.log('  ok  empty -> null'); pass++;
assert.strictEqual(parseOwnerCommand('lol that creeper got me'), null); console.log('  ok  banter -> null'); pass++;

console.log(`\nOWNER CHANNEL: ${pass} PASS`);
