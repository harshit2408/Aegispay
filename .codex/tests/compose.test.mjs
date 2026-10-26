import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const read = (path) => readFileSync(resolve(root, path), 'utf8');
const variables = (source) => Object.fromEntries(
  [...source.matchAll(/^([A-Z][A-Z0-9_]*)=(.*)$/gm)]
    .map(([, key, value]) => [key, value.split(/\s+#/)[0].trim()]),
);

test('environment template covers every PRD section 7 variable with empty secrets', () => {
  const section = read('docs/PRD_SRS.md').split('## 7. Environment configuration')[1]
    .split('\n## 8.')[0];
  const expected = variables(section);
  const actual = variables(read('.env.example'));
  assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort());
  for (const [key, value] of Object.entries(expected)) {
    assert.equal(actual[key], value, key);
  }
});

const composeVersion = spawnSync('docker', ['compose', 'version'], { encoding: 'utf8' });
test('compose excludes settlement secrets even when host env contains them', {
  skip: composeVersion.status !== 0 && 'Docker Compose CLI not installed',
}, () => {
  const forbidden = [
    'TREASURY_CLIENT_ID', 'TREASURY_CLIENT_SECRET', 'TREASURY_ACCOUNT_EMAIL',
    'SETTLEMENT_DATABASE_URL', 'AUDIT_HMAC_KEY', 'AUDIT_ANCHOR_REPO',
    'VELOCITY_MAX_PAYOUTS_PER_HOUR', 'VELOCITY_MAX_CENTS_PER_HOUR', 'ALERT_WEBHOOK_URL',
  ];
  const result = spawnSync('docker', [
    'compose', '--env-file', '.env.example', '--profile', 'frontend',
    'config', '--format', 'json',
  ], {
    cwd: root,
    encoding: 'utf8',
    env: {
      ...process.env,
      ...variables(read('.env.example')),
      ...Object.fromEntries(forbidden.map((key) => [key, 'test-settlement-only'])),
      NEXT_PUBLIC_AG_LICENSE_KEY: 'test-render-only',
      UNRELATED_SECRET: 'test-must-not-forward',
    },
  });
  assert.equal(result.status, 0, result.stderr);
  const { services } = JSON.parse(result.stdout);
  for (const service of Object.values(services)) {
    assert.equal(service.env_file, undefined);
    const config = JSON.stringify(service);
    assert.equal(config.includes('test-settlement-only'), false);
    assert.equal(config.includes('test-render-only'), false);
    assert.equal(config.includes('test-must-not-forward'), false);
  }
  for (const key of forbidden) assert.equal(key in services.api.environment, false, key);
  assert.equal(services.api.volumes, undefined, 'Do not mount host files into the API');
  assert.deepEqual(Object.keys(services.api.environment).sort(),
    Object.keys(variables(read('.env.example'))).filter((key) => ![
      ...forbidden, 'NEXT_PUBLIC_API_URL', 'NEXT_PUBLIC_AG_LICENSE_KEY',
    ].includes(key)).sort());
  assert.equal(services.api.environment.DATABASE_URL,
    'postgresql+asyncpg://aegis_api:aegis@postgres:5432/aegis');
  assert.equal(services.api.depends_on.postgres.condition, 'service_healthy');
  assert.ok(services.api.healthcheck.test);
  assert.deepEqual(Object.keys(services.frontend.build.args), ['NEXT_PUBLIC_API_URL']);
  assert.deepEqual(services.frontend.profiles, ['frontend']);
});

test('plain compose up defaults non-secret API settings to the PRD section 7 values', {
  skip: composeVersion.status !== 0 && 'Docker Compose CLI not installed',
}, () => {
  const template = variables(read('.env.example'));
  const env = Object.fromEntries(Object.entries(process.env)
    .filter(([key]) => !(key in template)));
  const result = spawnSync('docker', [
    'compose', '--env-file', '/dev/null', 'config', '--format', 'json',
  ], { cwd: root, encoding: 'utf8', env });
  assert.equal(result.status, 0, result.stderr);
  const api = JSON.parse(result.stdout).services.api.environment;
  for (const key of ['LLM_PROVIDER', 'LLM_MODEL_EXTRACT', 'LLM_MODEL_REASON']) {
    assert.equal(api[key], template[key], key);
  }
});
