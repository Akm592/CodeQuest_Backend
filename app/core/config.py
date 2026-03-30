# Configuration management
import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    SUPABASE_URL: str = os.getenv("SUPABASE_URL")
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY")
    # JSON string mapping specific IPs to custom limits (e.g. {"1.2.3.4": 100, "5.6.7.8": -1})
    # -1 means unlimited. Default for unlisted IPs is 10.
    RATE_LIMIT_RULES: str = os.getenv("RATE_LIMIT_RULES", "{}")
    APP_LOG_FILE: str = "app.log"
    CORS_ORIGINS = [
        "http://localhost:5173/",
        "http://localhost:5173",
        "https://codequest101.vercel.app/",
        "https://codequest101.vercel.app",
    ]
# Example origins


settings = Settings()

if not settings.GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY environment variable not set.")
if not settings.SUPABASE_URL:
    raise ValueError("SUPABASE_URL environment variable not set.")
if not settings.SUPABASE_ANON_KEY:
    raise ValueError("SUPABASE_ANON_KEY environment variable not set.")
