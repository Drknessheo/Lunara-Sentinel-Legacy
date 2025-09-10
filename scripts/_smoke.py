import importlib
import os

os.environ["ADMIN_USER_ID"] = "6284071528"
importlib.invalidate_caches()
import config
from src import trade

print("config.ADMIN_USER_ID=", getattr(config, "ADMIN_USER_ID", None))
print(
    "trade.get_user_client returns:",
    trade.get_user_client(getattr(config, "ADMIN_USER_ID", None)),
)
