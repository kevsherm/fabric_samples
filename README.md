
# fabric_samples

A collection of Microsoft Fabric utilities, scripts, and code samples focused on automation, observability, and understanding the Fabric platform. This repository is intended as a practical reference for individuals, teams, and the broader Fabric community.

---

## 📌 Purpose

This repo serves as a growing library of reusable tools and samples built around Microsoft Fabric. Whether you're just getting started with Fabric or looking for scripts to streamline your workflows, you'll find practical, real-world examples here.

---

## 📂 Contents

| Folder | Description |
|---|---|
| `/Operations` | general items to help with understanding of Fabric Operations |
| `/Project-Management` | items related to Project Management Activities - ADO Analtics, etc |

> 📝 This structure will grow over time as new samples are added.

---

## 🚀 Getting Started

1. **Clone the repo**
   ```bash
   git clone https://github.com/YOUR_USERNAME/fabric_samples.git
   ```

2. **Navigate to the sample you want** and follow any instructions in that folder

3. **Update any configuration values** — scripts that require a local repo path or workspace ID will have a clearly marked placeholder to update

---

## 🔧 Requirements

- Python 3.8+
- `pandas` library (`pip install pandas`)
- Access to a Microsoft Fabric workspace (for API-based samples)
- A local clone of your Fabric Git-connected workspace repository (for file-based samples)

---

## 📄 Samples

### `get_fabric_pipeline_schedules.py`
Reads `.schedules` files from a locally cloned Fabric Git repository and produces a summary of all pipeline schedules — including schedule type, run times, timezone, and enabled/disabled status.

**Usage:**
```bash
python get_fabric_pipeline_schedules.py
```
Update `REPO_PATH` in the script to point to your local Fabric repo before running.

---

## 📝 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---
