// node --test .claude/tests/post-test-sync.test.mjs
// Runs the hook against a throwaway repo + local bare remote; never touches the project remote.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
<<<<<<< HEAD
import { existsSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
=======
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
>>>>>>> origin/b/P0-19-payer-closeout
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HOOK = join(dirname(fileURLToPath(import.meta.url)), '..', 'hooks', 'post-test-sync.mjs');
const git = (cwd, ...args) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim();

function scratchRepo() {
  const root = mkdtempSync(join(tmpdir(), 'aegis-hook-'));
  const remote = join(root, 'remote.git');
  const work = join(root, 'work');
  git(root, 'init', '-q', '--bare', '-b', 'main', remote);
  git(root, 'init', '-q', '-b', 'main', work);
  git(work, 'config', 'user.email', 't@example.invalid');
  git(work, 'config', 'user.name', 't');
  git(work, 'remote', 'add', 'origin', remote);
  writeFileSync(join(work, 'a.txt'), 'a\n');
  git(work, 'add', '.');
  git(work, 'commit', '-qm', 'init');
  git(work, 'push', '-q', 'origin', 'main');
  git(work, 'switch', '-qc', 'a/P0-99-feature'); // no upstream yet
  return { root, remote, work };
}

function runHook(work, payload) {
  const res = spawnSync('node', [HOOK], {
    input: JSON.stringify({ tool_input: { command: 'pytest backend' }, ...payload }),
    env: { ...process.env, CLAUDE_PROJECT_DIR: work, AEGIS_AUTOPUSH: '1' },
    encoding: 'utf8',
  });
  assert.equal(res.status, 0, res.stderr);
  return JSON.parse(res.stdout);
}

const published = (remote) => git(remote, 'branch', '--list', 'a/P0-99-feature') !== '';

const NO_PUSH = {
  'nonzero exit code (usage error)': {
    hook_event_name: 'PostToolUse',
    tool_response: { exit_code: 2, stderr: 'pytest: error: unrecognized arguments: --bad-flag' },
  },
  'nonzero exit code, quiet output': { hook_event_name: 'PostToolUse', tool_response: { exitCode: 1, stdout: '' } },
  'PostToolUseFailure event': { hook_event_name: 'PostToolUseFailure', error: 'exit 1' },
  'failure text despite exit 0': { hook_event_name: 'PostToolUse', tool_response: { exit_code: 0, stdout: '2 failed, 3 passed' } },
  'backgrounded run, no result yet': { hook_event_name: 'PostToolUse', tool_response: { backgroundTaskId: 'b1', stdout: '' } },
  'interrupted run': { hook_event_name: 'PostToolUse', tool_response: { interrupted: true, stdout: '' } },
  'unknown exit status': { hook_event_name: 'PostToolUse', tool_response: { stdout: '3 passed' } },
};

for (const [name, payload] of Object.entries(NO_PUSH)) {
  test(`does not publish the branch: ${name}`, () => {
    const { root, remote, work } = scratchRepo();
    try {
      runHook(work, payload);
      assert.equal(published(remote), false);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
}

test('publishes a new feature branch only after an explicit exit status of 0', () => {
  const { root, remote, work } = scratchRepo();
  try {
    const out = runHook(work, { hook_event_name: 'PostToolUse', tool_response: { exit_code: 0, stdout: '3 passed' } });
    assert.equal(published(remote), true, JSON.stringify(out));
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
<<<<<<< HEAD

test('does not pull or push an existing branch after an unknown exit status', () => {
  const { root, remote, work } = scratchRepo();
  try {
    git(work, 'push', '-qu', 'origin', 'a/P0-99-feature');
    const previous = git(remote, 'rev-parse', 'refs/heads/a/P0-99-feature');
    writeFileSync(join(work, 'a.txt'), 'changed\n');
    git(work, 'commit', '-qam', 'local change');
    runHook(work, NO_PUSH['unknown exit status']);
    assert.equal(git(remote, 'rev-parse', 'refs/heads/a/P0-99-feature'), previous);
    assert.equal(existsSync(join(work, '.git', 'FETCH_HEAD')), false);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
=======
>>>>>>> origin/b/P0-19-payer-closeout
