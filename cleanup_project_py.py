#!/usr/bin/env python3
"""Remove Project doctype references from Python files (mechanical changes only)."""

import re
import os

PROJECT_ROOT = "/Users/balajik/erpnext_clean"

def read_file(path):
    with open(path, 'r') as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

# ---- 1. Remove report column dict entries that reference options="Project" ----

# Pattern: match a column dict entry with options "Project" and remove it
# These entries are like:
# {
#     "label": _("Project"),
#     "options": "Project",
#     "fieldname": "project",
#     "width": 100
# },

COLUMN_PATTERN = re.compile(
    r'\{\s*\n(?:\s*"[^"]*":\s*[^,]+,\s*\n)*?'
    r'\s*"options":\s*"Project"\s*\n(?:\s*"[^"]*":\s*[^,]+,\s*\n)*?'
    r'\s*\},\s*\n',
    re.MULTILINE
)

# More targeted patterns for specific files
def remove_columns_with_options_project(content):
    """Remove column dict entries where options is 'Project'."""
    lines = content.split('\n')
    result = []
    skip_block = False
    brace_count = 0
    in_block = False
    block_start = 0
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        if not in_block and stripped == '{' and 'options' not in content.split('\n')[min(i+1, len(lines)-1):min(i+10, len(lines))]:
            # Could be a column block, check next few lines
            pass
            
        if skip_block:
            # We're inside a block we want to skip
            brace_count += stripped.count('{') - stripped.count('}')
            if brace_count == 0:
                skip_block = False
                # Also skip the trailing comma line
                if i + 1 < len(lines) and lines[i+1].strip() in [',', '],']:
                    i += 1
                i += 1
                continue
            i += 1
            continue
        
        if not in_block and stripped == '{':
            # Check if this block has options: "Project"
            block_lines = []
            j = i
            local_brace = 0
            has_project_options = False
            while j < len(lines):
                block_lines.append(lines[j])
                local_brace += lines[j].count('{') - lines[j].count('}')
                if '"options"' in lines[j] and '"Project"' in lines[j]:
                    has_project_options = True
                if local_brace == 0:
                    break
                j += 1
            
            if has_project_options:
                # Skip this block
                i = j + 1
                # Also skip trailing comma
                if i < len(lines) and lines[i].strip() in [',', '],']:
                    i += 1
                continue
        
        result.append(line)
        i += 1
    
    return '\n'.join(result)

# Simpler approach: find exact patterns and remove them
def remove_exact_project_column(content):
    """Remove specific known column definition patterns."""
    
    # Pattern 1: Standard 4-5 line column dict
    patterns = [
        # label, options, fieldname, width pattern
        r'        \{\s*\n            "label": _\("Project"\),\s*\n            "options": "Project",\s*\n            "fieldname": "project",\s*\n            "width": \d+\s*\n        \},\s*\n',
        # Same without trailing comma on the block
        r'        \{\s*\n            "label": _\("Project"\)[^}]*"options": "Project"[^}]*\},\s*\n',
        # Column entries with "Project" in them
        r'\{\s*\n\s*"label":\s*_\("Project"\)[^}]*"options":\s*"Project"[^}]*\},\n',
    ]
    
    for pat in patterns:
        content = re.sub(pat, '', content)
    
    return content

# More careful file-by-file approach

FILES_TO_EDIT = {
    # Format: path -> list of (old_string, new_string) replacements
    
    # === REPORT COLUMNS ===
    
    # accounts/report/item_wise_sales_register
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/item_wise_sales_register/item_wise_sales_register.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # accounts/report/accounts_receivable
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/accounts_receivable/accounts_receivable.py"): [
        (
            '\t\tself.add_column(label=_("Project"), fieldname="project", fieldtype="Link", options="Project")\n',
            ''
        ),
    ],
    
    # accounts/report/received_items_to_be_billed
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/received_items_to_be_billed/received_items_to_be_billed.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # accounts/report/general_ledger
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/general_ledger/general_ledger.py"): [
        (
            '\t\tcolumns.append({"label": _("Project"), "options": "Project", "fieldname": "project", "width": 100})\n',
            ''
        ),
    ],
    
    # accounts/report/item_wise_purchase_register
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/item_wise_purchase_register/item_wise_purchase_register.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # accounts/report/sales_register
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/sales_register/sales_register.py"): [
        (
            '\t\t\t\t{\n\t\t\t\t\t"label": _("Project"),\n\t\t\t\t\t"options": "Project",\n\t\t\t\t\t"fieldname": "project",\n\t\t\t\t\t"width": 100\n\t\t\t\t},\n',
            ''
        ),
    ],
    
    # accounts/report/delivered_items_to_be_billed
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/delivered_items_to_be_billed/delivered_items_to_be_billed.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # accounts/report/purchase_register
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/purchase_register/purchase_register.py"): [
        (
            '\t\t\t\t{\n\t\t\t\t\t"label": _("Project"),\n\t\t\t\t\t"options": "Project",\n\t\t\t\t\t"fieldname": "project",\n\t\t\t\t\t"width": 100\n\t\t\t\t},\n',
            ''
        ),
    ],
    
    # selling/report/item_wise_sales_history
    os.path.join(PROJECT_ROOT, "erpnext/selling/report/item_wise_sales_history/item_wise_sales_history.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],

    # buying/report/purchase_order_analysis
    os.path.join(PROJECT_ROOT, "erpnext/buying/report/purchase_order_analysis/purchase_order_analysis.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # buying/report/item_wise_purchase_history
    os.path.join(PROJECT_ROOT, "erpnext/buying/report/item_wise_purchase_history/item_wise_purchase_history.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # buying/report/procurement_tracker
    os.path.join(PROJECT_ROOT, "erpnext/buying/report/procurement_tracker/procurement_tracker.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # stock/report/reserved_stock
    os.path.join(PROJECT_ROOT, "erpnext/stock/report/reserved_stock/reserved_stock.py"): [
        (
            '\t\t\t{\n\t\t\t\t"label": _("Project"),\n\t\t\t\t"options": "Project",\n\t\t\t\t"fieldname": "project",\n\t\t\t\t"width": 100\n\t\t\t},\n',
            ''
        ),
    ],
    
    # stock/report/stock_ledger - has 2 column patterns + 2 Inventory Dimension references
    os.path.join(PROJECT_ROOT, "erpnext/stock/report/stock_ledger/stock_ledger.py"): [
        # Label/options pattern 1
        (
            '\t\t\t\t{\n\t\t\t\t\t"label": _("Project"),\n\t\t\t\t\t"options": "Project",\n\t\t\t\t\t"fieldname": "project",\n\t\t\t\t\t"width": 100\n\t\t\t\t},\n',
            ''
        ),
        # Inventory Dimension references
        (
            '\t\t"Inventory Dimension", filters={"reference_document": "Project"}\n',
            ''
        ),
        (
            '\t\t"Inventory Dimension", filters={"reference_document": "Project"}\n',
            ''
        ),
    ],

    # === SIMPLE PYTHON REFERENCES ===
    
    # controllers/trends.py
    os.path.join(PROJECT_ROOT, "erpnext/controllers/trends.py"): [
        (
            '\telif based_on == "Project":\n',
            ''
        ),
    ],
    
    # controllers/accounts_controller.py
    os.path.join(PROJECT_ROOT, "erpnext/controllers/accounts_controller.py"): [
        (
            '\t\tdimension_list = sum(dimension_list, ["Project", "Cost Center"])\n',
            '\t\tdimension_list = sum(dimension_list, ["Cost Center"])\n'
        ),
    ],
    
    # selling/report/sales_analytics
    os.path.join(PROJECT_ROOT, "erpnext/selling/report/sales_analytics/sales_analytics.py"): [
        (
            '\t\telif self.filters.tree_type == "Project":\n',
            ''
        ),
    ],
    
    # accounts/report/budget_variance_report
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/budget_variance_report/budget_variance_report.py"): [
        (
            '\tif filters.get("budget_against") in ["Cost Center", "Project"]:\n',
            '\tif filters.get("budget_against") == "Cost Center":\n'
        ),
    ],
    
    # accounts/report/profitability_analysis 
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/profitability_analysis/profitability_analysis.py"): [
        (
            '\telif based_on == "Project":\n',
            ''
        ),
        (
            '\t\treturn frappe.get_all("Project", fields=["name"], filters={"company": company}, order_by="name")\n',
            ''
        ),
    ],
    
    # accounts/report/gross_profit
    os.path.join(PROJECT_ROOT, "erpnext/accounts/report/gross_profit/gross_profit.py"): [
        (
            '\t\t\t"options": "Project",\n',
            ''
        ),
    ],
    
    # setup/setup_wizard/data/dashboard_charts
    os.path.join(PROJECT_ROOT, "erpnext/setup/setup_wizard/data/dashboard_charts.py"): [
        (
            '\t\t\t\t"dashboard_name": "Project",\n',
            ''
        ),
    ],
    
    # accounts/doctype/process_statement_of_accounts - reference to PSOA Project
    os.path.join(PROJECT_ROOT, "erpnext/accounts/doctype/process_statement_of_accounts/process_statement_of_accounts.py"): [
        (
            '\t\t\t\t\t"options": "PSOA Project",\n\t\t\t\t\t"label": "Project",\n',
            '\t\t\t\t\t"options": "PSOA Project",\n\t\t\t\t\t"label": "Project",\n'
        ),
    ],
}

def apply_replacements(file_path, replacements):
    if not os.path.exists(file_path):
        print(f"SKIP: {file_path} not found")
        return False
    
    content = read_file(file_path)
    modified = False
    
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            modified = True
            print(f"  OK: Applied replacement in {os.path.relpath(file_path, PROJECT_ROOT)}")
        else:
            print(f"  ?: Pattern not found in {os.path.relpath(file_path, PROJECT_ROOT)}")
    
    if modified:
        write_file(file_path, content)
        return True
    return False

print("Applying Python Project doctype reference cleanup...")
modified_count = 0
for file_path, replacements in FILES_TO_EDIT.items():
    if apply_replacements(file_path, replacements):
        modified_count += 1

print(f"\nModified {modified_count} files")

# Let's also check the gross_profit file more carefully
gp_path = os.path.join(PROJECT_ROOT, "erpnext/accounts/report/gross_profit/gross_profit.py")
print(f"\n--- Checking gross_profit.py for 'Project' references ---")
gp_content = read_file(gp_path)
for i, line in enumerate(gp_content.split('\n'), 1):
    if 'Project' in line and '#' not in line.split('Project')[0]:
        print(f"  Line {i}: {line.strip()}")

# Check for any remaining references
print(f"\n--- Checking for remaining 'Project' doctype references ---")
for root, dirs, files in os.walk(os.path.join(PROJECT_ROOT, "erpnext")):
    # Skip patches, test, and node_modules
    if '/patches/' in root or '/test_' in root or '/node_modules' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            content = read_file(path)
            for i, line in enumerate(content.split('\n'), 1):
                stripped = line.strip()
                # Look for "Project" as a doctype reference (string literal)
                if '"Project"' in stripped or "'Project'" in stripped:
                    # Skip comments, test files, patches
                    if stripped.startswith('#') or '/test_' in path or '/patches/' in path:
                        continue
                    # Skip if just a label or comment
                    if 'label' in stripped.lower() and 'Project' in stripped:
                        continue
                    print(f"  {os.path.relpath(path, PROJECT_ROOT)}:{i}: {stripped[:120]}")
