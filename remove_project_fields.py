#!/usr/bin/env python3
"""Remove all Link fields referencing the 'Project' doctype from ERPNext doctype JSON files."""

import json
import os
import glob

PROJECT_ROOT = "."

# Find all doctype JSON files
json_files = glob.glob(os.path.join(PROJECT_ROOT, "erpnext/**/*.json"), recursive=True)

modified_files = []

for filepath in sorted(json_files):
    relpath = os.path.relpath(filepath, PROJECT_ROOT)
    if "doctype" not in relpath or "node_modules" in relpath:
        continue

    with open(filepath, "r") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            continue

    if "fields" not in data or not isinstance(data["fields"], list):
        continue

    # Find fields with options="Project" and fieldtype="Link"
    fields_to_remove = []
    for i, field in enumerate(data["fields"]):
        fieldname = field.get("fieldname", "")
        fieldtype = field.get("fieldtype", "")
        options = field.get("options", "")
        
        # Remove if it's a Link field referencing Project doctype
        if fieldtype == "Link" and options == "Project":
            fields_to_remove.append((i, fieldname))

    if not fields_to_remove:
        continue

    # Remove fields from the fields array (reverse order to maintain indices)
    fieldnames_to_remove = set(fn for (_, fn) in fields_to_remove)
    for i, fn in reversed(fields_to_remove):
        data["fields"].pop(i)

    # Remove from field_order if it exists
    if "field_order" in data and isinstance(data["field_order"], list):
        data["field_order"] = [fo for fo in data["field_order"] if fo not in fieldnames_to_remove]

    # Special handling for budget.json: also remove "Project" from budget_against options
    if "budget" in relpath.lower() and "budget.json" in relpath:
        for field in data["fields"]:
            if field.get("fieldname") == "budget_against":
                options = field.get("options", "")
                if "Project" in options:
                    opts = [o.strip() for o in options.split("\n")]
                    opts = [o for o in opts if o != "Project"]
                    field["options"] = "\n" + "\n".join(opts)
                    print(f"  Updated budget_against options: {field['options']!r}")

    # Write back
    with open(filepath, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")

    print(f"Modified: {relpath}")
    for _, fn in fields_to_remove:
        print(f"  Removed field: {fn}")
    modified_files.append(relpath)

print(f"\nTotal modified files: {len(modified_files)}")
