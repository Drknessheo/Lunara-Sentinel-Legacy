import os
import subprocess
import sys

from dotenv import load_dotenv


def main() -> int:
    """Load .env and run the bot as a module using the current Python executable.

    Returns an exit code suitable for sys.exit().
    """
    load_dotenv()

    # Check for essential environment variables
    required_vars = ["TELEGRAM_BOT_TOKEN", "REDIS_URL", "ADMIN_USER_ID"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        print(
            f"Error: Missing required environment variables: {', '.join(missing_vars)}"
        )
        print("Please create a .env file based on .env.example and fill in the values.")
        return 2

    print("Starting Lunara Bot worker...")

    env = os.environ.copy()
    # Ensure subprocess uses UTF-8 on Windows to avoid encoding errors
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        # Use the same Python interpreter that's running this script
        subprocess.run([sys.executable, "-m", "src.main"], check=True, env=env)
        return 0
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        return 0
    except subprocess.CalledProcessError as e:
        print(f"Bot process failed with exit code {e.returncode}")
        return e.returncode


if __name__ == "__main__":
    raise SystemExit(main())
