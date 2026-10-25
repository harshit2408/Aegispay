// Shared helpers for the AegisPay project hooks (Node >= 18, no dependencies).
import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { join, isAbsolute } from 'node:path';

// pull + push must both fit inside the 120 s PostToolUse hook timeout.
const GIT_TIMEOUT_MS = 40_000;

export async function readStdin() {
  process.stdin.setEncoding('utf8');
  let raw = '';
  for await (const chunk of process.stdin) raw += chunk;
  try {
    return raw.trim() ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function emit(obj) {
  process.stdout.write(JSON.stringify(obj));
}

export function projectDir(input = {}) {
  return process.env.CLAUDE_PROJECT_DIR || input.cwd || process.cwd();
}

// Runs git and returns trimmed stdout; throws with stderr on non-zero exit.
export function git(cwd, ...args) {
  return execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: GIT_TIMEOUT_MS,
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0' }, // never hang on a credential prompt
  }).trim();
}

export function tryGit(cwd, ...args) {
  try {
    return { ok: true, out: git(cwd, ...args) };
  } catch (err) {
    return { ok: false, out: String(err.stderr || err.message).trim() };
  }
}

export function repoBusy(cwd) {
  return ['rebase-merge', 'rebase-apply', 'MERGE_HEAD'].some((p) => {
    const r = tryGit(cwd, 'rev-parse', '--git-path', p);
    return r.ok && existsSync(isAbsolute(r.out) ? r.out : join(cwd, r.out));
  });
}

// git pull --rebase --autostash that never touches a rebase/merge someone else started,
// and reports an autostash re-apply conflict instead of calling it success.
export function pullRebase(cwd) {
  if (repoBusy(cwd)) return { ok: false, out: 'a rebase or merge is already in progress — left untouched' };
  const pull = tryGit(cwd, 'pull', '--rebase', '--autostash');
  if (!pull.ok) {
    if (repoBusy(cwd)) tryGit(cwd, 'rebase', '--abort'); // only ours: repo was clean before the pull
    return { ok: false, out: `rebase aborted: ${pull.out}` };
  }
  const unmerged = tryGit(cwd, 'diff', '--name-only', '--diff-filter=U').out;
  if (unmerged) return { ok: false, out: `autostash re-apply conflicted (stash kept): ${unmerged.split('\n').join(', ')}` };
  return pull;
}

export function readText(dir, rel) {
  const p = join(dir, rel);
  return existsSync(p) ? readFileSync(p, 'utf8') : '';
}

// Checklist line format: "- [ ] **BE-01** @A Do the thing (WIP A)"  (owner @A, @B or @AB)
const ITEM = /^\s*- \[( |x|X)\] \*\*([A-Z0-9]+-\d+)\*\*\s+@(AB|A|B)\s+(.*?)\s*$/;

export function parseChecklist(text) {
  return text
    .split(/\r?\n/)
    .map((l) => l.match(ITEM))
    .filter(Boolean)
    .map(([, mark, id, owner, title]) => ({
      id,
      title,
      owner,
      done: mark.toLowerCase() === 'x',
      phase: id.split('-')[0],
    }));
}
