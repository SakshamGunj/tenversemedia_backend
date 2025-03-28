from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.routes.auth import get_current_user
from app.db import save_submission, get_user_submission, get_offer, db, update_loyalty_data, restaurant_exists, get_loyalty_data
import logging
import os
from datetime import datetime
from typing import Dict, Optional
import firebase_admin
from firebase_admin import firestore
import uuid

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)

@router.get("/claim-reward", response_class=HTMLResponse)
async def claim_reward_form(request: Request, restaurant_id: str):
    if not restaurant_exists(restaurant_id):
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return templates.TemplateResponse("claim_reward.html", {"request": request, "restaurant_id": restaurant_id})

@router.post("/api/claim-reward")
async def claim_reward(
    request: Request,
    restaurant_id: str,  # Changed to query parameter
    name: str = Form(...),
    whatsapp: str = Form(...),
    email: Optional[str] = Form(None),
    reward: str = Form(...),
    spend_amount: float = Form(..., ge=0),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["uid"]
    logger.info(f"Processing claim for {user_id} at {restaurant_id}")

    if not restaurant_exists(restaurant_id):
        logger.error(f"Restaurant {restaurant_id} not found")
        raise HTTPException(status_code=404, detail="Restaurant not found")

    # Validate reward against registered offers
    restaurant_doc = db.collection("restaurants").document(restaurant_id).get()
    if not restaurant_doc.exists:
        logger.error(f"Restaurant {restaurant_id} data not found")
        raise HTTPException(status_code=404, detail="Restaurant data not found")
    
    restaurant_data = restaurant_doc.to_dict()
    offers = restaurant_data.get("offers", [])
    if reward not in offers:
        logger.error(f"Invalid offer {reward} for {restaurant_id}")
        raise HTTPException(status_code=400, detail="Invalid offer")

    # Get loyalty settings
    loyalty_settings = restaurant_data.get("loyalty_settings", {}).get("current", {})
    points_per_rupee = loyalty_settings.get("points_per_rupee", 1.0)
    points_to_add = int(spend_amount * points_per_rupee)
    punches_to_add = 1 if spend_amount >= 50 else 0  # Example punch logic

    # Generate a unique coupon code
    coupon_code = f"COUPON-{str(uuid.uuid4())[:8].upper()}"  # e.g., COUPON-ABCDEF12

    # Save submission with coupon code
    existing_submission = get_user_submission(restaurant_id, user_id)
    submission_data = {
        "name": name,
        "whatsapp": whatsapp,
        "email": email,
        "reward": reward,
        "spend_amount": spend_amount,
        "restaurant_id": restaurant_id,
        "coupon_code": coupon_code,
        "previous_rewards": existing_submission.get("previous_rewards", []) + [reward] if existing_submission else [reward]
    }
    save_submission(restaurant_id, submission_data, user_id)

    # Update loyalty data and add coupon to redemption history
    try:
        loyalty_data = update_loyalty_data(user_id, {
            "total_points": firestore.Increment(points_to_add),
            "punches": firestore.Increment(punches_to_add),
            "restaurant_points": {restaurant_id: firestore.Increment(points_to_add)},
            "redemption_history": firestore.ArrayUnion([{
                "restaurant_id": restaurant_id,
                "coupon_code": coupon_code,
                "offer": reward,
                "claimed_at": datetime.utcnow().isoformat()
            }])
        })
    except Exception as e:
        logger.error(f"Failed to process claim for {user_id} at {restaurant_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update loyalty data")

    # Calculate points left for next offer
    current_points = loyalty_data.get("total_points", 0)
    reward_thresholds = loyalty_settings.get("reward_thresholds", {})
    next_threshold = None
    for threshold in sorted(reward_thresholds.keys(), reverse=False):
        if current_points < int(threshold):
            next_threshold = int(threshold)
            break
    points_left = next_threshold - current_points if next_threshold else 0

    # Messaging logic (simplified)
    message = f"Hi {name}, your reward {reward} has been claimed! Coupon Code: {coupon_code}, Spend: ${spend_amount}"
    if os.getenv("ODOO_URL"):
        logger.info(f"Sending message via Odoo: {message}")
    elif os.getenv("TWILIO_SID"):
        logger.info(f"Sending message via Twilio: {message}")
    else:
        logger.warning("No messaging service configured")

    return {
        "status": "success",
        "message": "Reward claimed!",
        "submission_id": user_id,
        "coupon_code": coupon_code,
        "redirect_to": "/offer",
        "offer_data": get_offer(restaurant_id),
        "loyalty": loyalty_data,
        "points_left_for_next_offer": points_left if points_left > 0 else None,
        "next_offer": reward_thresholds.get(str(next_threshold)) if next_threshold else "Maximum offer reached"
    }