from fastapi import APIRouter, Form, HTTPException, Depends, Request
from app.routes.auth import get_current_user
from app.db import save_submission, get_submissions, get_user_submission, get_offer, db, update_loyalty_data, restaurant_exists
from app.services.odoo import OdooSession
from app.services.twillo import send_twilio_sms
from app.services.validation import validate_and_format_whatsapp_number
from datetime import datetime
import logging
from typing import Dict
from google.cloud import firestore

router = APIRouter()
logger = logging.getLogger(__name__)

async def get_restaurant_id(request: Request) -> str:
    restaurant_id = request.query_params.get("restaurant_id")
    if not restaurant_id:
        logger.error("Missing restaurant_id in claim-reward request")
        raise HTTPException(status_code=400, detail="restaurant_id is required")
    return restaurant_id

@router.post(
    "/api/claim-reward",
    tags=["User"],
    summary="Claim a reward at a restaurant",
    description="Claims a reward for a user, validating against registered offers and updating loyalty points.",
    responses={200: {"description": "Reward claimed"}, 400: {"description": "Invalid input"}, 401: {"description": "Invalid token"}, 500: {"description": "Server error"}}
)
async def claim_reward(
    name: str = Form(None),
    whatsapp: str = Form(None),
    email: str = Form(None),
    reward: str = Form(None),
    referred_by: str = Form(None),
    spend_amount: float = Form(None),
    current_user: dict = Depends(get_current_user),
    request: Request = None
):
    restaurant_id = await get_restaurant_id(request)
    user_id = current_user["uid"]
    logger.info(f"Processing claim for user {user_id} at {restaurant_id}")

    # Validate restaurant exists
    if not restaurant_exists(restaurant_id):
        logger.error(f"Restaurant {restaurant_id} not found")
        raise HTTPException(status_code=400, detail="Restaurant not found")

    # Get registered offers
    restaurant_doc = db.collection("restaurants").document(restaurant_id).get()
    if not restaurant_doc.exists:
        logger.error(f"Restaurant document {restaurant_id} not found")
        raise HTTPException(status_code=500, detail="Restaurant data missing")
    registered_offers = restaurant_doc.to_dict().get("offers", [])

    existing_user = None
    original_restaurant_id = None
    for r_doc in db.collection("restaurants").stream():
        r_id = r_doc.id
        user_doc = db.collection("restaurants").document(r_id).collection("users").document(user_id).get()
        if user_doc.exists:
            existing_user = user_doc.to_dict()
            existing_user["id"] = user_id
            if "submitted_at" in existing_user and isinstance(existing_user["submitted_at"], datetime):
                existing_user["submitted_at"] = existing_user["submitted_at"].isoformat()
            original_restaurant_id = r_id
            break

    points_to_add = 0
    punches_to_add = 1
    if spend_amount and spend_amount > 0:
        loyalty_settings = restaurant_doc.to_dict().get("loyalty_settings", {}).get("current", {}).get("points_per_rupee", 1.0)
        points_to_add = int(spend_amount * loyalty_settings)

    if existing_user:
        if reward and reward not in registered_offers:
            logger.error(f"Invalid offer {reward} for user {user_id} at {restaurant_id}")
            raise HTTPException(status_code=400, detail="Invalid offer")
        new_offer = reward if reward else "Default Offer"
        submission_data = {
            "name": existing_user.get("name", "Unknown"),
            "whatsapp": existing_user.get("whatsapp", ""),
            "email": existing_user.get("email", ""),
            "reward": new_offer,
            "submitted_at": datetime.utcnow(),
            "restaurant_id": restaurant_id,
            "original_restaurant": original_restaurant_id,
            "recognized_from": True,
            "previous_rewards": existing_user.get("previous_rewards", []) + [new_offer]
        }
    else:
        if not all([name, whatsapp, email, reward]):
            return {"status": "new_user", "form_required": True}
        if "@" not in email or "." not in email:
            raise HTTPException(status_code=400, detail="Invalid email")
        if reward not in registered_offers:
            raise HTTPException(status_code=400, detail="Invalid offer")
        formatted_whatsapp = validate_and_format_whatsapp_number(whatsapp)
        submission_data = {
            "name": name,
            "whatsapp": formatted_whatsapp,
            "email": email,
            "reward": reward,
            "submitted_at": datetime.utcnow(),
            "restaurant_id": restaurant_id,
            "previous_rewards": [reward]
        }

    try:
        save_submission(restaurant_id, submission_data, user_id)
        loyalty_data = update_loyalty_data(user_id, {
            "total_points": firestore.Increment(points_to_add),
            "punches": firestore.Increment(punches_to_add),
            "restaurant_points": {restaurant_id: firestore.Increment(points_to_add)}
        })
        if referred_by and not loyalty_data.get("referred_by"):
            db.collection("loyalty").document(user_id).set({"referred_by": referred_by}, merge=True)
            update_loyalty_data(referred_by, {"total_points": firestore.Increment(50)})
    except Exception as e:
        logger.error(f"Failed to process claim for {user_id} at {restaurant_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process claim")

    punch_reward_available = loyalty_data["punches"] >= 10
    odoo_session = OdooSession()
    formatted_whatsapp = submission_data.get("whatsapp") or existing_user.get("whatsapp", "")
    sms_body = f"Hi {submission_data.get('name', 'Customer')}, you won: {reward} at {restaurant_id}!"
    try:
        if odoo_session and odoo_session.authenticated:
            partner_id = 3
            variables = {"1": reward, "2": submission_data.get("name", "Customer"), "3": "Enjoy!"}
            composer_id = odoo_session.create_whatsapp_composer(partner_id, 10, formatted_whatsapp, variables)
            if composer_id: odoo_session.send_whatsapp_message(composer_id, partner_id, formatted_whatsapp)
            sms_composer_id = odoo_session.create_sms_composer(partner_id, formatted_whatsapp, sms_body)
            if sms_composer_id: odoo_session.send_sms_message(sms_composer_id, partner_id, formatted_whatsapp)
        else:
            logger.warning("Odoo failed, using Twilio")
            send_twilio_sms(formatted_whatsapp, sms_body)
    except Exception as e:
        logger.error(f"Messaging error for {user_id}: {e}")
        send_twilio_sms(formatted_whatsapp, sms_body)

    offer_data = get_offer(restaurant_id)
    status = "recognized" if existing_user else "success"
    return {
        "status": status,
        "message": f"Reward claimed!" if not existing_user else f"Recognized from {original_restaurant_id}, new offer: {reward}",
        "submission_id": user_id,
        "redirect_to": "/offer",
        "offer_data": offer_data,
        "loyalty": {
            "total_points": loyalty_data["total_points"],
            "tier": loyalty_data.get("tier", "Bronze"),
            "punches": loyalty_data["punches"],
            "punch_reward_available": punch_reward_available,
            "restaurant_points": loyalty_data.get("restaurant_points", {})
        }
    }