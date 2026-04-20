# Releasing VoiceCheck

How to ship a new version to PyPI + GHCR. Automated via
`.github/workflows/release.yml` — fires on any `v*` tag push.

## One-time setup

These only need to happen once per project. Skip if already done.

### 1. PyPI Trusted Publisher

No API token in GitHub secrets — PyPI authenticates the release workflow
directly via OIDC.

1. Log in to https://pypi.org.
2. Go to your account → **Publishing** → **Add a new publisher** → **GitHub**.
3. Fill in:
   - **PyPI Project Name:** `voicecheck`
   - **Owner:** `sujitnoronha`
   - **Repository name:** `voicecheck`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
4. Save.

The first successful publish will register the project name. No pre-registration
needed.

### 2. GitHub Environment

The workflow references an environment called `pypi` to scope the OIDC exchange.

1. In the repo on GitHub → **Settings** → **Environments** → **New environment**.
2. Name it `pypi`. No secrets, no protection rules required.
3. Save.

### 3. GHCR

Nothing to configure. The workflow uses `GITHUB_TOKEN` with `packages: write`
to push `ghcr.io/sujitnoronha/voicecheck:<tag>`. The image becomes public the
first time it's pushed and the repo is public.

## Versioning

Semver. Bumps happen in `pyproject.toml` (`project.version`) and are noted in
`CHANGELOG.md`.

- **Patch** (`0.1.0` → `0.1.1`) — bug fixes, no API changes.
- **Minor** (`0.1.0` → `0.2.0`) — new evaluators, transports, CLI flags,
  anything additive.
- **Major** (`0.1.0` → `1.0.0`) — breaking YAML schema or Python API changes.

Pre-releases use `0.2.0a1`, `0.2.0b1`, `0.2.0rc1`. PyPI recognizes these as
pre-releases automatically.

## Release ritual

```bash
# 1. Make sure main is green and up to date.
git checkout main
git pull
# Confirm CI is green for the commit you're about to tag.

# 2. Bump the version. Example: 0.1.0 → 0.1.1.
#    Edit pyproject.toml `version = "0.1.1"`.
#    Update CHANGELOG.md — move "unreleased" items under a new
#    `## 0.1.1 — YYYY-MM-DD` heading.

# 3. Commit the bump.
git add pyproject.toml CHANGELOG.md
git commit -m "release: 0.1.1"

# 4. Tag and push.
git tag v0.1.1
git push origin main
git push origin v0.1.1
```

That's it. The tag push triggers `.github/workflows/release.yml`.

## What the workflow does

Four jobs, in order:

1. **`test`** — runs the unit suite (`pytest tests/unit/`) and validates every
   YAML in `examples/industries/`. Gates everything else.
2. **`build`** — builds wheel + sdist with `python -m build`, smoke-tests
   the wheel in a fresh venv, uploads as a `dist` artifact.
3. **`publish-pypi`** — downloads the artifact and publishes to PyPI via OIDC
   Trusted Publishing. Runs in the `pypi` environment.
4. **`publish-docker`** — builds the `Dockerfile` and pushes to GHCR tagged
   `v0.1.1`, `0.1.1`, `0.1`, and `latest`.

Total wall time: ~5 minutes. Docker build dominates (faster-whisper has
large wheels).

## Verifying a release

After the workflow finishes:

```bash
# PyPI: fresh venv, install from the index.
python -m venv /tmp/vcverify && source /tmp/vcverify/bin/activate
pip install 'voicecheck[all]==0.1.1'
voicecheck --version
voicecheck validate examples/industries/banking_support.yaml
deactivate

# GHCR: pull and run.
docker run --rm ghcr.io/sujitnoronha/voicecheck:0.1.1 --version
```

If both work, the release is good.

## If something goes wrong

**PyPI publish failed with `File already exists`.**
PyPI doesn't allow re-uploading a version. Bump to the next patch (e.g.
`0.1.2`) and re-tag. You can't replace `0.1.1` in-place.

**PyPI publish failed with `invalid-publisher` / `audience mismatch`.**
The Trusted Publisher config on PyPI doesn't match the workflow. Double-check
the environment name (`pypi`), workflow filename (`release.yml`), and repo
owner/name on the PyPI publisher settings page.

**Tag pushed but no workflow ran.**
The workflow triggers on tags matching `v*`. Check the tag name
(`v0.1.1` not `0.1.1`).

**Docker push failed with `denied`.**
The `GITHUB_TOKEN` needs `packages: write`. It's set in the workflow already,
but a repo-level setting can override it — check Settings → Actions → General
→ Workflow permissions → "Read and write permissions."

## Yanking a broken release

If a version ships with a critical bug:

1. On PyPI: go to the project page → Releases → click the bad version →
   **Yank release**. Yanked versions stay installable by exact pin but are
   excluded from default resolution.
2. On GHCR: delete the bad tag from the package settings.
3. Release a fix as the next patch version. Don't delete — PyPI does not
   allow deleting a version once it's been published, only yanking.
