import importlib
import sys
import traceback

try:
    # Try to import and check src.security
    security_module = importlib.import_module("src.security")
    print(
        "security.decrypt_data callable:",
        callable(getattr(security_module, "decrypt_data", None)),
    )

    # Try to import and check src.modules.db_access
    db_access_module = importlib.import_module("src.modules.db_access")
    print(
        "db_access.decrypt_data callable:",
        callable(getattr(db_access_module, "decrypt_data", None)),
    )

except Exception as e:
    # If any import fails, print the error and exit
    print("An import error occurred:")
    traceback.print_exc()
    sys.exit(1)

# If we get here, everything imported successfully.
print("Imports were successful.")
