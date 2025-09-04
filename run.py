import os
import subprocess

from dotenv import load_dotenv


def main():
    """
    Loads environment variables from .env and runs the bot.
    """
    print("Loading environment variables from .env file...")
    load_dotenv()

    # Check for essential environment variables
    required_vars = ["TELEGRAM_BOT_TOKEN", "REDIS_URL", "ADMIN_USER_ID"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        print(
            f"Error: Missing required environment variables: {', '.join(missing_vars)}"
        )
        print("Please create a .env file based on .env.example and fill in the values.")
        return

    print("Starting Lunara Bot worker...")
    try:
        # Ensure subprocess uses UTF-8 on Windows to avoid encoding errors
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # Using python -m src.main ensures package imports work correctly.
        subprocess.run(["python", "-m", "src.main"], check=True, env=env)
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except subprocess.CalledProcessError as e:
        print(f"Bot process failed with exit code {e.returncode}")


if __name__ == "__main__":
    main()
