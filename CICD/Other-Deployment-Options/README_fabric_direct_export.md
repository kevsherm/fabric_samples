# fabric_direct_export.py — Fabric Workspace Export

Exports all item definitions from a Microsoft Fabric workspace to local files
using the Fabric REST API, producing the same folder/file format as Fabric's
native Git integration. The output is committed to this repository and consumed
by our fabric-cicd deployment process.

## When to use this script

Use this export when the target Git platform has **no native Fabric Git
integration**. Fabric's built-in source control only supports Azure DevOps and
GitHub — for anything else, this script is the bridge:

- **GitLab**
- Bitbucket, Gitea, AWS CodeCommit, or any other Git host
- Air-gapped or self-hosted Git servers
- Ad-hoc snapshots of a workspace for backup or review

## When NOT to use this script

**Never run this export against a workspace that is (or was) connected to
Fabric's native Git integration, and never point it at a repo that native Git
integration also writes to.** Pick one sync mechanism per repo — this script
*or* native Git integration, never both.

Why this matters: Fabric tracks items in Git using a `logicalId` in each
item's `.platform` file. Native Git integration mints these IDs itself when a
workspace connects. This script can't retrieve those (they don't exist for
non-connected workspaces), so it injects the item's workspace GUID instead.
The two ID schemes are both valid but **incompatible**: if the same item gets
committed once by native sync and once by this script, its logicalId changes,
and downstream tooling (fabric-cicd) treats it as a deleted item plus a brand
new one — breaking deployment history and cross-item references.

If a workspace needs to move from native sync to this export (or back),
disconnect the old mechanism first and start from a clean branch.

## What the script does

1. Authenticates to the Fabric API (interactive browser sign-in)
2. Lists all workspace folders and items
   (`GET /v1/workspaces/{id}/folders`, `GET /v1/workspaces/{id}/items`)
3. For each item, calls `POST .../items/{id}/getDefinition` with Git-friendly
   formats (notebooks as `.py`, semantic models as TMDL, reports as PBIR),
   resolving the API's long-running-operation polling pattern
4. Base64-decodes each definition part to disk, mirroring the workspace
   folder hierarchy (`Sales/Y2024/MyNotebook.Notebook/...`)
5. Injects a `logicalId` (the item's workspace GUID) into any `.platform`
   file that has a blank one — required by fabric-cicd. Existing real
   logicalIds are never overwritten
6. Writes shell folders (`.platform` only) for types with no exportable
   definition (Lakehouse, Warehouse) — item metadata only; **data is never
   exported or versioned**

## Usage

```bash
# One-time setup
python -m venv .venv
.venv\Scripts\activate                # source .venv/bin/activate on macOS/Linux
python -m pip install requests azure-identity

# Export (full refresh — deletions in Fabric show up as deletions in Git)
python export.py --workspace-id <dev-workspace-guid> --clean

# Options
#   --workspace-id   Source workspace GUID (or set FABRIC_WORKSPACE_ID env var)
#   --output         Output folder, default ./workspace
#   --clean          Delete the output folder first (retries through
#                    OneDrive/antivirus file locks)
```

Requires at least **Contributor** on the source workspace. Items with a
protected sensitivity label return 403 and are skipped with a log line.

## Standard workflow

1. Make changes in the Fabric **dev** workspace
2. Run the export with `--clean`
3. Review the diff (`git status` / VS Code Source Control)
4. Commit to a feature branch, open a merge request
5. On merge, the CI pipeline deploys to test/prod via fabric-cicd

The dev workspace is the authoring surface; this repo is the source of truth;
test/prod workspaces are deploy targets only — no manual edits.

## Known limitations

- Cross-item references inside definitions contain raw dev-workspace GUIDs.
  Because the injected logicalId equals that same GUID, fabric-cicd rewires
  intra-workspace references automatically on deploy. References to anything
  *outside* the workspace (connections, other workspaces) must be mapped in
  `parameter.yml`.
- Moving an item between Fabric folders appears in Git as a delete + add of
  the item directory, not a rename.
- No incremental mode — every run is a full export; Git provides the change
  detection.
- The auth token lives ~1 hour; very large workspaces could hit expiry late
  in a run.
