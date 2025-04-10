from fastapi import APIRouter, HTTPException, Depends, Form
from app.routes.auth import get_current_user, is_admin
from app.db import db
from datetime import datetime
import logging
from google.cloud import firestore

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/api/coupons")
async def list_coupons(current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    coupons = []
    for doc in db.collection("coupons").where("user_id", "==", user_id).stream():
        coupon_data = doc.to_dict()
        coupons.append({
            "coupon_id": doc.id,
            "offer": coupon_data["offer"],
            "expiry_date": str(coupon_data["expiry_date"]),
            "is_used": coupon_data["is_used"]
        })
    return coupons

@router.get("/api/admin/coupons")
async def list_all_coupons(restaurant_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    # Validate restaurant existence (optional, depending on your needs)
    restaurant_ref = db.collection("restaurants").document(restaurant_id).get()
    if not restaurant_ref.exists:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    
    if restaurant_ref.get("admin") != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to view coupons for this restaurant")

    coupons = []
    for doc in db.collection("coupons").where("restaurant_id", "==", restaurant_id).stream():
        coupon_data = doc.to_dict()
        coupons.append({
            "coupon_id": doc.id,
            "user_id": coupon_data["user_id"],
            "restaurant_id": coupon_data["restaurant_id"],
            "offer": coupon_data["offer"],
            "expiry_date": str(coupon_data["expiry_date"]),
            "is_used": coupon_data["is_used"],
            "created_at": coupon_data.get("created_at", ""),
            "redeemed_at": coupon_data.get("redeemed_at", "")
        })
    return {"coupons": coupons}

@router.post("/api/coupons/redeem")
async def redeem_coupon(coupon_id: str = Form(...), current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    logger.info(f"Attempting to redeem coupon {coupon_id} for user {user_id}")

    coupon_ref = db.collection("coupons").document(coupon_id)
    coupon = coupon_ref.get()

    if not coupon.exists:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon_data = coupon.to_dict()

    if coupon_data["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Unauthorized to redeem this coupon")
    if coupon_data["is_used"]:
        raise HTTPException(status_code=400, detail="Coupon already used")
    expiry_date = datetime.fromisoformat(coupon_data["expiry_date"]).date()
    if datetime.utcnow().date() > expiry_date:
        raise HTTPException(status_code=400, detail="Coupon expired")

    @firestore.transactional
    def update_coupon(transaction):
        updated_coupon = coupon_ref.get(transaction=transaction).to_dict()
        if updated_coupon["is_used"] or datetime.utcnow().date() > expiry_date:
            raise HTTPException(status_code=400, detail="Coupon invalid, used, or expired")
        transaction.update(coupon_ref, {"is_used": True, "redeemed_at": datetime.utcnow().isoformat()})
        return {"message": "Coupon redeemed"}

    transaction = db.transaction()
    result = update_coupon(transaction)
    logger.info(f"Coupon {coupon_id} redeemed by user {user_id}")
    return result

@router.post("/api/coupons/edit-expiry")
async def edit_coupon_expiry(coupon_id: str = Form(...), new_expiry_date: str = Form(...), current_user: dict = Depends(is_admin)):
    user_id = current_user["uid"]
    logger.info(f"Attempting to edit expiry for coupon {coupon_id} by admin {user_id}")

    coupon_ref = db.collection("coupons").document(coupon_id)
    coupon = coupon_ref.get()
    if not coupon.exists:
        raise HTTPException(status_code=404, detail="Coupon not found")
    coupon_data = coupon.to_dict()

    new_date = datetime.strptime(new_expiry_date, "%Y-%m-%d").date()
    if new_date < datetime.utcnow().date():
        raise HTTPException(status_code=400, detail="Expiry date must be in the future")
    
    coupon_ref.update({"expiry_date": new_expiry_date})
    logger.info(f"Expiry updated for coupon {coupon_id} to {new_expiry_date}")
    return {"message": "Expiry updated"}