import importlib
import traceback

out = []
try:
    m = importlib.import_module("src.modules.db_access")
    out.append(
        "db_access.decrypt_data callable="
        + str(callable(getattr(m, "decrypt_data", None)))
    )
except Exception:
    out.append("db_access import failed")
    out.append(traceback.format_exc())

try:
    s = importlib.import_module("src.security")
    out.append(
        "security.decrypt_data callable="
        + str(callable(getattr(s, "decrypt_data", None)))
    )
except Exception:
    out.append("src.security import failed")
    out.append(traceback.format_exc())

with open("tmp_check_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("wrote tmp_check_output.txt")
