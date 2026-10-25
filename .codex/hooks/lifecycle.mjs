// Codex transport adapters. The original Claude files beside this remain unchanged copies.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { basename, dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { brief } from './context.mjs';
import { repoBusy, tryGit } from './lib.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const read = (path) => readFileSync(resolve(root, path), 'utf8');
const output = (event, context) => ({ hookSpecificOutput: { hookEventName: event, additionalContext: context } });

export function testResult(input) {
  const command = String(input.tool_input?.command ?? input.tool_input?.cmd ?? '');
  const invocation = command.match(/^\s*(?:&\s*)?(?:"([^"]+)"|'([^']+)'|([^\s]+))\s*(.*)$/s);
  if (!invocation) return 'unrelated';
  const executable = basename((invocation[1] || invocation[2] || invocation[3]).replaceAll('\\', '/')).replace(/\.(exe|cmd|ps1)$/i, '').toLowerCase();
  const args = invocation[4];
  const runner = ['pytest', 'py.test', 'vitest', 'jest'].includes(executable)
    || (/^(python(?:3(?:\.\d+)?)?|py)$/.test(executable) && /^-m\s+(pytest|unittest)\b/.test(args))
    || (executable === 'node' && /^--test\b/.test(args))
    || (['npm', 'pnpm', 'yarn', 'bun'].includes(executable) && /^(?:run\s+)?test[\w:-]*\b/.test(args))
    || (executable === 'npx' && /^(?:playwright\s+test|vitest|jest)\b/.test(args));
  if (!runner) return 'unrelated';
  // A shell's final status cannot establish that earlier commands in a compound script passed.
  if (/[;\n]|&&|\|\||\|/.test(command) || /(?:^|\s)(?:--help|--version|--collect-only|--listTests|--list|--dry-run|-h)(?:\s|$)/.test(args)
    || (executable === 'vitest' && /^list\b/.test(args))) return 'unknown';
  // stdout-only strings (including JSON-looking stdout) cannot prove a transport exit status.
  const response = input.tool_response;
  if (!response || typeof response !== 'object' || response.session_id != null) return 'unknown';
  const code = response.exit_code ?? response.exitCode;
  if (!Number.isInteger(code)) return 'unknown';
  const text = [response.stdout, response.stderr, response.output, input.error].filter(Boolean).join('\n');
  if (code !== 0 || input.error || /\b[1-9]\d* (?:failed|errors?)\b|^FAILED /im.test(text)) return 'failed';
  return 'passed';
}

export function syncRepo(cwd, push = false) {
  if (repoBusy(cwd)) return 'Sync stopped: merge/rebase already in progress; left untouched.';
  const status = tryGit(cwd, 'status', '--porcelain');
  if (!status.ok) return `Sync stopped: ${status.out}`;
  if (status.out) return 'Sync deferred: uncommitted work. Commit intended files, rerun tests, then sync; nothing pulled or pushed.';
  if (!tryGit(cwd, 'rev-parse', '@{u}').ok) return 'Sync deferred: no upstream. Commit and push -u the intended branch.';
  const before = tryGit(cwd, 'rev-parse', 'HEAD');
  const pulled = tryGit(cwd, 'pull', '--rebase');
  if (!pulled.ok) return `Sync stopped; nothing pushed. Resolve the reported Git failure:\n${pulled.out}`;
  const changed = tryGit(cwd, 'rev-parse', 'HEAD').out !== before.out;
  if (changed) return 'Pulled teammate changes; nothing pushed. Rerun tests against the synced tree before pushing.';
  if (!push || process.env.AEGIS_AUTOPUSH === '0') return 'Pulled: already up to date. Auto-push not requested/enabled.';
  const ahead = tryGit(cwd, 'rev-list', '--count', '@{u}..HEAD');
  if (!ahead.ok) return `Sync stopped: ${ahead.out}`;
  if (Number(ahead.out) === 0) return 'Tests passed; branch is already synchronized.';
  const pushed = tryGit(cwd, 'push');
  return pushed.ok ? 'Tests passed; committed work pushed. Update checklist and handoff.' : `Push failed:\n${pushed.out}`;
}

export function ruleContext(input) {
  const command = String(input.tool_input?.command ?? input.tool_input?.cmd ?? '');
  const paths = [...command.matchAll(/^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm)].map((match) => match[1]);
  for (const key of ['path', 'file_path', 'filePath']) {
    if (typeof input.tool_input?.[key] === 'string') paths.push(input.tool_input[key]);
  }
  const normalized = paths.map((path) => relative(root, resolve(input.cwd || root, path.replaceAll('\\', '/'))).replaceAll('\\', '/'));
  const rules = readdirSync(resolve(root, '.codex/rules')).filter((name) => name.endsWith('.md'));
  return rules.filter((name) => {
    if (!normalized.length) return true; // Shell paths can be computed; don't silently drop a safety rule.
    const patterns = [...read(`.codex/rules/${name}`).matchAll(/^\s+- "([^"]+)"/gm)].map((match) => match[1]);
    return patterns.some((pattern) => normalized.some((path) => pattern.endsWith('/**')
      ? path.startsWith(pattern.slice(0, -2)) : path === pattern));
  }).map((name) => read(`.codex/rules/${name}`)).join('\n\n');
}

export function secretPath(input) {
  const strings = (value) => typeof value === 'string' ? [value]
    : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : [];
  const text = strings(input.tool_input).join('\n').replaceAll('\\', '/');
  // Mirrors the Claude deny rules for direct file operations/commands. Managed permissions remain authoritative.
  return [...text.matchAll(/(?:^|[\s/"'])(\.env(?:\.[\w.-]+)?)(?=$|[\s"'])/g)]
    .some((match) => match[1] !== '.env.example');
}

export async function main() {
  let raw = '';
  for await (const chunk of process.stdin) raw += chunk;
  const input = raw.trim() ? JSON.parse(raw) : {};
  const defaults = JSON.parse(read('.codex/settings.json')).env ?? {};
  const localPath = resolve(root, '.codex/settings.local.json');
  const local = existsSync(localPath) ? JSON.parse(readFileSync(localPath, 'utf8')).env ?? {} : {};
  for (const [name, value] of Object.entries({ ...defaults, ...local })) {
    if (process.env[name] === undefined && typeof value === 'string') process.env[name] = value;
  }
  process.env.AEGIS_DEV = 'B';
  process.env.ECC_MEMORY_HARNESS = 'codex';
  const mode = process.argv[2];
  const cwd = input.cwd || root;
  if (mode === 'session') {
    const note = syncRepo(cwd);
    process.stdout.write(JSON.stringify(output('SessionStart', note + '\n' + brief(read('docs/CHECKLIST.md'), read('docs/SESSION_LOG.md')))));
  } else if (mode === 'test') {
    const result = testResult(input);
    if (result === 'unrelated') return;
    const note = result === 'passed' ? syncRepo(cwd, true)
      : `${result === 'failed' ? 'Tests FAILED' : 'Test completion unverified'}; nothing pulled or pushed. Inspect final results; fix or run an isolated test command before syncing.`;
    process.stdout.write(JSON.stringify(output('PostToolUse', note)));
  } else if (mode === 'rules') {
    if (secretPath(input)) {
      process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: 'PreToolUse',
        permissionDecision: 'deny', permissionDecisionReason: 'Copied Claude permissions deny access to real .env files; use .env.example.' } }));
    } else {
      process.stdout.write(JSON.stringify(output('PreToolUse', ruleContext(input))));
    }
  } else throw new Error('Expected session, test or rules');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { process.stderr.write(`AegisPay lifecycle: ${error.message}\n`); process.exitCode = 1; });
}
