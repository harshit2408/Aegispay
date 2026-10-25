import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { brief, routeContext, selectRoutes } from '../hooks/context.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const routes = JSON.parse(readFileSync(resolve(root, '.claude/routing.json'), 'utf8')).routes;
const script = resolve(root, '.codex/hooks/context.mjs');

test('routes reuse shared frontend, data and money rules', () => {
  assert.ok(selectRoutes('AG Studio widget', routes).some((r) => r.id === 'frontend'));
  assert.ok(selectRoutes('SQLAlchemy migration', routes).some((r) => r.id === 'database'));
  assert.ok(selectRoutes('payout webhook', routes).some((r) => r.rules.includes('money-safety')));
  assert.ok(selectRoutes('payout schema React review security', routes).length <= 3);
});

test('explicit skill/agent overrides and empty prompts skip routing', () => {
  for (const prompt of ['', ' ', '$aegis-standup review', '/standup review', '@agent-ponytail-frontend review']) {
    assert.deepEqual(selectRoutes(prompt, routes), []);
  }
});

test('project routes delegate to native agents while external ECC stays a discipline', () => {
  const context = routeContext(selectRoutes('React widget', routes));
  assert.match(context, /agent=ponytail-frontend \(.codex\/agents\/ponytail-frontend.toml\)/);
  assert.match(context, /full developer_instructions to a worker/);
  assert.match(routeContext(selectRoutes('standup handoff', routes)), /agent=memory-keeper/);
  assert.match(routeContext(selectRoutes('review', routes)), /discipline=ecc:code-reviewer/);
});

test('brief includes Dev B/shared work and latest handoff without losing WIP', () => {
  const context = brief([
    '- [ ] **P0-01** @A Treasury only',
    '- [ ] **P0-02** @B Data (WIP B)',
    '- [ ] **P0-03** @AB Contract (WIP A)',
    '- [x] **P0-04** @B Completed',
  ].join('\r\n'), '# Log\n## Old\nold\n## New\nlatest');
  assert.match(context, /1\/4 complete/);
  assert.match(context, /Data \(WIP B\)/);
  assert.match(context, /Contract \(WIP A\)/);
  assert.match(context, /New\nlatest/);
  assert.doesNotMatch(context, /Treasury only|Completed|old/);
});

test('hook works from subdirectories, emits supported context, and rejects malformed input', () => {
  const result = spawnSync(process.execPath, [script, 'route'], {
    cwd: resolve(root, 'backend'), input: JSON.stringify({ prompt: 'React widget' }), encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  const output = JSON.parse(result.stdout).hookSpecificOutput;
  assert.equal(output.hookEventName, 'UserPromptSubmit');
  assert.match(output.additionalContext, /frontend/);
  const invalid = spawnSync(process.execPath, [script, 'route'], { input: '{', encoding: 'utf8' });
  assert.equal(invalid.status, 1);
});

test('failed and asynchronous test results only remind; never announce passing tests', () => {
  for (const tool_response of [{ exit_code: 1 }, { session_id: 123 }, { exit_code: 0 }]) {
    const result = spawnSync(process.execPath, [script, 'check'], {
      cwd: root, encoding: 'utf8', input: JSON.stringify({
        tool_name: 'Bash', tool_input: { command: 'python -m pytest backend' }, tool_response,
      }),
    });
    assert.equal(result.status, 0, result.stderr);
    const context = JSON.parse(result.stdout).hookSpecificOutput.additionalContext;
    assert.match(context, /inspect the final exit code/);
    assert.match(context, /No Git mutation/);
    assert.doesNotMatch(context, /tests passed|pushed \d/i);
  }
});

test('configured hook commands execute from a subdirectory on the current platform', () => {
  const config = JSON.parse(readFileSync(resolve(root, '.codex/hooks.json'), 'utf8'));
  for (const [event, groups] of Object.entries(config.hooks)) {
    const hook = groups[0].hooks[0];
    const windows = process.platform === 'win32';
    const command = windows ? hook.commandWindows : hook.command;
    const result = spawnSync(windows ? 'powershell.exe' : '/bin/sh',
      windows ? ['-NoProfile', '-Command', command] : ['-c', command], {
        cwd: resolve(root, 'backend'), encoding: 'utf8', timeout: 10000,
        input: JSON.stringify({ prompt: 'React widget', tool_input: { command: 'pytest' } }),
      });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).hookSpecificOutput.hookEventName, event);
  }
});
