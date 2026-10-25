import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { ruleContext, secretPath, syncRepo, testResult } from '../hooks/lifecycle.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const packet = (command, response) => ({ tool_input: { command }, tool_response: response });

test('only isolated real test runners and explicit final success qualify for sync', () => {
  for (const command of ['python -m pytest backend', '.venv/Scripts/python.exe -m pytest backend', 'node --test .codex/tests/context.test.mjs', 'npm run test']) {
    assert.equal(testResult(packet(command, { exit_code: 0 })), 'passed');
    for (const response of [{ session_id: 2 }, {}, { exit_code: null }, 'Process exited with code 0', '{"exit_code":0}']) {
      assert.equal(testResult(packet(command, response)), 'unknown');
    }
    assert.equal(testResult(packet(command, { exit_code: 1 })), 'failed');
    assert.equal(testResult(packet(command, { exit_code: 0, output: '1 failed' })), 'failed');
  }
  for (const command of ['echo pytest', 'Get-Content pytest.log', 'python -c "print(1)"']) {
    assert.equal(testResult(packet(command, { exit_code: 0 })), 'unrelated');
  }
  for (const command of ['pytest; echo done', 'pytest && echo done', 'pytest --collect-only', 'vitest list', 'npx playwright test --list']) {
    assert.notEqual(testResult(packet(command, { exit_code: 0 })), 'passed');
  }
});

test('path rules cover normalized patches and unknown shell paths conservatively', () => {
  for (const path of ['backend/app/models/x.py', './backend/app/models/x.py', 'backend/../backend/app/models/x.py']) {
    const rules = ruleContext({ cwd: root, tool_input: { command: `*** Update File: ${path}` } });
    assert.match(rules, /Money-safety rule/);
    assert.match(rules, /Backend rule/);
    assert.doesNotMatch(rules, /Frontend rule/);
  }
  assert.match(ruleContext({ tool_input: { command: 'python -m pytest' } }), /Money-safety rule/);
  assert.match(ruleContext({ cwd: join(root, 'frontend'), tool_input: { path: 'src/app/page.tsx' } }), /Frontend rule/);
});

test('copied secret-file deny applies to quoted paths, while templates remain accessible', () => {
  for (const command of ['Get-Content .env', 'Get-Content ".env"', "cat '.env.local'", 'cat D:/project/.env', 'cat .env.example.local', 'cat $(echo .e)nv']) {
    assert.equal(secretPath(packet(command, {})), true, command);
  }
  assert.equal(secretPath({ tool_input: { file_path: '.env.local' } }), true);
  assert.equal(secretPath(packet('Get-Content .env.example', {})), false);
});

test('PreToolUse rules run for every shell tool PostToolUse sees, plus patch edits', async () => {
  const { readFileSync } = await import('node:fs');
  const hooks = JSON.parse(readFileSync(join(root, '.codex/hooks.json'), 'utf8'));
  const pre = hooks.hooks.PreToolUse[0].matcher.split('|');
  for (const tool of [...hooks.hooks.PostToolUse[0].matcher.split('|'), 'apply_patch']) {
    assert.ok(pre.includes(tool), tool);
  }
});

test('sync pushes verified clean committed work but leaves dirty work untouched', () => {
  const directory = mkdtempSync(join(tmpdir(), 'aegis-sync-'));
  const remote = join(directory, 'origin.git');
  const checkout = join(directory, 'checkout');
  const git = (cwd, ...args) => execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
  git(directory, 'init', '--bare', remote);
  git(directory, 'clone', remote, checkout);
  git(checkout, 'config', 'user.name', 'Harness Test');
  git(checkout, 'config', 'user.email', 'harness@example.invalid');
  writeFileSync(join(checkout, 'note.md'), 'first\n');
  git(checkout, 'add', 'note.md');
  git(checkout, 'commit', '-m', 'initial');
  git(checkout, 'push', '-u', 'origin', 'HEAD');
  const branch = git(checkout, 'branch', '--show-current');
  writeFileSync(join(checkout, 'note.md'), 'second\n');
  git(checkout, 'commit', '-am', 'second');
  const committed = git(checkout, 'rev-parse', 'HEAD');
  const previous = process.env.AEGIS_AUTOPUSH;
  process.env.AEGIS_AUTOPUSH = '1';
  try {
    assert.match(syncRepo(checkout, true), /committed work pushed/);
    assert.equal(git(remote, 'rev-parse', `refs/heads/${branch}`), committed);
    writeFileSync(join(checkout, 'note.md'), 'dirty\n');
    assert.match(syncRepo(checkout, true), /uncommitted work/);
    assert.equal(git(remote, 'rev-parse', `refs/heads/${branch}`), committed);
  } finally {
    if (previous === undefined) delete process.env.AEGIS_AUTOPUSH;
    else process.env.AEGIS_AUTOPUSH = previous;
  }
});
