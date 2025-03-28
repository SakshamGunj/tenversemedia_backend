from fastapi import APIRouter, Form, HTTPException, Depends
from app.routes.auth import get_current_user
from app.services.odoo import OdooSession
from app.services.twillo import send_twilio_sms
from app.services.validation import validate_and_format_whatsapp_number
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/api/send-whatsapp-message")
async def send_whatsapp(
    number: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    formatted_number = validate_and_format_whatsapp_number(number)
    logger.info(f"Attempting to send WhatsApp message to {formatted_number}")
    odoo_session = OdooSession()
    if odoo_session and odoo_session.authenticated:
        try:
            partner_id = 3
            variables = {"1": "Welcome", "2": "Customer", "3": "Enjoy your offer!"}
            composer_id = odoo_session.create_whatsapp_composer(partner_id, 10, formatted_number, variables)
            if composer_id:
                odoo_session.send_whatsapp_message(composer_id, partner_id, formatted_number)
                return {"message": "WhatsApp message sent successfully"}
            raise HTTPException(status_code=500, detail="Failed to create WhatsApp composer")
        except Exception as e:
            logger.error(f"Error sending WhatsApp message: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to send WhatsApp message: {str(e)}")
    raise HTTPException(status_code=500, detail="Odoo session not initialized")

@router.post("/api/send-twilio-sms")
async def send_twilio(
    number: str = Form(...),
    message: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    formatted_number = validate_and_format_whatsapp_number(number)
    logger.info(f"Attempting to send Twilio SMS to {formatted_number}")
    try:
        send_twilio_sms(formatted_number, message)
        return {"message": "Twilio SMS sent successfully"}
    except Exception as e:
        logger.error(f"Error sending Twilio SMS: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send Twilio SMS: {str(e)}")