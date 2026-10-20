const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const repo = path.resolve(__dirname, '..');
const runtimeEnv = fs.readFileSync(path.join(repo, '.github/ci.env'), 'utf8');

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'podcast-deploy-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const app = path.join(root, 'app with spaces');
  const fakeBin = path.join(root, 'fake-bin');
  fs.mkdirSync(app);
  fs.mkdirSync(fakeBin);
  fs.cpSync(path.join(repo, 'etc/bin'), path.join(app, 'bin'), { recursive: true });
  fs.copyFileSync(path.join(repo, 'etc/docker-compose.yml'), path.join(app, 'docker-compose.yml'));
  fs.writeFileSync(path.join(app, '.env'), runtimeEnv);
  fs.writeFileSync(path.join(app, '.version'), 'DOCKER_IMAGE=example/app:1.2.3\nDOCKER_IMAGE_TAG=1.2.3\n');
  const calls = path.join(root, 'calls');
  const stub = `#!/bin/sh
printf '%s\\n' "$0 $*" >> "$CALLS"
case "$*" in
  *" up "*) if [ "\${FAIL_UP:-}" = 1 ]; then exit 7; fi ;;
  *" pull"*) if [ "\${FAIL_PULL:-}" = 1 ]; then exit 8; fi ;;
  *" exec "*) if [ "\${FAIL_HEALTH:-}" = 1 ]; then exit 9; fi ;;
esac
`;
  for (const executable of ['docker', 'systemctl']) {
    fs.writeFileSync(path.join(fakeBin, executable), stub, { mode: 0o755 });
  }
  fs.writeFileSync(path.join(fakeBin, 'sudo'), '#!/bin/sh\nif [ "$1" = -n ]; then shift; fi\nexec "$@"\n', { mode: 0o755 });
  function run(script, args = [], extra = {}) {
    return spawnSync('bash', [path.join(app, 'bin', script), ...args], {
      cwd: root,
      env: { ...process.env, PATH: `${fakeBin}:${process.env.PATH}`, CALLS: calls, ...extra },
      encoding: 'utf8',
    });
  }
  return { root, app, run, calls: () => fs.existsSync(calls) ? fs.readFileSync(calls, 'utf8') : '' };
}

test('start works outside the app directory and waits for both services', (t) => {
  const f = fixture(t);
  const result = f.run('start');
  assert.equal(result.status, 0, result.stderr);
  assert.ok(f.calls().includes(`--project-directory ${f.app}`));
  assert.ok(f.calls().includes(`--env-file ${f.app}/.env --env-file ${f.app}/.version`));
  assert.match(f.calls(), /up --detach --no-build --pull never --wait --wait-timeout 240/);
  assert.match(f.calls(), /exec -T web python -m src.healthcheck web/);
  assert.match(f.calls(), /exec -T background python -m src.healthcheck background/);
});

test('a readiness timeout fails startup and prints diagnostics', (t) => {
  const f = fixture(t);
  const result = f.run('start', [], { FAIL_UP: '1' });
  assert.notEqual(result.status, 0);
  assert.match(f.calls(), /logs --no-color --tail 80/);
});

test('failed pull does not restart the running service', (t) => {
  const f = fixture(t);
  assert.notEqual(f.run('deploy', [], { FAIL_PULL: '1' }).status, 0);
  assert.doesNotMatch(f.calls(), /systemctl restart/);
});

test('deployment fails when a service is not ready after restart', (t) => {
  const f = fixture(t);
  assert.notEqual(f.run('deploy', [], { FAIL_HEALTH: '1' }).status, 0);
  assert.match(f.calls(), /systemctl restart podcast-app.service/);
  assert.match(f.calls(), /logs --no-color --tail 80/);
});

test('stop only tears down the app and does not prune shared resources', (t) => {
  const f = fixture(t);
  assert.equal(f.run('stop').status, 0);
  assert.match(f.calls(), /down --timeout 120/);
  assert.doesNotMatch(f.calls(), /prune|--volumes|network rm/);
});

test('dotenv content is not executed and a successful deployment preserves secrets', (t) => {
  const f = fixture(t);
  const sentinel = path.join(f.root, 'must-not-exist');
  const content = `APP_SECRET_KEY='$(touch "${sentinel}")'\n`;
  fs.writeFileSync(path.join(f.app, '.env'), content);
  assert.equal(f.run('deploy').status, 0);
  assert.equal(fs.existsSync(sentinel), false);
  assert.equal(fs.readFileSync(path.join(f.app, '.env'), 'utf8'), content);
});

test('missing configuration fails before starting containers', (t) => {
  const f = fixture(t);
  fs.unlinkSync(path.join(f.app, '.version'));
  const result = f.run('start');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Missing .*\.version/);
  assert.equal(f.calls(), '');
});

test('release kit contains the matching version, scripts, and instructions but no .env', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'podcast-package-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const archive = path.join(root, 'release.tar.gz');
  const result = spawnSync('bash', [path.join(repo, '.github/scripts/package-release'), 'example/app:1.2.3', '1.2.3', archive], { cwd: repo, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(spawnSync('tar', ['-xzf', archive, '-C', root]).status, 0);
  assert.equal(fs.existsSync(path.join(root, '.env')), false);
  assert.equal(fs.readFileSync(path.join(root, '.version'), 'utf8'), 'DOCKER_IMAGE=example/app:1.2.3\nDOCKER_IMAGE_TAG=1.2.3\n');
  for (const name of ['docker-compose.yml', '.env.template', 'podcast-app.service', 'nginx.conf', 'DEPLOYMENT.md', 'bin/common', 'bin/start', 'bin/stop', 'bin/deploy', 'bin/service']) {
    assert.ok(fs.existsSync(path.join(root, name)), name);
  }
});

test('real Compose uses the selected version, preserves quoted secrets, and only joins storage', (t) => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.app, '.env'), runtimeEnv + "\nAPP_SECRET_KEY='spaces and $literal'\nDOCKER_IMAGE=wrong:old\nAPP_PORT=8013\n");
  const result = spawnSync('bash', ['-c', 'source "$1/bin/common"; compose config --format json', 'check', f.app], {
    cwd: f.root,
    env: { ...process.env, DOCKER_IMAGE: 'wrong:exported', DOCKER_IMAGE_TAG: 'wrong' },
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  const config = JSON.parse(result.stdout);
  assert.deepEqual(Object.keys(config.services).sort(), ['background', 'web']);
  assert.equal(config.networks.storage.external, true);
  assert.equal(config.networks.storage.name, 'storage');
  for (const service of Object.values(config.services)) {
    assert.equal(service.image, 'example/app:1.2.3');
    assert.equal(service.environment.APP_VERSION, '1.2.3');
    // Compose escapes literal dollars when serializing reusable config output.
    assert.equal(service.environment.APP_SECRET_KEY, 'spaces and $$literal');
  }
  assert.equal(config.services.web.ports[0].published, '8013');
  assert.equal(config.services.web.ports[0].host_ip, '127.0.0.1');
});

for (const [architectures, succeeds] of [
  [['amd64', 'arm64'], true],
  [['amd64'], false],
  [['arm64'], false],
  [['amd64', 'unknown'], false],
]) {
  test(`manifest gate checks platform coverage: ${architectures.join(',')}`, (t) => {
    const f = fixture(t);
    const bin = path.join(f.root, 'manifest-bin');
    fs.mkdirSync(bin);
    const manifest = { manifests: architectures.map((architecture) => ({ platform: { os: architecture === 'unknown' ? 'unknown' : 'linux', architecture } })) };
    const snapshot = path.join(f.root, 'manifest.json');
    fs.writeFileSync(snapshot, JSON.stringify(manifest));
    fs.writeFileSync(path.join(bin, 'docker'), '#!/bin/sh\ncat "$MANIFEST"\n', { mode: 0o755 });
    const result = spawnSync('bash', [path.join(repo, '.github/scripts/verify-platforms'), 'example/app:1.2.3'], {
      env: { ...process.env, PATH: `${bin}:${process.env.PATH}`, MANIFEST: snapshot },
      encoding: 'utf8',
    });
    assert.equal(result.status === 0, succeeds, result.stderr);
  });
}
