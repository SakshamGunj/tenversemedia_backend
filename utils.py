import re
from fastapi import HTTPException

def validate_and_format_whatsapp_number(number: str) -> str:
    number = re.sub(r'[\s\-\(\)]', '', number)
    number = number.lstrip('0')

    if number.startswith('+91'):
        if len(number) != 12:
            raise HTTPException(status_code=400, detail="Invalid Indian WhatsApp number. Must be 10 digits after +91.")
        return number
    if number.startswith('91'):
        if len(number) != 12:
            raise HTTPException(status_code=400, detail="Invalid Indian WhatsApp number. Must be 10 digits after 91.")
        return '+' + number
    if len(number) == 10:
        return '+91' + number
    raise HTTPException(status_code=400, detail="Invalid WhatsApp number. Please provide a valid Indian number (10 digits, or starting with +91 or 91).")