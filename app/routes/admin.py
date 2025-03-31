from fastapi import APIRouter, Form, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from app.routes.auth import get_current_user
from app.db import get_submissions, get_loyalty_data, db, restaurant_exists
import logging
from typing import List, Dict, Optional
from google.cloud.firestore_v1.transaction import Transaction
from google.cloud.firestore_v1 import ArrayUnion
from datetime import datetime
import os
import json
import uuid

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

async def get_restaurant_id(request: Request) -> str:
    restaurant_id = request.query_params.get("restaurant_id")
    if not restaurant_id:
        logger.error("Missing restaurant_id in request")
        raise HTTPException(status_code=400, detail="restaurant_id is required")
    return restaurant_id

@router.get(
    "/admin",
    response_class=HTMLResponse,
    tags=["Admin"],
    summary="Admin dashboard for a restaurant",
    description="Displays a dashboard of submissions and loyalty data for a specific restaurant.",
    responses={
        200: {"description": "Admin dashboard rendered"},
        400: {"description": "Missing restaurant_id"},
        401: {"description": "Invalid token"},
        500: {"description": "Server error"}
    }
)
async def admin_dashboard(
    request: Request,
    current_user: dict = Depends(get_current_user),
    restaurant_id: str = Depends(get_restaurant_id),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    try:
        submissions, total = get_submissions(restaurant_id, page, limit)
        loyalty_data = {
            s.get("id", ""): {
                "total_points": get_loyalty_data(s.get("id", "")).get("total_points", 0),
                "tier": get_loyalty_data(s.get("id", "")).get("tier", "Bronze"),
                "punches": get_loyalty_data(s.get("id", "")).get("punches", 0),
                "restaurant_points": get_loyalty_data(s.get("id", "")).get("restaurant_points", {}).get(restaurant_id, 0)
            } for s in submissions
        }
    except Exception as e:
        logger.error(f"Admin dashboard error for {restaurant_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard data")
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "submissions": submissions,
        "restaurant_id": restaurant_id,
        "loyalty_data": loyalty_data,
        "page": page,
        "limit": limit,
        "total": total
    })

@router.post("/api/register-restaurant")
async def register_restaurant(
    restaurant_name: str = Form(...),
    offer1: str = Form(default=""),
    offer2: str = Form(default=""),
    offer3: str = Form(default=""),
    offer4: str = Form(default=""),
    offer5: str = Form(default=""),
    offer6: str = Form(default=""),
    points_per_rupee: float = Form(default=1.0),
    reward_thresholds: str = Form(default=""),
    referrer_reward_type: str = Form(default="points"),  # New: "points", "coupon", or "item"
    referrer_reward_value: str = Form(default="20"),    # New: e.g., "20" for points, "5% Off" for coupon
    referred_reward_type: str = Form(default="points"), # New: "points", "coupon", or "item"
    referred_reward_value: str = Form(default="10"),    # New: e.g., "10" for points, "5% Off" for coupon
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["uid"]

    if points_per_rupee < 0:
        raise HTTPException(status_code=400, detail="Points per rupee must be non-negative")

    # Collect offers
    offers = [offer for offer in [offer1, offer2, offer3, offer4, offer5, offer6] if offer]
    if not offers:
        raise HTTPException(status_code=400, detail="At least one offer is required")

    # Parse reward thresholds
    reward_thresholds_dict = {}
    if reward_thresholds:
        for pair in reward_thresholds.split(","):
            points, reward = pair.split(":")
            reward_thresholds_dict[points.strip()] = reward.strip()

    # Validate referral reward types
    valid_reward_types = ["points", "coupon", "item"]
    if referrer_reward_type not in valid_reward_types or referred_reward_type not in valid_reward_types:
        raise HTTPException(status_code=400, detail="Invalid reward type. Must be 'points', 'coupon', or 'item'")

    # Convert reward values for points
    referrer_value = int(referrer_reward_value) if referrer_reward_type == "points" else referrer_reward_value
    referred_value = int(referred_reward_value) if referred_reward_type == "points" else referred_reward_value

    # Generate restaurant ID
    restaurant_id = f"rest_{str(uuid.uuid4())[:8]}"

    # Save restaurant data
    restaurant_data = {
        "restaurant_name": restaurant_name,
        "offers": offers,
        "loyalty_settings": {
            "current": {
                "points_per_rupee": points_per_rupee,
                "reward_thresholds": reward_thresholds_dict
            }
        },
        "referral_rewards": {
            "referrer": {"type": referrer_reward_type, "value": referrer_value},
            "referred": {"type": referred_reward_type, "value": referred_value}
        },
        "owner_id": user_id,
        "created_at": datetime.utcnow().isoformat()
    }
    await db.collection("restaurants").document(restaurant_id).set(restaurant_data)

    return {"restaurant_id": restaurant_id}