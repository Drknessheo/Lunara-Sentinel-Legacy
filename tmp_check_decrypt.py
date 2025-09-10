import importlib
import traceback

try:
    m = importlib.import_module("src.modules.db_access")
    print(
        "db_access.decrypt_data callable=", callable(getattr(m, "decrypt_data", None))
    )
except Exception:
    print("db_access import failed")
    traceback.print_exc()

try:
    s = importlib.import_module("src.security")
    print("security.decrypt_data callable=", callable(getattr(s, "decrypt_data", None)))
except Exception:
    print("src.security import failed")
    traceback.print_exc()
import importlib
import traceback

try:
    m = importlib.import_module("src.modules.db_access")
    print(
        "db_access.decrypt_data callable=", callable(getattr(m, "decrypt_data", None))
    )
except Exception:
    print("db_access import failed")
    traceback.print_exc()

try:
    s = importlib.import_module("src.security")
    print("security.decrypt_data callable=", callable(getattr(s, "decrypt_data", None)))
except Exception:
    print("src.security import failed")
    traceback.print_exc()
