from fastapi import APIRouter, Form, HTTPException, Depends
from app.routes.auth import get_current_user
from app.db import get_loyalty_data, update_loyalty_data, restaurant_exists, db
from datetime import datetime
import logging
from google.cloud import firestore

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post(
    "/api/loyalty-settings",
    tags=["Admin"],
    summary="Update loyalty settings for a restaurant",
    description="Updates points per rupee and reward thresholds with retroactive application.",
    responses={
        200: {"description": "Settings updated"},
        400: {"description": "Invalid input"},
        401: {"description": "Invalid token"},
        500: {"description": "Server error"}
    }
)
async def update_loyalty_settings(
    restaurant_id: str = Form(...),
    points_per_rupee: float = Form(1.0, ge=0.1),
    reward_thresholds: str = Form("1000:20%,2000:30%"),  # Format: "points:reward,points:reward"
    current_user: dict = Depends(get_current_user)
):
    if not restaurant_exists(restaurant_id):
        logger.error(f"Restaurant {restaurant_id} not found")
        raise HTTPException(status_code=400, detail="Restaurant not found")

    try:
        thresholds = {}
        for item in reward_thresholds.split(","):
            points, reward = item.split(":")
            thresholds[str(int(points))] = reward  # Convert points to string
        if not thresholds or min([int(k) for k in thresholds.keys()]) < 0:
            raise ValueError
    except ValueError:
        logger.error(f"Invalid reward thresholds format: {reward_thresholds}")
        raise HTTPException(status_code=400, detail="Reward thresholds must be in format 'points:reward,points:reward'")

    try:
        restaurant_ref = db.collection("restaurants").document(restaurant_id)
        restaurant_data = restaurant_ref.get().to_dict()
        current_settings = restaurant_data.get("loyalty_settings", {}).get("current", {})
        current_thresholds = current_settings.get("reward_thresholds", {})
        history = restaurant_data.get("loyalty_settings", {}).get("history", [])

        # Update settings with transaction
        with db.transaction() as transaction:
            # Apply retroactive rewards
            users_ref = restaurant_ref.collection("users")
            users = users_ref.stream(transaction=transaction)
            for user_doc in users:
                user_id = user_doc.id
                loyalty = get_loyalty_data(user_id)
                total_points = loyalty.get("total_points", 0)
                old_reward = next((r for p, r in sorted(current_thresholds.items(), key=lambda x: int(x[0])) if total_points >= int(p)), None)
                new_reward = next((r for p, r in sorted(thresholds.items(), key=lambda x: int(x[0])) if total_points >= int(p)), None)

                if old_reward and total_points >= int(min(current_thresholds.keys())) and (not new_reward or old_reward != new_reward):
                    update_loyalty_data(user_id, {
                        "redemption_history": firestore.ArrayUnion([{
                            "restaurant_id": restaurant_id,
                            "reward": old_reward,
                            "date": datetime.utcnow().isoformat(),
                            "type": "retroactive"
                        }])
                    })
                elif total_points > int(max(thresholds.keys())) and new_reward:
                    next_threshold = min([int(p) for p in thresholds.keys() if int(p) > total_points], default=None)
                    if next_threshold:
                        update_loyalty_data(user_id, {
                            "redemption_history": firestore.ArrayUnion([{
                                "restaurant_id": restaurant_id,
                                "reward": thresholds[str(next_threshold)],
                                "date": datetime.utcnow().isoformat(),
                                "type": "next_tier"
                            }])
                        })

            # Update restaurant settings
            new_settings = {"points_per_rupee": points_per_rupee, "reward_thresholds": thresholds}
            transaction.set(restaurant_ref.collection("loyalty_settings").document("current"), new_settings)
            history.append({
                "points_per_rupee": points_per_rupee,
                "reward_thresholds": thresholds,
                "timestamp": datetime.utcnow().isoformat()
            })
            transaction.update(restaurant_ref, {"loyalty_settings.history": history})

        logger.info(f"Updated loyalty settings for {restaurant_id}")
        return {"message": f"Loyalty settings updated for {restaurant_id}"}
    except Exception as e:
        logger.error(f"Failed to update loyalty settings for {restaurant_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update loyalty settings")

@router.get("/api/loyalty/balance")
async def get_loyalty_balance(current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    loyalty_data = get_loyalty_data(user_id)
    return {
        "total_points": loyalty_data["total_points"],
        "tier": loyalty_data.get("tier", "Bronze"),
        "punches": loyalty_data["punches"],
        "restaurant_points": loyalty_data["restaurant_points"],
        "referral_code": loyalty_data.get("referral_code", f"REF{user_id[:8]}")
    }

@router.post("/api/loyalty/redeem")
async def redeem_loyalty(
    restaurant_id: str = Form(...),
    reward_type: str = Form(...),
    points_value: int = Form(None),
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["uid"]
    loyalty_data = get_loyalty_data(user_id)

    if reward_type == "punch_card":
        if loyalty_data["punches"] < 10:
            raise HTTPException(status_code=400, detail="Insufficient punches. Need 10 to redeem.")
        reward = "Free Dessert"
        update_loyalty_data(user_id, {"punches": 0})
        update_loyalty_data(user_id, {
            "redemption_history": firestore.ArrayUnion([{
                "restaurant_id": restaurant_id,
                "reward": reward,
                "date": datetime.utcnow().isoformat()
            }])
        })
        return {
            "message": "Reward redeemed successfully",
            "reward": reward,
            "remaining_points": loyalty_data["total_points"],
            "remaining_punches": 0,
            "tier": loyalty_data.get("tier", "Bronze")
        }
    elif reward_type == "points":
        if not points_value or loyalty_data["total_points"] < points_value:
            raise HTTPException(status_code=400, detail="Insufficient points.")
        if points_value < 50:
            raise HTTPException(status_code=400, detail="Minimum 50 points required for discount.")
        reward = f"{int(points_value / 50)}% off"
        update_loyalty_data(user_id, {"total_points": firestore.Increment(-points_value)})
        new_points = loyalty_data["total_points"] - points_value
        tier = "Gold" if new_points >= 300 else "Silver" if new_points >= 100 else "Bronze"
        update_loyalty_data(user_id, {"tier": tier})
        update_loyalty_data(user_id, {
            "redemption_history": firestore.ArrayUnion([{
                "restaurant_id": restaurant_id,
                "reward": reward,
                "date": datetime.utcnow().isoformat()
            }])
        })
        return {
            "message": "Reward redeemed successfully",
            "reward": reward,
            "remaining_points": new_points,
            "remaining_punches": loyalty_data["punches"],
            "tier": tier
        }
    raise HTTPException(status_code=400, detail="Invalid reward type. Use 'punch_card' or 'points'.")