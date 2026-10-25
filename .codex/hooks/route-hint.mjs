#!/usr/bin/env node
// UserPromptSubmit hook: match the prompt against .claude/routing.json and
// tell both the human (systemMessage) and Claude (additionalContext) which
// agent / skills / rules handle it. The table decides, not the model.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { readStdin, emit, projectDir } from './lib.mjs';

const MAX_ROUTES = 3;
const MAX_PROMPT_CHARS = 4000; // bound regex work on huge pasted prompts

const input = await readStdin();
const prompt = String(input.prompt ?? '').slice(0, MAX_PROMPT_CHARS);

// Explicit choices win: slash commands and @agent-<name> mentions skip routing.
if (!prompt.trim() || /^\s*\//.test(prompt) || /(^|\s)@agent-[\w:-]+/i.test(prompt)) process.exit(0);

let routes;
try {
  routes = JSON.parse(readFileSync(join(projectDir(input), '.claude', 'routing.json'), 'utf8')).routes;
} catch (err) {
  emit({ systemMessage: `route-hint: cannot read .claude/routing.json (${err.message})` });
  process.exit(0);
}

const bad = [];
const matches = (r) => {
  try {
    return new RegExp(r.match, 'i').test(prompt);
  } catch {
    bad.push(r.id);
    return false;
  }
};
const hits = routes.filter(matches).slice(0, MAX_ROUTES);
if (hits.length === 0) {
  if (bad.length) emit({ systemMessage: `route-hint: invalid regex in routing.json route(s): ${bad.join(', ')}` });
  process.exit(0);
}

const list = (name, v) => (v?.length ? `${name}=${v.join(',')}` : null);
const line = (r) =>
  [`[${r.id}]`, r.agent && `agent=${r.agent}`, list('review', r.review), list('skills', r.skills), list('rules', r.rules)]
    .filter(Boolean)
    .join(' ');

const context = [
  'ROUTING (from .claude/routing.json — follow it unless the user overrides):',
  ...hits.map((r) => `- ${line(r)} — ${r.note}`),
  'Delegate implementation to the listed agent via the Agent tool, load the listed skills with the Skill tool,',
  'and run the listed review agents before declaring the work done. Path rules in .claude/rules/ load automatically.',
].join('\n');

emit({
  systemMessage: `route → ${hits.map(line).join('  |  ')}`,
  hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: context },
});
