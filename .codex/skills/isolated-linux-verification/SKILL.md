---
name: isolated-linux-verification
description: Verify tracked backend code with Linux Python 3.12 and disposable PostgreSQL 16 when Windows native modules are blocked or host isolation is needed.
---
# Isolated Linux backend verification

## When to use
Use for backend verification when Windows AppControl blocks a native DLL, or to reproduce CI without host credentials. Keep host security unchanged. This tests backend behavior; runtime-only image boot and frontend checks remain separate.

## Steps
Run from the repository root in PowerShell. Commit the intended backend changes first. For an uncommitted reproduction, overlay only named intended backend files into the archive and record those paths; never copy the whole worktree.

```powershell
$verifyId = 'aegis-verify-' + [guid]::NewGuid().ToString('N')
$verifyDir = Join-Path ([IO.Path]::GetTempPath()) $verifyId
$verifyPg = $verifyId + '-pg'
$verifyJob = $verifyId + '-test'
New-Item -ItemType Directory -Path $verifyDir -ErrorAction Stop | Out-Null
git archive --output="$verifyDir/source.tar" HEAD backend
if ($LASTEXITCODE -ne 0) { throw 'Archive failed' }
tar -xf "$verifyDir/source.tar" -C "$verifyDir"
if ($LASTEXITCODE -ne 0) { throw 'Extract failed' }
@'
FROM python:3.12-slim
COPY backend /test/backend
RUN pip install --no-cache-dir "/test/backend[dev]"
WORKDIR /test
CMD ["python", "-m", "pytest", "backend", "-q"]
'@ | Set-Content -LiteralPath "$verifyDir/Dockerfile" -Encoding ascii
try {
    docker build -t "$verifyId" "$verifyDir"
    if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
    docker network create "$verifyId"
    if ($LASTEXITCODE -ne 0) { throw 'Network failed' }
    docker run -d --name "$verifyPg" --network "$verifyId" --network-alias pg --tmpfs /var/lib/postgresql/data -e POSTGRES_PASSWORD=verify-only --health-cmd 'pg_isready -U postgres' --health-interval 1s --health-timeout 2s --health-retries 30 postgres:16
    if ($LASTEXITCODE -ne 0) { throw 'Postgres failed' }
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $health = docker inspect --format '{{.State.Health.Status}}' "$verifyPg"
        if ($LASTEXITCODE -ne 0) { throw 'Health inspection failed' }
        if ($health -eq 'healthy') { break }
        Start-Sleep -Seconds 1
    }
    if ($health -ne 'healthy') { throw 'Postgres did not become healthy' }
    docker run --rm "$verifyId" python -m ruff check backend
    if ($LASTEXITCODE -ne 0) { throw 'Ruff failed' }
    docker run --name "$verifyJob" --network "$verifyId" -e CI=true -e TEST_PG_URL=postgresql+asyncpg://postgres:verify-only@pg:5432 "$verifyId"
    $verifyExit = $LASTEXITCODE
    if ($verifyExit -ne 0) { throw "Pytest failed: $verifyExit" }
} finally {
    docker rm -f "$verifyJob" "$verifyPg"
    docker network rm "$verifyId"
    docker image rm "$verifyId"
}
```

All names are unique and owned by this run; Postgres data lives in disposable tmpfs. Missing-resource cleanup messages after setup failure are expected. Do not prune Docker or touch another worker's resources. Keep the temporary archive for diagnosis; remove only its verified absolute directory if cleanup is needed.

## Verify
Record commit, any explicit overlays, Python/Postgres versions, final test count and exit code. `CI=true` makes unavailable PostgreSQL fail instead of skip for the integration fixture. A running command, missing exit code, skipped DB suite or failure summary is not a pass. Review the actual log before Git sync. No environment files, credential stores, home directories or host mounts enter the image. Re-run after code changes or a merge that changes tested code.

## Gotchas
See team memories mem_20261026_e5a2118492a34b25814b (runtime dependencies and Compose paths) and mem_20261026_fa1171b7107840dfa532 (audit transaction/ORM freshness). A `[dev]` test image can hide missing runtime dependencies; separately smoke-test the production image. This procedure repeats the P0-17 and P1-09/P1-11 verification pattern; task state stays in `docs/CHECKLIST.md`.
