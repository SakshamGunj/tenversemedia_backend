from twilio.rest import Client
from app.config import config
import logging

client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
logging.info("Twilio client initialized")

def send_twilio_sms(to_number: str, body: str) -> str:
    """Send an SMS via Twilio."""
    try:
        message = client.messages.create(
            body=body,
            from_=config.TWILIO_PHONE_NUMBER,
            to=to_number
        )
        logging.info(f"Twilio SMS sent to {to_number}, SID: {message.sid}")
        return message.sid
    except Exception as e:
        logging.error(f"Twilio SMS failed: {e}")
        raise