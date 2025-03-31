from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
import uuid
from app.routes.auth import get_current_user
from app.db import db

router = APIRouter()

# Base URL for the frontend (replace with your actual frontend URL)
FRONTEND_BASE_URL = "https://your-frontend-app.com"

@router.get("/api/referral-code")
async def generate_referral_code(restaurant_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]

    # Validate restaurant
    restaurant_ref = db.collection("restaurants").document(restaurant_id)
    restaurant = (await restaurant_ref.get()).to_dict()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if "referral_rewards" not in restaurant:
        raise HTTPException(status_code=400, detail="Referral rewards not configured for this restaurant")

    # Fetch user's loyalty data
    loyalty_ref = db.collection("loyalty").document(user_id)
    loyalty_data = (await loyalty_ref.get()).to_dict() or {
        "total_points": 0,
        "tier": "Bronze",
        "punches": 0,
        "restaurant_points": {},
        "redemption_history": [],
        "visited_restaurants": [],
        "last_spin_time": None,
        "spin_history": [],
        "referral_codes": [],
        "referrals_made": [],
        "referred_by": None
    }

    # Check if the user already has a referral code for this restaurant
    referral_codes = loyalty_data.get("referral_codes", [])
    existing_code = next((code for code in referral_codes if code["restaurant_id"] == restaurant_id), None)
    if existing_code:
        referral_code = existing_code["code"]
    else:
        # Generate a new referral code
        referral_code = f"REF-{str(uuid.uuid4())[:8]}"
        referral_codes.append({"restaurant_id": restaurant_id, "code": referral_code})
        loyalty_data["referral_codes"] = referral_codes
        await loyalty_ref.set(loyalty_data)

    # Generate the referral URL
    referral_url = f"{FRONTEND_BASE_URL}/refer?code={referral_code}&restaurant_id={restaurant_id}"

    return {"referral_code": referral_code, "referral_url": referral_url}

@router.post("/api/referral")
async def process_referral(code: str, restaurant_id: str, current_user: dict = Depends(get_current_user)):
    referred_user_id = current_user["uid"]

    # Validate restaurant
    restaurant_ref = db.collection("restaurants").document(restaurant_id)
    restaurant = (await restaurant_ref.get()).to_dict()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    if "referral_rewards" not in restaurant:
        raise HTTPException(status_code=400, detail="Referral rewards not configured for this restaurant")

    # Find the referrer by referral code
    referrer = None
    users_ref = db.collection("loyalty")
    users = await users_ref.get()
    for user in users:
        user_data = user.to_dict()
        referral_codes = user_data.get("referral_codes", [])
        for rc in referral_codes:
            if rc["code"] == code and rc["restaurant_id"] == restaurant_id:
                referrer = {"user_id": user.id, "data": user_data}
                break
        if referrer:
            break

    if not referrer:
        raise HTTPException(status_code=404, detail="Invalid referral code")

    referrer_user_id = referrer["user_id"]
    referrer_data = referrer["data"]

    # Check if the referred user has already been referred for this restaurant
    referred_loyalty_ref = db.collection("loyalty").document(referred_user_id)
    referred_loyalty_data = (await referred_loyalty_ref.get()).to_dict() or {
        "total_points": 0,
        "tier": "Bronze",
        "punches": 0,
        "restaurant_points": {},
        "redemption_history": [],
        "visited_restaurants": [],
        "last_spin_time": None,
        "spin_history": [],
        "referral_codes": [],
        "referrals_made": [],
        "referred_by": None
    }

    if referred_loyalty_data.get("referred_by"):
        raise HTTPException(status_code=400, detail="User has already been referred")

    # Check if the referrer has already referred this user for this restaurant
    referrals_made = referrer_data.get("referrals_made", [])
    if any(ref["referred_user_id"] == referred_user_id and ref["restaurant_id"] == restaurant_id for ref in referrals_made):
        raise HTTPException(status_code=400, detail="This user has already been referred by you for this restaurant")

    # Process rewards
    referral_rewards = restaurant["referral_rewards"]
    now = datetime.utcnow()

    # Award the referrer
    referrer_reward = referral_rewards["referrer"]
    referrer_response = {"reward": f"{referrer_reward['value']} {referrer_reward['type']}", "points_added": 0, "coupon_code": None}
    if referrer_reward["type"] == "points":
        points_to_add = int(referrer_reward["value"])
        referrer_data["total_points"] = referrer_data.get("total_points", 0) + points_to_add
        referrer_response["points_added"] = points_to_add
    elif referrer_reward["type"] in ["coupon", "item"]:
        coupon_code = f"REF-{str(uuid.uuid4())[:8]}"
        referrer_data["redemption_history"].append({
            "reward": referrer_reward["value"],
            "coupon_code": coupon_code,
            "claimed_at": now.isoformat()
        })
        referrer_response["coupon_code"] = coupon_code

    # Award the referred user
    referred_reward = referral_rewards["referred"]
    referred_response = {"reward": f"{referred_reward['value']} {referred_reward['type']}", "points_added": 0, "coupon_code": None}
    if referred_reward["type"] == "points":
        points_to_add = int(referred_reward["value"])
        referred_loyalty_data["total_points"] = referred_loyalty_data.get("total_points", 0) + points_to_add
        referred_response["points_added"] = points_to_add
    elif referred_reward["type"] in ["coupon", "item"]:
        coupon_code = f"REF-{str(uuid.uuid4())[:8]}"
        referred_loyalty_data["redemption_history"].append({
            "reward": referred_reward["value"],
            "coupon_code": coupon_code,
            "claimed_at": now.isoformat()
        })
        referred_response["coupon_code"] = coupon_code

    # Update referrer's data
    referrer_data["referrals_made"] = referrals_made + [{
        "restaurant_id": restaurant_id,
        "referred_user_id": referred_user_id,
        "timestamp": now.isoformat()
    }]
    await db.collection("loyalty").document(referrer_user_id).set(referrer_data)

    # Update referred user's data
    referred_loyalty_data["referred_by"] = {
        "restaurant_id": restaurant_id,
        "referrer_user_id": referrer_user_id
    }
    if restaurant_id not in referred_loyalty_data.get("visited_restaurants", []):
        referred_loyalty_data["visited_restaurants"] = referred_loyalty_data.get("visited_restaurants", []) + [restaurant_id]
    await referred_loyalty_ref.set(referred_loyalty_data)

    # Generate redirect URL to the Spin the Wheel app
    redirect_url = f"{FRONTEND_BASE_URL}/spin-wheel?restaurant_id={restaurant_id}"

    return {
        "message": "Referral processed successfully",
        "referrer_reward": referrer_response,
        "referred_reward": referred_response,
        "redirect_url": redirect_url
    }