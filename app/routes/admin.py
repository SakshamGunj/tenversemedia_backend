from fastapi import APIRouter, Form, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from app.routes.auth import get_current_user
from app.db import get_submissions, get_loyalty_data, db, restaurant_exists, update_loyalty_settings
import logging
from typing import List, Dict, Optional
from google.cloud.firestore_v1.transaction import Transaction
from google.cloud.firestore_v1 import ArrayUnion
from datetime import datetime
import os
import json
import uuid

ALLOWED_ADMIN_UID = "qkmgiVcJhYgTpJSITv7PD6kxgn12"

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)

RESTAURANTS_FILE = "restaurants.json"

# Load restaurants
def load_restaurants():
    if os.path.exists(RESTAURANTS_FILE):
        with open(RESTAURANTS_FILE, "r") as f:
            return json.load(f)
    return {}

# Save restaurants
def save_restaurants(restaurants):
    with open(RESTAURANTS_FILE, "w") as f:
        json.dump(restaurants, f)

# Create restaurant
@router.post("/api/restaurants")
async def create_restaurant(restaurant: dict):
    name = restaurant.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Restaurant name is required")

    restaurant_id = name.lower().replace(" ", "-")
    restaurants = load_restaurants()
    if restaurant_id in restaurants:
        raise HTTPException(status_code=400, detail="Restaurant ID already exists")

    restaurants[restaurant_id] = name
    save_restaurants(restaurants)

    base_url = "http://localhost:8000"  # Update to your domain in production
    return {"url": f"{base_url}/{restaurant_id}", "name": name}

# Get restaurant name
@router.get("/api/restaurants/{restaurant}")
async def get_restaurant_name(restaurant: str):
    restaurants = load_restaurants()
    if restaurant not in restaurants:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return {"name": restaurants[restaurant]}

# Register restaurant
class RestaurantCreate(BaseModel):
    restaurant_name: str
    admin: str
    address: str
    offers: List[str]  # Accept offers as a list
    points_per_rupee: float = 1.0
    spin_points_per_spin: int = 10
    coupon_expiry_days: int = 30
    max_referrals_per_user: int = 10
    currency: str = "INR"
    reward_expiry_days: int = 90
    referrer_reward_type: str = "points"
    referrer_reward_value: str = "20"
    referred_reward_type: str = "points"
    referred_reward_value: str = "10"

@router.post("/api/register-restaurant")
async def register_restaurant(
    restaurant_data: RestaurantCreate,
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["uid"]
    if user_id != ALLOWED_ADMIN_UID:
        raise HTTPException(status_code=403, detail="Only the designated admin can register restaurants")

    if not restaurant_data.restaurant_name:
        raise HTTPException(status_code=400, detail="Restaurant name is required")
    if restaurant_data.points_per_rupee < 0:
        raise HTTPException(status_code=400, detail="Points per rupee must be non-negative")
    offers = restaurant_data.offers
    if not offers:
        raise HTTPException(status_code=400, detail="At least one offer is required")
    reward_thresholds_dict = {}

    valid_reward_types = ["points", "coupon", "item"]
    if restaurant_data.referrer_reward_type not in valid_reward_types or restaurant_data.referred_reward_type not in valid_reward_types:
        raise HTTPException(status_code=400, detail="Invalid reward type")
    referrer_value = int(restaurant_data.referrer_reward_value) if restaurant_data.referrer_reward_type == "points" else restaurant_data.referrer_reward_value
    referred_value = int(restaurant_data.referred_reward_value) if restaurant_data.referred_reward_type == "points" else restaurant_data.referred_reward_value

    restaurant_id = f"rest_{str(uuid.uuid4())[:8]}"
    restaurant_data_dict = {
        "restaurant_name": restaurant_data.restaurant_name,
        "address": restaurant_data.address,
        "offers": offers,
        "loyalty_settings": {"current": {"points_per_rupee": restaurant_data.points_per_rupee, "reward_thresholds": reward_thresholds_dict}},
        "referral_rewards": {"referrer": {"type": restaurant_data.referrer_reward_type, "value": referrer_value}, "referred": {"type": restaurant_data.referred_reward_type, "value": referred_value}},
        "admin": restaurant_data.admin,  # Store the admin UID
        "created_at": datetime.utcnow().isoformat(),
        "spin_points_per_spin": restaurant_data.spin_points_per_spin,
        "coupon_expiry_days": restaurant_data.coupon_expiry_days,
        "max_referrals_per_user": restaurant_data.max_referrals_per_user,
        "currency": restaurant_data.currency,
        "reward_expiry_days": restaurant_data.reward_expiry_days
    }
    db.collection("restaurants").document(restaurant_id).set(restaurant_data_dict)
    logger.info(f"Restaurant {restaurant_id} registered by admin {user_id}")
    return {"restaurant_id": restaurant_id}
# Update offers and thresholds
@router.post("/api/offers/update")
async def update_offers(
    restaurant_id: str = Form(...),
    reward_thresholds: str = Form(...),
    current_user: dict = Depends(get_current_user)
):
    if not restaurant_exists(restaurant_id) or (await db.collection("restaurants").document(restaurant_id).get()).to_dict()["owner_id"] != current_user["uid"]:
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    thresholds = {}
    for item in reward_thresholds.split(","):
        points, reward = item.split(":")
        thresholds[str(int(points))] = reward
    
    restaurant_ref = db.collection("restaurants").document(restaurant_id)
    restaurant_ref.update({"loyalty_settings.current.reward_thresholds": thresholds})
    
    for user in (await db.collection("loyalty").get()):
        loyalty = user.to_dict()
        spin_points = loyalty.get("spin_points", 0)
        spend_points = loyalty.get("spend_points", 0)
        for points, reward in thresholds.items():
            if (spin_points >= int(points) or spend_points >= int(points)) and not any(r["offer"] == reward for r in loyalty.get("redemption_history", [])):
                db.collection("messages").add({
                    "user_id": user.id,
                    "type": "email",
                    "content": f"New offer unlocked: {reward}",
                    "status": "queued",
                    "scheduled_at": datetime.utcnow().isoformat()
                })
    return {"message": "Thresholds updated, notifications queued"}


@router.get("/api/admin/claimed-rewards")
async def list_claimed_rewards(restaurant_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    logger.info(f"Attempting to list claimed rewards for restaurant {restaurant_id} by user {user_id}")

    restaurant_ref = db.collection("restaurants").document(restaurant_id).get()
    if not restaurant_ref.exists:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    restaurant_data = restaurant_ref.to_dict()

    if restaurant_data.get("admin") != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to view claimed rewards for this restaurant")

    users = db.collection("restaurants").document(restaurant_id).collection("users").stream()
    claimed_rewards = []
    for user_doc in users:
        user_data = user_doc.to_dict()
        if user_data.get("claim_history"):
            claimed_rewards.append({
                "user_id": user_doc.id,
                "name": user_data.get("name", "Unknown"),
                "phone": user_data.get("phone", ""),
                "email": user_data.get("email", ""),
                "claim_history": user_data.get("claim_history", []),
                "spin_history": get_loyalty_data(user_doc.id).get("spin_history", []),
                "spend_history": get_loyalty_data(user_doc.id).get("spend_history", []),
                "redemption_history": get_loyalty_data(user_doc.id).get("redemption_history", [])
            })

    return {"claimed_rewards": claimed_rewards}

@router.get("/api/admin/user-history")
async def get_user_history(restaurant_id: str, user_id: str, current_user: dict = Depends(get_current_user)):
    admin_user_id = current_user["uid"]
    logger.info(f"Attempting to get history for user {user_id} at restaurant {restaurant_id} by admin {admin_user_id}")

    restaurant_ref = db.collection("restaurants").document(restaurant_id).get()
    if not restaurant_ref.exists:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    restaurant_data = restaurant_ref.to_dict()

    if restaurant_data.get("admin") != admin_user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to view user history for this restaurant")

    user_ref = db.collection("restaurants").document(restaurant_id).collection("users").document(user_id).get()
    if not user_ref.exists:
        raise HTTPException(status_code=404, detail="User not found")

    user_data = user_ref.to_dict()
    loyalty_data = get_loyalty_data(user_id)

    return {
        "user_id": user_id,
        "name": user_data.get("name", "Unknown"),
        "phone": user_data.get("phone", ""),
        "email": user_data.get("email", ""),
        "claim_history": user_data.get("claim_history", []),
        "spin_history": loyalty_data.get("spin_history", []),
        "spend_history": loyalty_data.get("spend_history", []),
        "redemption_history": loyalty_data.get("redemption_history", []),
        "total_points": loyalty_data.get("total_points", 0),
        "spin_points": loyalty_data.get("spin_points", 0),
        "spend_points": loyalty_data.get("spend_points", 0)
    }