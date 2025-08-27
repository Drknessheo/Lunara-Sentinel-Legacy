import os
import re

def scan_for_relative_imports(directory):
    relative_imports_found = {}
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    # Regex to find relative imports (from . or from ..)
                    # This regex is simplified and might catch some false positives
                    # but should be good enough for a quick scan.
                    matches = re.findall(r"^from\s+\.\.?\w+\s+import", content, re.MULTILINE)
                    if matches:
                        relative_imports_found[filepath] = matches
    return relative_imports_found

if __name__ == "__main__":
    src_directory = "g:\\Lunara Bot\\src"
    found_imports = scan_for_relative_imports(src_directory)

    if found_imports:
        print("Relative imports found:")
        for filepath, imports in found_imports.items():
            print(f"  File: {filepath}")
            for imp in imports:
                print(f"    - {imp}")
    else:
        print("No relative imports found in the src/ directory.")
