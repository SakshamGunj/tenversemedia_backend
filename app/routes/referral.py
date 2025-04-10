from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
import uuid
from app.routes.auth import get_current_user
from app.db import db, get_loyalty_data, update_loyalty_data
from firebase_admin import firestore

router = APIRouter()
FRONTEND_BASE_URL = "https://your-frontend-app.com"

@router.get("/api/referral-code")
async def generate_referral_code(restaurant_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    restaurant = (await db.collection("restaurants").document(restaurant_id).get()).to_dict()
    if not restaurant or "referral_rewards" not in restaurant:
        raise HTTPException(status_code=400, detail="Referral rewards not configured")
    
    loyalty_data = get_loyalty_data(user_id)
    referral_codes = loyalty_data.get("referral_codes", [])
    existing_code = next((code for code in referral_codes if code["restaurant_id"] == restaurant_id), None)
    
    if existing_code:
        referral_code = existing_code["code"]
    else:
        referral_code = f"REF-{str(uuid.uuid4())[:8]}"
        referral_codes.append({"restaurant_id": restaurant_id, "code": referral_code})
        await db.collection("loyalty").document(user_id).update({"referral_codes": referral_codes})
    
    referral_url = f"{FRONTEND_BASE_URL}/refer?code={referral_code}&restaurant_id={restaurant_id}"
    return {"referral_code": referral_code, "referral_url": referral_url}

@router.post("/api/referral")
async def process_referral(code: str, restaurant_id: str, current_user: dict = Depends(get_current_user)):
    referred_user_id = current_user["uid"]
    restaurant = (await db.collection("restaurants").document(restaurant_id).get()).to_dict()
    if not restaurant or "referral_rewards" not in restaurant:
        raise HTTPException(status_code=400, detail="Referral rewards not configured")
    
    referrer = None
    for user in (await db.collection("loyalty").get()):
        user_data = user.to_dict()
        for rc in user_data.get("referral_codes", []):
            if rc["code"] == code and rc["restaurant_id"] == restaurant_id:
                referrer = {"user_id": user.id, "data": user_data}
                break
        if referrer:
            break
    
    if not referrer:
        raise HTTPException(status_code=404, detail="Invalid referral code")
    
    referrer_user_id = referrer["user_id"]
    referrer_data = referrer["data"]
    referred_loyalty_data = get_loyalty_data(referred_user_id)
    
    if referred_loyalty_data.get("referred_by") or len([r for r in referrer_data.get("referrals_made", []) if r["restaurant_id"] == restaurant_id]) >= restaurant["max_referrals_per_user"]:
        raise HTTPException(status_code=400, detail="Maximum referrals reached")
    if referred_user_id == referrer_user_id:
        raise HTTPException(status_code=400, detail="Self-referral not allowed")
    
    referral_rewards = restaurant["referral_rewards"]
    now = datetime.utcnow()
    
    # Award the referrer
    referrer_response = {"reward": f"{referral_rewards['referrer']['value']} {referral_rewards['referrer']['type']}", "points_added": 0, "coupon_code": None}
    if referral_rewards["referrer"]["type"] == "points":
        points = int(referral_rewards["referrer"]["value"])
        update_loyalty_data(referrer_user_id, {"total_points": firestore.Increment(points)})
        referrer_response["points_added"] = points
    else:
        coupon_code = f"REF-{str(uuid.uuid4())[:8]}"
        update_loyalty_data(referrer_user_id, {
            "redemption_history": firestore.ArrayUnion([{"reward": referral_rewards["referrer"]["value"], "coupon_code": coupon_code, "claimed_at": now.isoformat()}])
        })
        referrer_response["coupon_code"] = coupon_code
    
    # Award the referred user
    referred_response = {"reward": f"{referral_rewards['referred']['value']} {referral_rewards['referred']['type']}", "points_added": 0, "coupon_code": None}
    if referral_rewards["referred"]["type"] == "points":
        points = int(referral_rewards["referred"]["value"])
        update_loyalty_data(referred_user_id, {"total_points": firestore.Increment(points)})
        referred_response["points_added"] = points
    else:
        coupon_code = f"REF-{str(uuid.uuid4())[:8]}"
        update_loyalty_data(referred_user_id, {
            "redemption_history": firestore.ArrayUnion([{"reward": referral_rewards["referred"]["value"], "coupon_code": coupon_code, "claimed_at": now.isoformat()}])
        })
        referred_response["coupon_code"] = coupon_code
    
    # Update referral data
    update_loyalty_data(referrer_user_id, {
        "referrals_made": firestore.ArrayUnion([{"restaurant_id": restaurant_id, "referred_user_id": referred_user_id, "timestamp": now.isoformat()}])
    })
    update_loyalty_data(referred_user_id, {
        "referred_by": {"restaurant_id": restaurant_id, "referrer_user_id": referrer_user_id},
        "visited_restaurants": firestore.ArrayUnion([restaurant_id])
    })
    
    redirect_url = f"{FRONTEND_BASE_URL}/spin-wheel?restaurant_id={restaurant_id}"
    return {"message": "Referral processed successfully", "referrer_reward": referrer_response, "referred_reward": referred_response, "redirect_url": redirect_url}