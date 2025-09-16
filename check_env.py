
import os
from dotenv import load_dotenv

# Go up three directories to find the project root.
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))

print(f"Searching for .env in project root: {dotenv_path}")

# Load the .env file from the calculated path
if os.path.exists(dotenv_path):
    print(".env file found. Loading variables...")
    load_dotenv(dotenv_path=dotenv_path)
else:
    print("Warning: .env file not found at the specified path.")

slip_key = os.getenv("SLIP_ENCRYPTION_KEY")
binance_key = os.getenv("BINANCE_ENCRYPTION_KEY")

if slip_key:
    print("✅ Revelation Complete: SLIP_ENCRYPTION_KEY is visible to the application.")
elif binance_key:
    print("✅ Revelation Complete: BINANCE_ENCRYPTION_KEY is visible and will be used as a fallback.")
else:
    print("❌ Revelation Failed: NEITHER SLIP_ENCRYPTION_KEY NOR BINANCE_ENCRYPTION_KEY are visible.")

