# app/routes/tracking.py (New File)
from fastapi import APIRouter, HTTPException, Depends
from app.routes.auth import get_current_user
from app.db import db, get_loyalty_data, restaurant_exists
import logging
from datetime import datetime

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/api/track/spending")
async def track_spending(restaurant_id: str, amount: float, current_user: dict = Depends(get_current_user)):
    if amount < 0:
        raise HTTPException(status_code=400, detail="Amount must be non-negative")
    if not restaurant_exists(restaurant_id):
        raise HTTPException(status_code=404, detail="Restaurant not found")
    restaurant_data = db.collection("restaurants").document(restaurant_id).get().to_dict()
    points = int(amount * restaurant_data["loyalty_settings"]["current"]["points_per_rupee"])
    user_id = current_user["uid"]
    update_loyalty_data(user_id, {
        "spend_points": firestore.Increment(points),
        "spend_history": firestore.ArrayUnion([{"amount": amount, "points": points, "date": datetime.utcnow().isoformat(), "restaurant_id": restaurant_id}])
    })
    db.collection("audit_logs").add({
        "user_id": user_id, "action": "track_spending", "details": {"points": points},
        "timestamp": datetime.utcnow().isoformat()
    })
    return {"message": "Spending tracked", "points_awarded": points}

@router.post("/api/track/spins")
async def track_spins(restaurant_id: str, current_user: dict = Depends(get_current_user)):
    if not restaurant_exists(restaurant_id):
        raise HTTPException(status_code=404, detail="Restaurant not found")
    restaurant_data = db.collection("restaurants").document(restaurant_id).get().to_dict()
    points = restaurant_data.get("spin_points_per_spin", 10)
    user_id = current_user["uid"]
    update_loyalty_data(user_id, {
        "spin_points": firestore.Increment(points),
        "spin_history": firestore.ArrayUnion([{"points": points, "won_at": datetime.utcnow().isoformat(), "restaurant_id": restaurant_id}])
    })
    db.collection("audit_logs").add({
        "user_id": user_id, "action": "track_spins", "details": {"points": points},
        "timestamp": datetime.utcnow().isoformat()
    })
    return {"message": "Spin tracked", "points_awarded": points}

@router.post("/api/track/claimed-rewards")
async def track_claimed_rewards(coupon_id: str, restaurant_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    coupon = db.collection("coupons").document(coupon_id).get().to_dict()
    if not coupon or coupon["user_id"] != user_id or coupon["is_used"] or coupon["expiry_date"] < datetime.utcnow().date():
        raise HTTPException(status_code=400, detail="Coupon invalid, used, or expired")
    db.collection("coupons").document(coupon_id).update({"is_used": True, "redeemed_at": datetime.utcnow().isoformat()})
    update_loyalty_data(user_id, {
        "claim_history": firestore.ArrayUnion([{"coupon_id": coupon_id, "date": datetime.utcnow().isoformat(), "restaurant_id": restaurant_id}])
    })
    db.collection("audit_logs").add({
        "user_id": user_id, "action": "claim_reward", "details": {"coupon_id": coupon_id},
        "timestamp": datetime.utcnow().isoformat()
    })
    return {"message": "Claim tracked"}