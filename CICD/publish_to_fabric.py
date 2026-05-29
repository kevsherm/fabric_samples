# =============================================================================
# publish_to_fabric.py
#
# Deploys ALL Fabric items found in the repository directory to a target
# Microsoft Fabric workspace. Designed to be called from an Azure DevOps
# pipeline but can also be run locally with az login credentials.
#
# Authentication uses DefaultAzureCredential (azure-identity), which
# automatically picks up credentials from environment variables (service
# principal), managed identity, Azure CLI, and more — no code changes needed
# between local and pipeline runs.
#
# Usage (pipeline):
#   python publish_to_fabric.py
#     --WorkspaceId      "<workspace-guid>"
#     --WorkspaceName    "<workspace-display-name>"
#     --Environment      "PPE|PROD"
#     --RepositoryDirectory "<path-to-fabric-items>"
#     --OutputJsonPath   "<path-for-output-json>"
#     --SkipUnpublish    "false"
#
# Usage (local):
#   Run `az login` first, then call the script with the args above.
# =============================================================================

import argparse, os, json
from pathlib import Path
from azure.identity import DefaultAzureCredential
from fabric_cicd import FabricWorkspace, publish_all_items, unpublish_all_orphan_items, change_log_level


# -----------------------------------------------------------------------------
# Argument parsing
# Accepts workspace details and paths from the calling pipeline (or CLI).
# -----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Deploy all Fabric items from a repository to a workspace")
    p.add_argument("--WorkspaceId",         required=True,   help="GUID of the target Fabric workspace")
    p.add_argument("--RepositoryDirectory", required=True,   help="Local path to the folder containing Fabric items")
    p.add_argument("--Environment",         default="N/A",   help="Environment label, e.g. PPE or PROD")
    p.add_argument("--SkipUnpublish",       default="false", help="Set to 'true' to skip removing orphaned items from the workspace")
    p.add_argument("--WorkspaceName",       default="",      help="Display name of the workspace (used in the deployment summary)")
    p.add_argument("--OutputJsonPath",      default="",      help="File path to write the deployed items JSON artifact")
    return p.parse_args()


# -----------------------------------------------------------------------------
# Repository scanner
# Walks the repository directory and finds every folder that matches the
# 'ItemName.ItemType' naming convention used by Fabric's Git integration
# (e.g. 'SalesNotebook.Notebook', 'InventoryModel.SemanticModel').
# Returns a sorted list of dicts ready for reporting.
# -----------------------------------------------------------------------------
def scan_deployed_items(repo_dir: Path) -> list[dict]:
    items = []
    seen = set()
    for root, dirs, _ in os.walk(repo_dir):
        for d in dirs:
            if "." not in d:
                continue  # Not a Fabric item folder — skip
            name, typ = d.rsplit(".", 1)
            if name and typ and d not in seen:
                seen.add(d)
                items.append({"itemName": name, "itemType": typ})
    # Sort by type then name for consistent, readable output
    return sorted(items, key=lambda x: (x["itemType"], x["itemName"]))


# -----------------------------------------------------------------------------
# JSON artifact writer
# Emits a machine-readable record of everything that was deployed.
# Useful for downstream pipeline steps, release gates, or audit trails.
# -----------------------------------------------------------------------------
def write_deployed_json(path: str, workspace_id: str, workspace_name: str, items: list[dict]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = [{"workspaceId": workspace_id, "workspaceName": workspace_name, **i} for i in items]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[info] Wrote deployed items list to {path}")


# -----------------------------------------------------------------------------
# Markdown summary writer
# Generates a human-readable deployment summary grouped by item type.
# The YAML pipeline picks this file up and surfaces it as the pipeline's
# built-in Summary tab in Azure DevOps via ##vso[task.uploadsummary].
# -----------------------------------------------------------------------------
def write_markdown_summary(path: str, workspace_name: str, environment: str, items: list[dict]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Group items by their type for a clean section-per-type layout
    by_type: dict[str, list[str]] = {}
    for i in items:
        by_type.setdefault(i["itemType"], []).append(i["itemName"])

    lines = [
        "# Fabric Deployment Summary",
        "",
        "| | |",
        "|---|---|",
        f"| **Workspace** | {workspace_name} |",
        f"| **Environment** | {environment} |",
        f"| **Total items deployed** | {len(items)} |",
        "",
    ]

    if by_type:
        lines += ["## Items Deployed", ""]
        for typ, names in sorted(by_type.items()):
            lines.append(f"### {typ} ({len(names)})")
            lines.append("")
            for name in sorted(names):
                lines.append(f"- {name}")
            lines.append("")
    else:
        lines += ["_No items were deployed._", ""]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[info] Wrote markdown summary to {path}")


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------
def main():
    args = parse_args()

    # Enable DEBUG logging so every Fabric API call is visible in the pipeline log
    change_log_level("DEBUG")

    # Convert the SkipUnpublish string argument ("true"/"false") to a boolean
    skip_unpublish = args.SkipUnpublish.strip().lower() == "true"

    print(f"[info] Target workspace : {args.WorkspaceId} (env={args.Environment})")
    print(f"[info] Repository dir   : {args.RepositoryDirectory}")
    print(f"[info] Item type scope  : ALL (item_type_in_scope omitted — library default)")
    print(f"[info] Skip unpublish   : {skip_unpublish}")

    # Initialise the FabricWorkspace object.
    # - item_type_in_scope is intentionally omitted. Per the fabric-cicd docs,
    #   when not provided it defaults to all available item types automatically.
    # - DefaultAzureCredential() resolves credentials from the environment:
    #     pipeline runs  → AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET env vars
    #     local dev runs → `az login` or `Connect-AzAccount` session
    ws = FabricWorkspace(
        workspace_id=args.WorkspaceId,
        environment=args.Environment,
        repository_directory=args.RepositoryDirectory,
        token_credential=DefaultAzureCredential(),
    )

    # Push every item in the repository directory to the target workspace
    publish_all_items(ws)
    print("[info] publish_all_items completed.")

    # Optionally remove items that exist in the workspace but not in the repo
    # (i.e. items that have been deleted from source control)
    if not skip_unpublish:
        unpublish_all_orphan_items(ws)
        print("[info] unpublish_all_orphan_items completed.")

    # Scan the repo to build the deployed items list for reporting
    repo_dir = Path(args.RepositoryDirectory)
    deployed = scan_deployed_items(repo_dir)

    # Derive the markdown path from the same directory as the JSON output
    out_dir = os.path.dirname(args.OutputJsonPath) if args.OutputJsonPath else ""
    md_path = os.path.join(out_dir, "summary.md") if out_dir else ""

    # Write both output artifacts
    write_deployed_json(args.OutputJsonPath, args.WorkspaceId, args.WorkspaceName, deployed)
    write_markdown_summary(md_path, args.WorkspaceName, args.Environment, deployed)


if __name__ == "__main__":
    main()
