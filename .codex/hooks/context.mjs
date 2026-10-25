// Read-only Codex adapters. Claude's rules, routing and checklist remain shared sources.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseChecklist } from '../../.claude/hooks/lib.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const read = (path) => readFileSync(resolve(root, path), 'utf8');

export function selectRoutes(prompt, routes) {
  if (!prompt.trim() || /^\s*[/$]/.test(prompt) || /(^|\s)@agent-[\w:-]+/i.test(prompt)) return [];
  return routes.filter((route) => new RegExp(route.match, 'i').test(prompt.slice(0, 4000))).slice(0, 3);
}

export function routeContext(hits) {
  return [
    'Shared routes (delegate to the matching native project agent):',
    ...hits.map((hit) => {
      const native = hit.agent && existsSync(resolve(root, '.codex/agents', `${hit.agent}.toml`));
      return `[${hit.id}] ${native ? `agent=${hit.agent} (.codex/agents/${hit.agent}.toml)` : `discipline=${hit.agent ?? 'documentation'}`}; ` +
        `review: ${hit.review.join(', ') || 'task checks'}; ` +
        `read rules: ${hit.rules.map((rule) => `.claude/rules/${rule}.md`).join(', ') || 'AGENTS.md'}; ${hit.note}`;
    }),
    'Project agents preserve their complete Claude definitions. Use $standup/$handoff for memory workflows. ' +
      'If custom roles are unavailable, pass the full developer_instructions to a worker. ' +
      'External ecc:* tools require an actual installation; otherwise perform the named review discipline.',
  ].join('\n');
}

export function brief(checklist, log) {
  const items = parseChecklist(checklist);
  const mine = items.filter((item) => !item.done && item.owner.includes('B'));
  return [
    'AegisPay · Dev B (Codex). Dev A uses Claude. Read AGENTS.md and docs/harness/CODEX.md.',
    `Checklist: ${items.filter((item) => item.done).length}/${items.length} complete.`,
    'Dev B open work (claims/blockers preserved; choose dependencies first):',
    ...mine.slice(0, 8).map((item) => `${item.id} @${item.owner} ${item.title}`),
    'Latest handoff:', log.split(/^## /m).at(-1).trim(),
    'No Git sync performed. Inspect git status; sync explicitly before claiming work. Use $standup.',
  ].join('\n');
}

function emit(event, context) {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: { hookEventName: event, additionalContext: context },
  }));
}

async function main() {
  const mode = process.argv[2];
  let input = {};
  // Explicit CLI arguments and terminal usage do not wait for stdin.
  if (!process.stdin.isTTY && !process.argv[3]) {
    let raw = '';
    for await (const chunk of process.stdin) raw += chunk;
    if (raw.trim()) input = JSON.parse(raw);
  }
  if (mode === 'brief') {
    emit('SessionStart', brief(read('docs/CHECKLIST.md'), read('docs/SESSION_LOG.md')));
  } else if (mode === 'route') {
    const hits = selectRoutes(String(process.argv[3] ?? input.prompt ?? ''), JSON.parse(read('.claude/routing.json')).routes);
    if (!hits.length) return;
    emit('UserPromptSubmit', routeContext(hits));
  } else if (mode === 'check') {
    // Reminder only: shell responses and asynchronous completion differ across Codex surfaces.
    // Never treat a missing exit code, a session id, or absence of failure text as a passing test.
    const command = String(input.tool_input?.command ?? input.tool_input?.cmd ?? '');
    if (!/pytest|ruff|vitest|jest|playwright|\btest\b|npm run build/i.test(command) && input.tool_name !== 'write_stdin') return;
    emit('PostToolUse', 'Verification reminder: inspect the final exit code and results; wait for running commands. ' +
      'Failed/incomplete checks: fix before pushing. After passing checks: review, commit intended paths, ' +
      'pull --rebase, rerun checks if sync changed code, push; update checklist and handoff. No Git mutation was run by this hook.');
  } else {
    throw new Error('Usage: node .codex/hooks/context.mjs brief|route [summary]|check');
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`AegisPay context: ${error.message}\n`);
    process.exitCode = 1;
  });
}
