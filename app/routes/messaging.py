from fastapi import APIRouter, Form, HTTPException, Depends
from app.routes.auth import get_current_user
from app.services.odoo import OdooSession
from app.services.twillo import send_twilio_sms
from app.services.validation import validate_and_format_whatsapp_number
import logging
import asyncio
from datetime import datetime
from app.db import db

router = APIRouter()
logger = logging.getLogger(__name__)

async def send_message_async(number: str, type: str, content: str, is_broadcast: bool = False):
    db.collection("messages").add({
        "user_id": None if is_broadcast else number,
        "type": type,
        "content": content,
        "status": "queued",
        "scheduled_at": datetime.utcnow().isoformat(),
        "is_broadcast": is_broadcast
    })
    await asyncio.sleep(1)  # Simulate async processing
    # Add actual Odoo/Twilio logic here later
    logger.info(f"Message sent to {number if not is_broadcast else 'broadcast list'}: {content}")

@router.post("/api/send-message")
async def send_message(
    number: str = Form(None),
    user_id: str = Form(None),
    type: str = Form(...),
    content: str = Form(...),
    is_broadcast: bool = Form(False),
    current_user: dict = Depends(get_current_user)
):
    if not number and not user_id and not is_broadcast:
        raise HTTPException(status_code=400, detail="Number, user_id, or broadcast required")
    
    if user_id:
        user = db.collection("users").document(user_id).get().to_dict()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        number = user.get("whatsapp") or user.get("phone")
    
    if number:
        number = validate_and_format_whatsapp_number(number)
    
    asyncio.create_task(send_message_async(number, type, content, is_broadcast))
    return {"message": "Message queued. It will be delivered soon."}

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