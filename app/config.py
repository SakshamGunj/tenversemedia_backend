import os
import json
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

class Config:
    """Configuration class for Restro Hub backend."""
    
    # Firebase
    FIREBASE_CREDENTIALS_PATH: str = os.getenv("FIREBASE_CREDENTIALS_PATH", "restro-hub-firebase-adminsdk.json")
    try:
        with open(FIREBASE_CREDENTIALS_PATH, 'r') as f:
            FIREBASE_CREDENTIALS: dict = json.load(f)
    except FileNotFoundError:
        raise ValueError(f"Firebase credentials file {FIREBASE_CREDENTIALS_PATH} not found. Set FIREBASE_CREDENTIALS_PATH in .env.")
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON in {FIREBASE_CREDENTIALS_PATH}")

    # Odoo
    ODOO_BASE_URL: str = os.getenv("ODOO_BASE_URL", "https://tenversemediarestrocafe.odoo.com")
    ODOO_DB: str = os.getenv("ODOO_DB", "tenversemediarestrocafe")
    ODOO_USERNAME: str = os.getenv("ODOO_USERNAME", "gunj06saksham@gmail.com")
    ODOO_PASSWORD: str = os.getenv("ODOO_PASSWORD", "gunj7250@")

    # Twilio
    TWILIO_ACCOUNT_SID: Optional[str] = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER: Optional[str] = os.getenv("TWILIO_PHONE_NUMBER")

    # Validate Twilio credentials
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
        raise ValueError("Twilio credentials (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER) must be set in .env")

    # CORS and Redis
    ALLOWED_ORIGINS: list = os.getenv("ALLOWED_ORIGINS", "*").split(",")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

config = Config()