#!/usr/bin/env node
// SessionStart hook: pull the teammate's work, then brief Claude (and the human)
// on where the project stands: checklist progress, this developer's next items,
// and the latest handoff from docs/SESSION_LOG.md.
// Who am I? AEGIS_DEV=A (backend/PayPal/agents) or B (full stack: frontend + backend/AG Studio/video),
// set in .claude/settings.local.json -> env.
import { readStdin, emit, projectDir, tryGit, pullRebase, readText, parseChecklist, behindTrunk, TRUNK } from './lib.mjs';

const NEXT_ITEMS = 6;

const input = await readStdin();
const cwd = projectDir(input);
const dev = (process.env.AEGIS_DEV || '').toUpperCase();

// 1. Sync with the remote (autostash keeps local edits safe).
let syncNote = 'no upstream branch';
if (tryGit(cwd, 'rev-parse', '@{u}').ok) {
  const pull = pullRebase(cwd);
  syncNote = pull.ok
    ? /up to date/i.test(pull.out) ? 'up to date with remote' : 'pulled teammate changes'
    : `PULL STOPPED: ${pull.out.split('\n')[0]}`;
}
const ahead = tryGit(cwd, 'rev-list', '--count', '@{u}..HEAD').out || '0';
const dirty = tryGit(cwd, 'status', '--porcelain').out.split('\n').filter(Boolean).length;
const branch = tryGit(cwd, 'branch', '--show-current').out;
const behind = behindTrunk(cwd);
const branchNote = behind > 0 ? ` (${behind} behind origin/${TRUNK}: git merge origin/${TRUNK})` : '';

// 2. Checklist progress.
const items = parseChecklist(readText(cwd, 'docs/CHECKLIST.md'));
const done = items.filter((i) => i.done).length;
const byPhase = {};
for (const i of items) {
  byPhase[i.phase] ??= [0, 0];
  byPhase[i.phase][1] += 1;
  if (i.done) byPhase[i.phase][0] += 1;
}
const phaseLine = Object.entries(byPhase).map(([p, [d, t]]) => `${p} ${d}/${t}`).join(' · ');
const mine = items.filter((i) => !i.done && (!dev || i.owner.includes(dev))).slice(0, NEXT_ITEMS);

// 3. Latest handoff entry.
const log = readText(cwd, 'docs/SESSION_LOG.md');
const lastEntry = log.split(/^## /m).slice(-1)[0]?.trim() || '(no handoffs yet)';

const context = [
  `AEGISPAY SESSION BRIEF — branch ${branch}${branchNote}: ${syncNote}; ${ahead} unpushed commit(s); ${dirty} uncommitted file(s).`,
  `Developer: ${dev ? `Dev ${dev}` : 'UNKNOWN — ask the user to set AEGIS_DEV=A|B in .claude/settings.local.json'}`,
  `Checklist: ${done}/${items.length} done (${phaseLine}).`,
  `Next open items${dev ? ` for Dev ${dev}` : ''}:`,
  ...mine.map((i) => `  - ${i.id} @${i.owner} ${i.title}`),
  'Latest handoff (docs/SESSION_LOG.md):',
  lastEntry.split('\n').slice(0, 14).map((l) => `  ${l}`).join('\n'),
  `Protocol: claim an item on ${TRUNK} ("(WIP <dev>)", push); code on branch <a|b>/<ID>-<slug>, PR into ${TRUNK}; tests → hook syncs git; end with /handoff.`,
  ...(branch === TRUNK && dirty ? [`You are on ${TRUNK} with uncommitted files: switch to a feature branch before committing code.`] : []),
].join('\n');

emit({
  systemMessage: `AegisPay: ${syncNote} · checklist ${done}/${items.length} · ${dev ? `Dev ${dev}` : 'set AEGIS_DEV'} · run /standup for the brief`,
  hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: context },
});
