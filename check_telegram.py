import importlib
import traceback

import telegram

print("telegram.__version__ =", getattr(telegram, "__version__", "unknown"))
print("telegram.__file__ =", getattr(telegram, "__file__", "unknown"))
try:
    from telegram.ext.utils.types import ConversationDict

    print("Imported ConversationDict from telegram.ext.utils.types")
except Exception:
    print("Failed to import telegram.ext.utils.types:")
    traceback.print_exc()

try:
    import telegram.ext as ext

    print(
        "telegram.ext attributes:",
        [
            name
            for name in dir(ext)
            if "utils" in name.lower() or "conversation" in name.lower()
        ],
    )
except Exception:
    print("Failed to import telegram.ext:")
    traceback.print_exc()
