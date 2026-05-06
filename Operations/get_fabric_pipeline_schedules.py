import os
import json
import pandas as pd


REPO_PATH = r"PATH_TO_YOUR_LOCAL_FABRIC_REPO"

results = []

for root, dirs, files in os.walk(REPO_PATH):
    # Skip any folders
    dirs[:] = [d for d in dirs if d != "SKIP_THIS_FOLDER"]
    # Skip any folders 
    dirs[:] = [d for d in dirs if d != "SKIP_THIS_FOLDER"]
    # Skip anything under the folder
    if "SKIP_THIS_FOLDER" in root:
        continue
    # Skip any folder named 
    dirs[:] = [d for d in dirs if d != "SKIP_THIS_FOLDER"]
    for file in files:
        #Match .schedules files (Fabric native format) OR .json in a schedules folder
        if file == ".schedules" or (file.endswith(".json") and "schedule" in root.lower()):

            file_path = os.path.join(root, file)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Structure: REPO/PipelineFolder/PL_Pipeline_Name.DataPipeline/.schedules
                # root = the .DataPipeline folder where .schedules lives
                # parent of root = top level group folder e.g. PipelineFolder
                top_level_folder = os.path.dirname(root)
                folder = os.path.basename(top_level_folder)  # e.g. "PipelineFolder"

                # Strip .DataPipeline suffix to get clean pipeline name
                raw_pipeline = os.path.basename(root)  # e.g. "PL_Pipeline_Name"
                pipeline_name = raw_pipeline.replace(".DataPipeline", "") if ".DataPipeline" in raw_pipeline else raw_pipeline

                schedules = data.get("schedules", [])

                if not schedules:
                    results.append({
                        "pipeline_name": pipeline_name,
                        "folder": folder,
                        "has_schedule": False,
                        "enabled": None,
                        "schedule_type": None,
                        "startDateTime": None,
                        "endDateTime": None,
                        "times": None,
                        "timezone": None
                    })
                else:
                    for s in schedules:
                        config = s.get("configuration", {})

                        results.append({
                            "pipeline_name": pipeline_name,
                            "folder": folder,
                            "has_schedule": True,
                            "enabled": s.get("enabled", False),
                            "schedule_type": config.get("type"),
                            "startDateTime": config.get("startDateTime"),
                            "endDateTime": config.get("endDateTime"),
                            "times": ", ".join(config.get("times", [])),
                            "timezone": config.get("localTimeZoneId")
                        })

            except Exception as e:
                print(f"Error reading {file_path}: {e}")

df = pd.DataFrame(results)

#Filter to only pipelines that have a schedule defined

scheduled_df = df[df["has_schedule"] == True].reset_index(drop=True)

print(f"\nTotal pipelines with schedules: {len(scheduled_df)}")
print(f"  - Enabled:  {scheduled_df['enabled'].sum()}")
print(f"  - Disabled: {(~scheduled_df['enabled']).sum()}")
print()
print(scheduled_df.to_string(index=False))

# Export to CSV
scheduled_df.to_csv("pipeline_schedules.csv", index=False)
