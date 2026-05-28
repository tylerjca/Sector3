import os
from pathlib import Path

from dotenv import load_dotenv


# Load variables from the .env file in the project root.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")


if not IG_USERNAME or IG_USERNAME == "YOUR_USERNAME_HERE":
    raise ValueError("Set IG_USERNAME in .env before running automation.")

if not IG_PASSWORD or IG_PASSWORD == "YOUR_PASSWORD_HERE":
    raise ValueError("Set IG_PASSWORD in .env before running automation.")
