#!/usr/bin/env node
/* Minimal worker bridge for one headless doubles battle. */

const fs = require('fs');
const path = require('path');

function readStdin() {
  return new Promise((resolve) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => (data += chunk));
    process.stdin.on('end', () => resolve(data));
  });
}

function mulberry32(seed) {
  let t = seed >>> 0;
  return function () {
    t += 0x6d2b79f5;
    let x = Math.imul(t ^ (t >>> 15), 1 | t);
    x ^= x + Math.imul(x ^ (x >>> 7), 61 | x);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

function sample(rng, items) {
  if (!items.length) return undefined;
  return items[Math.floor(rng() * items.length)];
}

function legalTeamPreview(request) {
  const n = request.maxTeamSize || 4;
  const count = (request.side?.pokemon || []).length;
  return `team ${Array.from({length: Math.min(n, count)}, (_, i) => i + 1).join(',')}`;
}

function legalMoveChoices(activeMon) {
  const moves = activeMon?.moves || [];
  const legal = [];
  moves.forEach((m, idx) => {
    if (!m.disabled) legal.push(`move ${idx + 1}`);
  });
  return legal;
}

function legalSwitchChoices(request, slotIdx) {
  const mons = request.side?.pokemon || [];
  const legal = [];
  mons.forEach((mon, idx) => {
    const benchPos = idx + 1;
    if (!mon.active && !mon.fainted) legal.push(`switch ${benchPos}`);
  });
  if (!legal.length && slotIdx === 0) return ['pass'];
  return legal;
}

function chooseFromRequest(request, policy, rng) {
  if (request.wait) return 'pass';

  if (request.teamPreview) return legalTeamPreview(request);

  const forceSwitch = request.forceSwitch;
  const active = request.active || [];

  // Doubles => two comma-separated choices in most turns.
  if (Array.isArray(forceSwitch)) {
    const parts = forceSwitch.map((mustSwitch, idx) => {
      if (!mustSwitch) {
        const moves = legalMoveChoices(active[idx]);
        return sample(rng, moves) || 'pass';
      }
      return sample(rng, legalSwitchChoices(request, idx)) || 'pass';
    });
    return parts.join(', ');
  }

  const parts = active.map((a) => sample(rng, legalMoveChoices(a)) || 'pass');
  if (parts.length) return parts.join(', ');
  return 'default';
}

(async () => {
  const input = JSON.parse((await readStdin()) || '{}');
  const repo = input.repo;
  if (!repo) throw new Error('Missing `repo` path to pokemon-showdown checkout.');

  const simPath = path.join(repo, 'dist', 'sim');
  if (!fs.existsSync(simPath)) {
    throw new Error('Could not find pokemon-showdown dist/sim. Build showdown first: `node build` in the showdown repo.');
  }

  const {BattleStreams} = require(simPath);
  const streams = BattleStreams.getPlayerStreams(new BattleStreams.BattleStream());

  const baseSeed = Number.isInteger(input.seed) ? input.seed : Date.now();
  const rngP1 = mulberry32(baseSeed ^ 0x11111111);
  const rngP2 = mulberry32(baseSeed ^ 0x22222222);

  const policyP1 = input.policy_p1 || 'random';
  const policyP2 = input.policy_p2 || 'random';

  streams.omniscient.write(`>start ${JSON.stringify({formatid: input.formatid || 'gen9vgc2024regg'})}\n`);
  streams.omniscient.write(`>player p1 ${JSON.stringify({name: 'p1', team: input.team_p1})}\n`);
  streams.omniscient.write(`>player p2 ${JSON.stringify({name: 'p2', team: input.team_p2})}\n`);

  let winner = 'tie';
  let turns = 0;

  async function pump(player, side) {
    const rng = side === 'p1' ? rngP1 : rngP2;
    const policy = side === 'p1' ? policyP1 : policyP2;

    for await (const chunk of player) {
      if (chunk.includes('|win|')) {
        winner = chunk.split('|win|')[1].trim().split('\n')[0];
        break;
      }
      if (chunk.includes('|turn|')) {
        const m = chunk.match(/\|turn\|(\d+)/);
        if (m) turns = Math.max(turns, Number(m[1]));
      }

      const reqPrefix = '|request|';
      const idx = chunk.lastIndexOf(reqPrefix);
      if (idx !== -1) {
        const reqLine = chunk.slice(idx + reqPrefix.length).split('\n')[0];
        const req = JSON.parse(reqLine);

        let choice = 'default';
        if (policy === 'random') {
          choice = chooseFromRequest(req, policy, rng);
        }
        streams[side].write(`>${side} ${choice}\n`);
      }
    }
  }

  await Promise.race([pump(streams.p1, 'p1'), pump(streams.p2, 'p2')]);
  process.stdout.write(JSON.stringify({winner, turns}));
})().catch((err) => {
  process.stderr.write(String(err.stack || err));
  process.exit(1);
});
