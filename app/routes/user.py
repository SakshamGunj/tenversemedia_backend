from fastapi import APIRouter, Depends, HTTPException
from app.routes.auth import get_current_user
from app.db import db, get_loyalty_data, get_user_submission, get_offer
import logging
from typing import Dict, List
import datetime

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/api/user-dashboard")
async def user_dashboard(current_user: dict = Depends(get_current_user)):
    user_id = current_user["uid"]
    logger.info(f"Fetching dashboard data for user {user_id}")

    # Fetch loyalty data
    loyalty_data = get_loyalty_data(user_id)
    if not loyalty_data:
        logger.error(f"No loyalty data found for user {user_id}")
        raise HTTPException(status_code=404, detail="No loyalty data found")

    # Fetch all submissions for the user across all restaurants
    restaurants = db.collection("restaurants").stream()
    submissions = []
    reward_progress = []  # New list to store reward threshold progress

    # Get user's restaurant points
    user_restaurant_points = loyalty_data.get("restaurant_points", {})

    for restaurant in restaurants:
        restaurant_id = restaurant.id
        restaurant_doc = restaurant.to_dict()

        # Check if the user has a submission for this restaurant
        submission_doc = db.collection("restaurants").document(restaurant_id).collection("users").document(user_id).get()
        if submission_doc.exists:
            # Add submission data
            submission_data = submission_doc.to_dict()
            submission_data["restaurant_id"] = restaurant_id
            submissions.append(submission_data)

            # Calculate reward threshold progress for this restaurant
            restaurant_name = restaurant_doc.get("restaurant_name", restaurant_id)
            loyalty_settings = restaurant_doc.get("loyalty_settings", {}).get("current", {})
            reward_thresholds = loyalty_settings.get("reward_thresholds", {})

            # Get user's points for this restaurant
            current_points = user_restaurant_points.get(restaurant_id, 0)

            if reward_thresholds:
                # Sort thresholds in ascending order
                thresholds_sorted = sorted(reward_thresholds.keys(), key=int)
                progress = {
                    "restaurant_id": restaurant_id,
                    "restaurant_name": restaurant_name,
                    "current_points": current_points,
                    "thresholds": []
                }

                # Check if user has reached all thresholds
                max_threshold = int(thresholds_sorted[-1]) if thresholds_sorted else 0
                if current_points >= max_threshold:
                    progress["status"] = "Maximum reward level reached"
                else:
                    progress["status"] = "In progress"

                # Calculate points left for each threshold
                for threshold in thresholds_sorted:
                    threshold_points = int(threshold)
                    reward = reward_thresholds[threshold]
                    points_left = max(0, threshold_points - current_points)
                    progress["thresholds"].append({
                        "threshold_points": threshold_points,
                        "reward": reward,
                        "points_left": points_left,
                        "achieved": current_points >= threshold_points
                    })

                reward_progress.append(progress)
            else:
                # No thresholds defined for this restaurant
                reward_progress.append({
                    "restaurant_id": restaurant_id,
                    "restaurant_name": restaurant_name,
                    "current_points": current_points,
                    "thresholds": [],
                    "status": "No reward thresholds defined"
                })

    # Prepare dashboard data
    dashboard_data = {
        "total_points": loyalty_data.get("total_points", 0),
        "tier": loyalty_data.get("tier", "Bronze"),
        "punches": loyalty_data.get("punches", 0),
        "restaurant_points": user_restaurant_points,
        "redemption_history": loyalty_data.get("redemption_history", []),
        "submissions": submissions,
        "reward_progress": reward_progress  # New section for reward progress
    }

    logger.info(f"Returning dashboard data for user {user_id}")
    return {"status": "success", "dashboard": dashboard_data}