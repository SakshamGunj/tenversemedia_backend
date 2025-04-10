from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.routes.auth import get_current_user
from app.db import save_submission, get_user_submission, get_offer, db, update_loyalty_data, restaurant_exists, get_loyalty_data
import logging
import os
from datetime import datetime, timedelta
import uuid
from typing import Optional
from firebase_admin import firestore

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
    restaurant_id: str,
    name: str = Form(...),
    whatsapp: str = Form(...),
    email: Optional[str] = Form(None),
    reward: str = Form(...),
    spend_amount: float = Form(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["uid"]
    logger.info(f"Processing claim for {user_id} at {restaurant_id}")

    # Single read for restaurant data
    restaurant_doc = db.collection("restaurants").document(restaurant_id).get()
    if not restaurant_doc.exists:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    restaurant_data = restaurant_doc.to_dict()
    if reward not in restaurant_data.get("offers", []):
        raise HTTPException(status_code=400, detail="Invalid offer")

    # Compute once
    now = datetime.utcnow()
    expiry_date = now + timedelta(days=restaurant_data["coupon_expiry_days"])
    expiry_date_str = expiry_date.date().isoformat()
    coupon_code = f"COUPON-{str(uuid.uuid4())[:8].upper()}"
    spin_count = len(get_loyalty_data(user_id).get("spin_history", [])) + 1
    spin_points = restaurant_data.get("spin_points_per_spin", 10) * spin_count
    spend_points = int(spend_amount * restaurant_data["loyalty_settings"]["current"]["points_per_rupee"])

    # Batch write operation
    batch = db.batch()
    claim_entry = {
        "coupon_code": coupon_code,
        "offer": reward,
        "claimed_at": now.isoformat(),
        "status": "claimed"
    }

    # Update restaurant subcollection user document
    user_ref = db.collection("restaurants").document(restaurant_id).collection("users").document(user_id)
    batch.set(user_ref, {
        "phone": whatsapp,
        "birthday": None,
        "name": name,
        "claim_history": firestore.ArrayUnion([claim_entry])
    }, merge=True)

    # Update loyalty data (simplified to batch if possible, but currently via function)
    updates = {
        "total_points": firestore.Increment(spin_points + spend_points),
        "spin_points": firestore.Increment(spin_points),
        "spend_points": firestore.Increment(spend_points),
        "spin_history": firestore.ArrayUnion([{"reward": reward, "won_at": now.isoformat()}]),
        "spend_history": firestore.ArrayUnion([{"amount": spend_amount, "points": spend_points, "date": now.isoformat()}]),
        "claim_history": firestore.ArrayUnion([claim_entry]),
        "redemption_history": firestore.ArrayUnion([{
            "coupon_code": coupon_code,
            "offer": reward,
            "claimed_at": now.isoformat(),
            "status": "pending"
        }])
    }
    update_loyalty_data(user_id, updates)  # Note: This is a separate call; consider batching if modified

    # Store coupon
    coupon_ref = db.collection("coupons").document(coupon_code)
    batch.set(coupon_ref, {
        "user_id": user_id,
        "restaurant_id": restaurant_id,
        "offer": reward,
        "expiry_date": expiry_date_str,
        "is_used": False,
        "created_at": now.isoformat()
    })

    # Commit batch
    await batch.commit()

    # Optimize threshold calculation
    loyalty_data = get_loyalty_data(user_id)  # Re-fetch for current points (could be optimized further)
    current_points = loyalty_data.get("total_points", 0) + spin_points + spend_points
    reward_thresholds = restaurant_data["loyalty_settings"]["current"]["reward_thresholds"]
    next_threshold = next((int(t) for t in sorted(reward_thresholds.keys()) if int(t) > current_points), None)
    points_left = next_threshold - current_points if next_threshold else 0

    return {
        "status": "success",
        "message": "Reward claimed!",
        "submission_id": user_id,
        "coupon_code": coupon_code,
        "expiry_date": expiry_date_str,
        "spin_count": spin_count,
        "spin_points": spin_points,
        "spend_points": spend_points,
        "points_left_for_next_offer": points_left if points_left > 0 else None,
        "next_offer": reward_thresholds.get(str(next_threshold)) if next_threshold else "Maximum offer reached"
    }