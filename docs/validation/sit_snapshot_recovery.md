# Official SIT snapshot recovery report

```
snapshot_recovery_status = UNRECOVERABLE
```

## Scope and immutable identity

This recovery attempt was limited to locating the exact payload bytes recorded by
`data/snapshots/sit-20260719/manifest.json`. The committed manifest was not
changed. Its file SHA-256 remains
`682e83a62e8acc3d4ef3a45c32174a6eff2e668df101bb12e02104d847141643`, and
`SnapshotManifest.hash` remains
`16ecae299c7944302c0bffe3688bf9bdb2b931012a82d3dd47c79c36778fabfe`.

All nine required files are missing:

| File | Required SHA-256 |
| --- | --- |
| `159915.csv` | `6139967d17fecb4cb84ac8fa1034602fc61d3edfb04e43d5b3e662ffb25fe570` |
| `510300.csv` | `2cb9b8ffef2d2de1b42cf879f203bbcd80f84804bbf01bf30565da201e4b6408` |
| `510500.csv` | `71c7063e984e0f74e257145d57935e6463432bbc135819553ee4fe8388e9ac57` |
| `511010.csv` | `17275886de8377d5ad7ca87dc53fa248966afef2a021987f76abe914b3f94613` |
| `512100.csv` | `af5b5fbb915d20d10a74c9db7f23e383f5d20eac1f2dde1839be5b0889f72c93` |
| `512480.csv` | `e8d9516076913cd0a145e4e086d4d398958825947d61ba5dc7326402a88c86ef` |
| `512880.csv` | `8410580b3c10ffdf780532fee0bd8b4220545e7c3aa11e18084ea412f09a9acd` |
| `513100.csv` | `d4d0d2c9e0babe70e428a644b70abe9ed54f514760a4d71936599e26172a3c6d` |
| `518880.csv` | `1bca5f49e7dbe7f8fcddd833d6065e5cf7d1e0e184c42942aa8eb3394b754086` |

## Recovery sources searched

The sources were checked in the required order on 2026-07-19 UTC:

1. **Local PR #15 execution machine/workspace.** The accessible workspace,
   repository, ignored data directories, Git objects, reflogs, `/workspace`,
   `/tmp`, and `/root` were searched for the nine filenames, the snapshot name,
   the recorded payload hashes, and likely snapshot, backup, archive, artifact,
   and result packages. Only the committed manifest was present. No payload or
   matching package was found.
2. **Retained Codex task workspaces/artifacts.** The accessible Codex home and
   retained session/task locations were searched using the same identifiers. No
   retained PR #15 payload or artifact was available.
3. **GitHub Actions artifacts.** The successful PR #15 workflow run
   [`29685667314`](https://github.com/jheyyeh1-hub/kelaode/actions/runs/29685667314)
   (head commit `40a860d81c0ebd7464d1fa8c6141d1cc0803b722`) reports zero artifacts through
   the GitHub Actions artifacts API. Its workflow only checked out the repository,
   installed dependencies, and ran tests/checks; it did not upload the ignored
   CSV payloads.
4. **Local backups, archives, and official-result packages.** The accessible
   local filesystem contained no candidate bearing the required filenames,
   hashes, or snapshot identifier.
5. **Immutable object stores and release assets.** The repository has no GitHub
   Release assets, and neither the repository nor the official SIT records name
   a pinned immutable object-store package from the run.

The PR #15 Git change itself added only the manifest, not any of the nine CSVs.
The repository ignore rules excluded `data/snapshots/`, so the execution
workspace was the only known location of those uncommitted bytes. That workspace
is not among the retained material available to this recovery attempt.

## Consequences

No file was recovered, so there is no truthful provenance record, immutable
package location, package SHA-256, materialization command, or complete directory
on which `SnapshotManifest.validate(...)` can succeed. Adding a materializer or
CI success-path tests would falsely imply that the payload package exists.

No market data was downloaded. No regenerated, normalized, reformatted,
reordered, rounded, or otherwise substituted data was accepted. In particular,
the provider and adjustment metadata in the manifest are evidence about the
missing inputs, not permission to query AKShare/Eastmoney for replacement bytes.
Any future acquisition must first preregister a **new snapshot identity** and
must not reuse the official SIT identity above.

This recovery report does not change the official SIT protocol or result files,
the frozen TSMOM protocol, parameters, dates, costs, benchmark, selection rules,
or judgment thresholds. It does not revise the failed-attempt facts recorded by
PR #18. No TSMOM fixed selection, frozen testing, walk-forward validation, or
economic analysis was run.
