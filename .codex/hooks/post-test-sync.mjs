#!/usr/bin/env node
// PostToolUse / PostToolUseFailure hook (Bash|PowerShell): after every test run,
// keep both developers in sync.
//   tests failed -> tell Claude not to push.
//   tests passed -> git pull --rebase --autostash, push any local commits,
//                   and tell Claude to commit + tick docs/CHECKLIST.md if the tree is dirty.
// Set AEGIS_AUTOPUSH=0 in .claude/settings.local.json to pull-only.
import { readStdin, emit, projectDir, tryGit, pullRebase, repoBusy, behindTrunk, TRUNK } from './lib.mjs';

// A test runner at the start of any `&&` / `;` / `|` segment (optionally after env vars or a runner prefix).
const TEST_RUNNER =
  /^(\w+=\S*\s+)*((uv|poetry) run |python3? -m |npx |pnpm exec |bunx )?(pytest|py\.test|vitest|jest|playwright test|(npm|pnpm|yarn|bun) (run )?test[\w:-]*|make test)\b/i;
const isTestCommand = (cmd) => cmd.split(/&&|\|\||;|\|/).some((seg) => TEST_RUNNER.test(seg.trim()));
// Belt and braces: some runners exit 0 with failures in the summary.
const FAIL_TEXT =
  /\b[1-9]\d* (failed|errors?)\b|^FAILED |✗ [1-9]\d* failed|^ERROR: |\berror: unrecognized arguments\b|\bno tests ran\b|^usage: /im;

const input = await readStdin();
const event = input.hook_event_name || 'PostToolUse';
const cmd = String(input.tool_input?.command ?? '');
if (!isTestCommand(cmd)) process.exit(0);

const resp = input.tool_response ?? {};
const output = [resp.stdout, resp.stderr, resp.output, input.error].filter(Boolean).join('\n');
// Any reported exit status must be exactly 0. Interrupted or backgrounded runs have no final result yet.
const exitCode = [resp.exit_code, resp.exitCode, resp.returnCode, resp.code].find((c) => c !== undefined && c !== null);
const completed = !resp.interrupted && !resp.backgroundTaskId && !resp.backgroundedByUser;
// Every git mutation (pull --rebase, push) needs an explicit exit status of 0; a missing status is unknown, not success.
const exitedZero = exitCode === 0 || exitCode === '0';
const passed = event === 'PostToolUse' && completed && exitedZero && !FAIL_TEXT.test(output);

const say = (systemMessage, additionalContext) =>
  emit({ systemMessage, hookSpecificOutput: { hookEventName: event, additionalContext } });

if (!passed) {
  say(
    'team-sync: tests FAILED — nothing pulled or pushed.',
    'Tests failed. Do NOT commit or push. Fix the failure (route: ecc:tdd-workflow) and re-run the tests; the sync hook runs again on the next passing run.'
  );
  process.exit(0);
}

const cwd = projectDir(input);
if (repoBusy(cwd)) {
  say(
    'team-sync: tests passed — a rebase/merge is in progress, left untouched. Nothing pushed.',
    'Tests passed while a git rebase or merge is in progress. Finish it (resolve conflicts, `git rebase --continue` or commit the merge), re-run tests, then push.'
  );
  process.exit(0);
}
const branch = tryGit(cwd, 'branch', '--show-current').out || 'HEAD';
const onTrunk = branch === TRUNK;
let upstream = tryGit(cwd, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}');
if (!upstream.ok) {
  const canPush = !onTrunk && branch !== 'HEAD' && process.env.AEGIS_AUTOPUSH !== '0';
  if (!canPush || !tryGit(cwd, 'push', '-u', 'origin', branch).ok) {
    say(
      `team-sync: tests passed; branch "${branch}" has no upstream.`,
      `Tests passed but "${branch}" tracks no remote branch. After committing, run: git push -u origin ${branch}`
    );
    process.exit(0);
  }
  upstream = tryGit(cwd, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}');
}

const notes = [];
const behind = behindTrunk(cwd);
if (behind > 0) notes.push(`branch is ${behind} commit(s) behind origin/${TRUNK}`);
const pull = pullRebase(cwd);
if (!pull.ok) {
  say(
    `team-sync: tests passed but sync stopped — ${pull.out.split('\n')[0]}. Nothing pushed.`,
    `Tests passed but syncing with the other developer's work stopped:\n${pull.out}\n` +
      'Do NOT commit or push yet. Resolve it (git pull --rebase or finish the in-progress rebase/merge; ' +
      'for an autostash conflict fix the files then `git stash drop`), keep both sides\' intent, re-run tests. Tell the user which files conflicted.'
  );
  process.exit(0);
}
notes.push(/up to date/i.test(pull.out) ? 'pulled: already up to date' : 'pulled teammate changes');

const ahead = Number(tryGit(cwd, 'rev-list', '--count', '@{u}..HEAD').out || 0);
if (ahead > 0 && process.env.AEGIS_AUTOPUSH !== '0') {
  const push = tryGit(cwd, 'push');
  notes.push(push.ok ? `pushed ${ahead} commit(s) to ${upstream.out}` : `push FAILED: ${push.out}`);
}

const dirty = tryGit(cwd, 'status', '--porcelain').out;
const trunkRule = onTrunk
  ? `You are on ${TRUNK}: only checklist claims and docs go here. Code goes on a feature branch — run git switch -c <a|b>/<ID>-<slug> BEFORE committing, then open a PR.`
  : `Open a PR into ${TRUNK} when the item is done (gh pr create); the other dev reviews, squash-merge.`;
const mergeRule = behind > 0 ? ` Branch is behind origin/${TRUNK}: run git merge origin/${TRUNK} (never force-push), re-run tests.` : '';
const context = dirty
  ? 'Tests passed and the tree has uncommitted work. Now: (1) tick finished items in docs/CHECKLIST.md, ' +
    '(2) commit with a conventional message (feat|fix|test|docs: ... — reference checklist IDs), ' +
    `(3) run git push so the other developer gets it. Do not commit .env or secrets. ${trunkRule}${mergeRule}`
  : `Tests passed; working tree is clean and in sync with the remote. ${trunkRule}${mergeRule}`;

say(`team-sync: tests passed — ${notes.join('; ')}${dirty ? '; uncommitted changes remain' : ''}.`, context);
