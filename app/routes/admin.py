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

@router.post(
    "/api/register-restaurant",
    tags=["Admin"],
    summary="Register a new restaurant",
    description="Registers a new restaurant with name, 6 offers, and initial loyalty settings.",
    responses={
        200: {"description": "Restaurant registered"},
        400: {"description": "Invalid input"},
        401: {"description": "Invalid token"},
        500: {"description": "Server error"}
    }
)
async def register_restaurant(
    restaurant_name: str = Form(...),
    offer1: str = Form(...),
    offer2: str = Form(...),
    offer3: str = Form(...),
    offer4: str = Form(...),
    offer5: str = Form(...),
    offer6: str = Form(...),
    points_per_rupee: float = Form(1.0, ge=0.1),
    reward_thresholds: str = Form("1000:20%,2000:30%"),  # Format: "points:reward,points:reward"
    current_user: dict = Depends(get_current_user)
):
    if not restaurant_name.strip():
        logger.error("Invalid restaurant name")
        raise HTTPException(status_code=400, detail="Restaurant name cannot be empty")
    
    restaurant_id = restaurant_name.lower().replace(" ", "_")
    if restaurant_exists(restaurant_id):
        logger.error(f"Restaurant {restaurant_id} already exists")
        raise HTTPException(status_code=400, detail="Restaurant already exists")

    offers = [o.strip() for o in [offer1, offer2, offer3, offer4, offer5, offer6] if o and o.strip()]
    if len(offers) < 6:
        logger.error(f"Only {len(offers)} valid offers provided, 6 required")
        raise HTTPException(status_code=400, detail="All 6 offers are required and must be non-empty")

    # Parse reward thresholds and convert keys to strings
    try:
        thresholds = {}
        for item in reward_thresholds.split(","):
            points, reward = [s.strip() for s in item.split(":")]
            if not points.isdigit() or not reward:
                raise ValueError
            thresholds[str(int(points))] = reward  # Convert points to string
        if not thresholds or min([int(k) for k in thresholds.keys()]) < 0:
            raise ValueError
    except ValueError:
        logger.error(f"Invalid reward thresholds format: {reward_thresholds}")
        raise HTTPException(status_code=400, detail="Reward thresholds must be in format 'points:reward,points:reward'")

    try:
        with db.transaction() as transaction:
            restaurant_ref = db.collection("restaurants").document(restaurant_id)
            initial_settings = {
                "points_per_rupee": points_per_rupee,
                "reward_thresholds": thresholds
            }
            history_entry = {
                "points_per_rupee": points_per_rupee,
                "reward_thresholds": thresholds,
                "timestamp": datetime.utcnow().isoformat()
            }
            # Log the document data for debugging
            document_data = {
                "name": restaurant_name,
                "offers": offers,
                "loyalty_settings": {
                    "current": initial_settings,
                    "history": [history_entry]
                }
            }
            logger.info(f"Attempting to save document for restaurant {restaurant_id}: {document_data}")

            # Set the initial document
            transaction.set(restaurant_ref, document_data)
            # Ensure loyalty_settings/current is set separately
            transaction.set(restaurant_ref.collection("loyalty_settings").document("current"), initial_settings)

        logger.info(f"Registered restaurant {restaurant_id} with offers {offers}")
        return {"message": f"Restaurant {restaurant_name} registered with ID {restaurant_id}"}
    except Exception as e:
        logger.error(f"Failed to register restaurant {restaurant_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to register restaurant")