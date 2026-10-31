# Releasing Story Studio

A release is a GitHub release `vX.Y.Z` on `soenderby/story-studio` with three assets:

| Asset | What it is |
| --- | --- |
| `StoryStudio-Setup-X.Y.Z.exe` | The Inno Setup installer |
| `latest.json` | Version, installer name, `sha256`, `size`, release notes, `publishedAt` (format: [API.md](API.md#updates-and-releases-phases-45)) |
| `latest.json.sig` | Ed25519 signature of the exact `latest.json` bytes, base64 on one line |

Installed apps read `releases/latest/download/latest.json` and its `.sig`, check the signature with the public key built into them (`src/story_studio/update_key.py`), then download the installer and check its size and SHA-256 before running it.

## How it works

| Piece | Role |
| --- | --- |
| `CHANGELOG.md` | Every change goes under `## Unreleased` as it is made. The release script turns that into `## X.Y.Z (date)` |
| `scripts/release.ps1` / `scripts/release.sh` | Run the checks, set the version, commit `Release X.Y.Z`, tag `vX.Y.Z`, then push after you confirm |
| `scripts/release_tool.py` | Shared helper (standard library only): versions, CHANGELOG edits, `latest.json`, public key |
| `.github/workflows/ci.yml` | Unit tests on Windows and Linux for every push and pull request |
| `.github/workflows/release.yml` | Runs on a pushed `v*.*.*` tag: build, sign and verify, then publish |

`release.yml` has three jobs:

1. **build** (Windows, read-only token, no secrets):
   - checks that the tag equals `v` + `__version__`, that `CHANGELOG.md` has a section with entries for that version and that the tagged commit is on `main`;
   - runs the tests;
   - installs the pinned Inno Setup 6, checked against its SHA-256 (the runner's own copy has no version number to check);
   - runs `packaging/build.ps1` and `packaging/smoke-test.ps1` (silent install, serve, every shutdown path, uninstall).
2. **sign** (Linux, read-only token): the only job that sees the `UPDATE_SIGNING_KEY` secret. It runs no third-party code, only Python's standard library and OpenSSL.
   - Writes `latest.json`.
   - Checks that the secret's public key is the key that installed apps verify with (`update_key.py` in the latest published release, or the current file before the first release).
   - Signs `latest.json`, then verifies the signature with that public key and checks the manifest against the installer.
   - The key exists only in a temporary file inside this job, which is deleted when the step ends. Uploads the three files as the workflow artifact `release-vX.Y.Z`.
3. **publish** (Linux, `contents: write`, publishing runs only): uploads the three files to a **draft** release, publishes it, then downloads the assets again and compares them byte for byte with the signed files. Prereleases are marked as prereleases. Stable releases are marked as Latest.

All actions are pinned to commit SHAs, and the workflow's default token permissions are none.

## Cutting a release

1. Make sure `## Unreleased` in `CHANGELOG.md` describes what changed, in words a user understands. These lines become the release notes in the app's update banner and on GitHub.
2. Dry run (changes nothing; reports every failing check):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\release.ps1 -DryRun 0.2.0
   ```
   ```sh
   scripts/release.sh --dry-run 0.2.0      # WSL / Linux
   ```

   It checks that:
   - you are on `main`, the tree is clean and `main` equals `origin/main`;
   - the tag is new and the tests pass;
   - the version is valid semver and greater than the current one;
   - `## Unreleased` has entries.

   It then shows the diff and the commands it would run.
3. Release: the same command without `-DryRun`/`--dry-run`. It commits `Release 0.2.0`, creates the annotated tag `v0.2.0`, then asks before it runs `git push --atomic origin main v0.2.0`. `-Yes`/`--yes` skips the question. If you answer no, nothing has left your machine. The script prints how to push later, or how to undo with `git tag -d v0.2.0` and `git reset --hard HEAD~1`.
4. Optional: before step 3, do a full dry run of the workflow (see below).

The version comes only from `src/story_studio/version.py`. Never edit it, or tag, by hand for a release: the workflow refuses a tag that doesn't match `__version__`.

## Watching and verifying a release

```sh
gh run list --workflow release.yml --limit 3
gh run watch <run-id>                     # or the Actions page the script printed
gh run view <run-id> --log-failed         # if it failed
gh release view v0.2.0
```

Independent check, from any machine with OpenSSL (the commands work in WSL):

```sh
mkdir check && cd check
gh release download v0.2.0 -R soenderby/story-studio
python3 ../scripts/release_tool.py public-key --out update_key.pem      # PEM from update_key.py
base64 -d latest.json.sig > sig.bin
openssl pkeyutl -verify -pubin -inkey update_key.pem -rawin -in latest.json -sigfile sig.bin
python3 ../scripts/release_tool.py verify-manifest latest.json --installer StoryStudio-Setup-0.2.0.exe
curl -fsSL https://github.com/soenderby/story-studio/releases/latest/download/latest.json   # what apps see
```

`openssl` must print `Signature Verified Successfully`, and `verify-manifest` must print `OK ... installer size and sha256 match`. Finally, an installed older version should show the update banner after **Check for updates** in its tray menu.

## Dry runs of the workflow

Actions → **Release** → **Run workflow**, or:

```sh
gh workflow run release.yml --ref main -f dry_run=true -f version=0.2.0
gh run download <run-id> -n release-v0.2.0
```

A dry run runs everything up to and including signing and verification, then uploads the three files as the artifact `release-vX.Y.Z` (kept for 14 days) and publishes nothing. `version` is optional. When set, the job applies the release edits (version and CHANGELOG section) to its own checkout first, so you can dry-run a version before cutting it. When empty, `version.py` must already have a CHANGELOG section. Running the workflow on a **tag** with `dry_run` unchecked publishes that tag, exactly like the tag push.

## If a release fails

Every step before the publish job has no effect outside the run, and the publish job is safe to re-run. Find the failed job in the run and pick the case:

| Failed in | What exists | What to do |
| --- | --- | --- |
| build or sign, transient (runner, network, GitHub outage) | the tag only | **Re-run all jobs** |
| build or sign, real problem (version or tag mismatch, failing test or smoke test, wrong key) | the tag only | Fix it on `main` (see below), then move the tag |
| publish | possibly a **draft** release with some assets | **Re-run failed jobs**. Within 14 days that reuses the signed files from the same run. A full re-run rebuilds and re-signs. Either way, the existing draft's assets are replaced (`--clobber`) and then it is published |
| the workflow says "already published" | a published release | Nothing to do. A published release is never changed by the workflow |

Moving a tag that was never published is safe, because no app has seen it. Fix the problem with a normal commit on `main`; if the change is user-visible, add a line to the `## X.Y.Z` section. Then:

```sh
git tag -d v0.2.0 && git push origin :refs/tags/v0.2.0       # remove the unpublished tag
git tag -a v0.2.0 -m "Story Studio 0.2.0" && git push origin v0.2.0
```

Check first that the release was not published (`gh release view v0.2.0` finds nothing, or only a draft). You can also skip the old version and release `0.2.1` with the script instead.

**A published release is never edited or re-uploaded.** Apps may be part-way through downloading it, and its signature and hashes are final. If it is broken, release a fixed patch version. If it is harmful, also turn it into a prerelease or delete it in the GitHub UI, which takes it out of `releases/latest`. The app does not downgrade, so installs that already took it stay on it until the fix.

## Prereleases

`scripts/release.ps1 0.3.0-beta.1` (any `X.Y.Z-<prerelease>` version) works like a normal release, with these differences:

- The CHANGELOG entries are **copied** into `## 0.3.0-beta.1 (date)`, and `## Unreleased` keeps them. So the stable `0.3.0` later collects everything since the last stable release, and its notes don't mention betas.
- GitHub marks it as a **prerelease**, so `releases/latest` (and therefore every installed app) ignores it. Testers install it manually from the release page.
- Semver order applies: `0.3.0-beta.1 < 0.3.0-beta.2 < 0.3.0`. An app running a beta is offered the stable `0.3.0` once it is released.

## The signing key

The private key (Ed25519, PEM) is what makes in-app updates safe. Whoever has it, and can also put files on the release (for example with a stolen GitHub login), can push any program to every installed copy. Without it, nobody can ship an update that installed apps will accept.

- It exists only in the GitHub Actions secret `UPDATE_SIGNING_KEY` and in the maintainer's offline backup. It is never committed (`.gitignore` excludes `*.pem` and `*.key`), never printed, and never copied into the repository or a workflow artifact.
- Its public half is `PUBLIC_KEY` in `src/story_studio/update_key.py` (base64 of the raw 32 bytes): `python3 scripts/release_tool.py public-key --format raw`.
- Check that the backup still matches without printing it: `openssl pkey -in <backup.pem> -pubout -outform DER | tail -c 32 | base64` must equal `PUBLIC_KEY`.
- **If the key is lost**, installed apps can no longer be updated. Generate a new key, put it in the app (`update_key.py`) and the secret, and ask users to download and run the new installer by hand once.
- **If the key leaks**, rotate it as soon as possible (below). If installs must not trust the old key a day longer, skip the bridge: users reinstall by hand, as for a lost key.

### Rotating the key

Every installed app trusts exactly one key, the one built into it. A release signed with a new key is rejected by every app that still has the old one, which would strand all existing installs. So a rotation goes through a **bridge release**: signed with the **old** key, so current installs accept it, and embedding the **new** key.

1. Generate the new key offline, outside the repository: `openssl genpkey -algorithm ed25519 -out <new-key.pem>`. Back it up. Its public key is `openssl pkey -in <new-key.pem> -pubout -outform DER | tail -c 32 | base64`.
2. Put that value in `PUBLIC_KEY` in `src/story_studio/update_key.py`, add a CHANGELOG line, and merge to `main`. Keep the old key in the `UPDATE_SIGNING_KEY` secret for now.
3. Allow the key change for exactly this version: `gh variable set KEY_ROTATION_RELEASE --body v0.5.0`. Without that variable, the sign job refuses a release whose `update_key.py` differs from the latest published release.
4. Release `0.5.0` as usual. The sign job checks the secret against the key in the previous release (the old key), signs with it and logs a "Key rotation bridge release" warning.
5. Replace the secret with the new key: `gh secret set UPDATE_SIGNING_KEY < <new-key.pem>`. Then remove the variable: `gh variable delete KEY_ROTATION_RELEASE`. From now on, the sign job expects the key that is in `v0.5.0`, which is the new one.
6. **Wait before releasing again.** Installs that update while `0.5.0` is the latest release move to the new key. Installs that are still older when the next release (signed with the new key) appears will reject it and be stranded: their users have to download and run the new installer by hand once. Leave the bridge release as the latest for as long as practical (weeks, not hours), and mention the manual path in the next release's notes.

If you ever need smoother rotations, the app would have to accept a list of keys, and the bridge release would then ship both. Today's `update_key.py` holds a single key.

## First install and SmartScreen

The installer and `StoryStudio.exe` are not code-signed. The first manual install therefore shows Windows SmartScreen's "Windows protected your PC" (**More info → Run anyway**), and browsers may warn about the download. In-app updates don't show this warning, because the app downloads and verifies the installer itself. A code-signing certificate or Azure Trusted Signing would remove the warning (see [PLAN.md](PLAN.md), "Known caveats").

## Before the first release (0.2.0)

- Merge the updater (phase 4), so `0.2.0` is able to update itself to later versions.
- The `UPDATE_SIGNING_KEY` secret exists and its public key is in `update_key.py`. The sign job checks this on every run.
- Keep an offline backup of the private key, and check it as shown above.
- Optional hardening: a tag ruleset that lets only the maintainer create `v*` tags, and branch protection on `main` that requires the CI checks.
