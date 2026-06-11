"""
export.py — Sync Microsoft Fabric workspace items to local files.

Flow:
  1. GET  /v1/workspaces/{ws}/items                      (paginated list)
  2. POST /v1/workspaces/{ws}/items/{id}/getDefinition   (per item, with format)
  3. Poll /v1/operations/{opId} on 202, then GET .../result
  4. Base64-decode each definition part to disk

Usage:
  python export.py --workspace-id <GUID>
  python export.py --workspace-id <GUID> --output workspace --clean

Auth: opens a browser for Entra sign-in (your own account).
Dependencies: pip install requests azure-identity
"""

import argparse
import base64
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

import requests
from azure.identity import InteractiveBrowserCredential

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"

# Preferred export format per item type (Git-friendly choices).
# Types listed with None use the API default. Types NOT listed at all are
# attempted anyway; if Fabric says the type has no definition, we fall back
# to a shell folder (.platform only).
FORMAT_BY_TYPE = {
    "Notebook": None,            # default = notebook-content.py (diffs best)
    "DataPipeline": None,
    "SemanticModel": "TMDL",     # human-readable, one file per table
    "Report": "PBIR",            # split into small files, Git-friendly
    "SparkJobDefinition": "SparkJobDefinitionV1",
}

# Types known to have no exportable definition — shell folder only.
SHELL_TYPES = {"Lakehouse", "Warehouse", "SQLEndpoint", "MirroredWarehouse"}

POLL_FALLBACK_SECONDS = 3   # poll cadence ceiling-capped by Retry-After header
MAX_429_RETRIES = 5


def rmtree_robust(path: Path, attempts: int = 5) -> None:
    """Delete a folder tree, surviving Windows quirks:
    - clears read-only attributes that block deletion
    - retries through transient locks (OneDrive sync, antivirus scans)
    Works on Python 3.12+ (onexc) and older versions (onerror)."""

    def unlock_and_retry(func, p, exc_info):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    kwargs = (
        {"onexc": unlock_and_retry}
        if sys.version_info >= (3, 12)
        else {"onerror": unlock_and_retry}
    )

    for attempt in range(attempts):
        try:
            shutil.rmtree(path, **kwargs)
            return
        except PermissionError:
            if attempt == attempts - 1:
                print(
                    f"\nCould not delete '{path}' after {attempts} attempts.\n"
                    "Something is holding a lock on it (OneDrive sync, antivirus,\n"
                    "or a file open in another program). Close/pause those and retry,\n"
                    "or run without --clean to overwrite files in place."
                )
                raise
            print(f"  '{path}' is locked (OneDrive/AV?) — retrying in 2s "
                  f"({attempt + 1}/{attempts})...")
            time.sleep(2)


def get_session() -> requests.Session:
    print("Authenticating (browser window will open)...")
    token = InteractiveBrowserCredential().get_token(SCOPE).token
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def request_with_throttle(session: requests.Session, method: str, url: str) -> requests.Response:
    """Issue a request, honoring Retry-After on 429."""
    for attempt in range(MAX_429_RETRIES):
        resp = session.request(method, url, timeout=120)
        if resp.status_code != 429:
            return resp
        wait = int(resp.headers.get("Retry-After", "10"))
        print(f"  throttled (429) — waiting {wait}s")
        time.sleep(wait)
    resp.raise_for_status()
    return resp


def list_items(session: requests.Session, workspace_id: str) -> list[dict]:
    items, url = [], f"{API}/workspaces/{workspace_id}/items"
    while url:
        resp = request_with_throttle(session, "GET", url)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("value", []))
        url = data.get("continuationUri")
    return items


def list_folders(session: requests.Session, workspace_id: str) -> list[dict]:
    """List all workspace folders (recursive by default). Each folder has
    id, displayName, and parentFolderId (absent for root-level folders)."""
    folders, url = [], f"{API}/workspaces/{workspace_id}/folders"
    while url:
        resp = request_with_throttle(session, "GET", url)
        if resp.status_code == 404:
            # Workspace has no folders / API unavailable — flat layout.
            return []
        resp.raise_for_status()
        data = resp.json()
        folders.extend(data.get("value", []))
        url = data.get("continuationUri")
    return folders


def sanitize(name: str) -> str:
    """Strip characters Windows forbids in path segments."""
    return "".join(c for c in name if c not in '<>:"/\\|?*').rstrip(" .")


def build_folder_paths(folders: list[dict]) -> dict[str, Path]:
    """Map folderId -> relative Path by walking each folder's parent chain,
    mirroring the workspace folder hierarchy on disk."""
    by_id = {f["id"]: f for f in folders}
    paths: dict[str, Path] = {}

    def resolve(folder_id: str, seen: set[str]) -> Path:
        if folder_id in paths:
            return paths[folder_id]
        if folder_id in seen:           # defensive: cycle guard
            return Path(".")
        seen.add(folder_id)
        folder = by_id[folder_id]
        name = sanitize(folder["displayName"])
        parent_id = folder.get("parentFolderId")
        if parent_id and parent_id in by_id:
            path = resolve(parent_id, seen) / name
        else:
            path = Path(name)
        paths[folder_id] = path
        return path

    for fid in by_id:
        resolve(fid, set())
    return paths


def get_definition(session: requests.Session, workspace_id: str, item: dict) -> dict | None:
    """POST getDefinition and resolve the LRO. Returns the definition dict,
    or None if the item type has no definition / is label-blocked."""
    url = f"{API}/workspaces/{workspace_id}/items/{item['id']}/getDefinition"
    fmt = FORMAT_BY_TYPE.get(item["type"])
    if fmt:
        url += f"?format={fmt}"

    resp = request_with_throttle(session, "POST", url)

    if resp.status_code == 403:
        print("  skipped: 403 (permissions or protected sensitivity label)")
        return None
    if resp.status_code == 400:
        # Most common cause: this item type doesn't support getDefinition.
        print(f"  skipped: 400 ({resp.json().get('errorCode', 'no definition for type')})")
        return None
    resp.raise_for_status()

    # 200 = definition inline; 202 = long-running operation.
    if resp.status_code == 200:
        return resp.json()

    op_url = resp.headers["Location"]
    retry_after = min(int(resp.headers.get("Retry-After", "30")), POLL_FALLBACK_SECONDS)
    while True:
        time.sleep(retry_after)
        op_resp = request_with_throttle(session, "GET", op_url)
        op_resp.raise_for_status()
        status = op_resp.json().get("status")
        if status == "Succeeded":
            result = request_with_throttle(session, "GET", f"{op_url}/result")
            result.raise_for_status()
            return result.json()
        if status in ("Failed", "Cancelled"):
            print(f"  operation {status}: {json.dumps(op_resp.json().get('error', {}))}")
            return None


BLANK_GUID = "00000000-0000-0000-0000-000000000000"


def ensure_logical_id(folder: Path, item_id: str) -> bool:
    """Items exported from a non-Git-connected workspace come back with a
    blank logicalId in .platform (logicalIds are minted by Git integration,
    which we're bypassing). fabric-cicd requires a valid, unique logicalId
    per item, so inject the item's workspace GUID: unique, deterministic
    across re-exports, and stable through renames.
    Returns True if the file was modified."""
    platform_file = folder / ".platform"
    if not platform_file.exists():
        return False

    data = json.loads(platform_file.read_text(encoding="utf-8"))
    config = data.setdefault("config", {})
    current = config.get("logicalId", "")

    if current and current != BLANK_GUID:
        return False  # already has a real logicalId — never overwrite

    config["logicalId"] = item_id
    platform_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def write_parts(folder: Path, definition: dict) -> int:
    parts = definition["definition"]["parts"]
    for part in parts:
        target = folder / part["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(part["payload"]))
    return len(parts)


def write_shell(folder: Path, item: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {
            "type": item["type"],
            "displayName": item["displayName"],
            "description": item.get("description", ""),
        },
        "config": {"version": "2.0", "logicalId": item["id"]},
    }
    (folder / ".platform").write_text(json.dumps(platform, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Fabric workspace items to local files")
    parser.add_argument("--workspace-id", default=os.environ.get("FABRIC_WORKSPACE_ID"),
                        help="Workspace GUID (or set FABRIC_WORKSPACE_ID)")
    parser.add_argument("--output", default="workspace", help="Output folder (default: workspace)")
    parser.add_argument("--clean", action="store_true", help="Delete output folder before export")
    args = parser.parse_args()

    if not args.workspace_id:
        sys.exit("Missing workspace ID: pass --workspace-id or set FABRIC_WORKSPACE_ID")

    out = Path(args.output)
    if args.clean and out.exists():
        print(f"Cleaning {out.resolve()}...")
        rmtree_robust(out)
    out.mkdir(parents=True, exist_ok=True)

    session = get_session()

    print(f"Listing folders in workspace {args.workspace_id}...")
    folders = list_folders(session, args.workspace_id)
    folder_paths = build_folder_paths(folders)
    print(f"Found {len(folders)} folders")

    print(f"Listing items in workspace {args.workspace_id}...")
    items = list_items(session, args.workspace_id)
    print(f"Found {len(items)} items\n")

    ok = skipped = 0
    for item in items:
        label = f"{item['displayName']} ({item['type']})"

        # Mirror the workspace folder structure: items with a folderId go
        # under that folder's resolved path; others sit at the root.
        parent = folder_paths.get(item.get("folderId"), Path("."))
        folder = out / parent / f"{sanitize(item['displayName'])}.{item['type']}"
        if parent != Path("."):
            label += f"  [in {parent.as_posix()}]"

        if item["type"] in SHELL_TYPES:
            print(f"{label}: shell only")
            write_shell(folder, item)
            ok += 1
            continue

        print(f"{label}: exporting...")
        definition = get_definition(session, args.workspace_id, item)
        if definition:
            n = write_parts(folder, definition)
            fixed = ensure_logical_id(folder, item["id"])
            print(f"  -> {n} file(s)" + ("  [logicalId injected]" if fixed else ""))
            ok += 1
        else:
            skipped += 1

    print(f"\nDone. {ok} exported, {skipped} skipped. Output: {out.resolve()}")


if __name__ == "__main__":
    main()