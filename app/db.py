from firebase_admin import credentials, firestore, initialize_app
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging
import os
from aiocache import cached
from aiocache.serializers import PickleSerializer

# Define the path to your service account key file
SERVICE_ACCOUNT_KEY_PATH = "C:/Users\Manoj Subba\superqrbackend/spinthewheel-e14a6-firebase-adminsdk-fbsvc-f991f1fc18.json"  

try:
    # Load the service account key directly
    cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
    # Initialize the app with explicit credentials and project ID
    initialize_app(cred, {
        'projectId': 'spinthewheel-e14a6'  # Your Firebase project ID
    })
    # Initialize Firestore client using Firebase Admin SDK
    db = firestore.async_client()
    logging.info("Firebase initialized successfully with explicit credentials")
except FileNotFoundError:
    logging.error(f"Service account key file not found at {SERVICE_ACCOUNT_KEY_PATH}")
    raise Exception(f"Service account key file not found at {SERVICE_ACCOUNT_KEY_PATH}. Please download it from the Firebase Console and update the path in app/db.py.")
except ValueError as e:
    logging.error(f"Invalid service account key file: {e}")
    raise Exception(f"Invalid service account key file: {e}. Ensure the JSON file is correct.")
except Exception as e:
    logging.error(f"Failed to initialize Firebase: {e}")
    raise Exception(f"Firebase initialization failed: {e}")

def restaurant_exists(restaurant_id: str) -> bool:
    """Check if a restaurant exists in the Firestore database."""
    try:
        doc = db.collection("restaurants").document(restaurant_id).get()
        return doc.exists
    except Exception as e:
        logging.error(f"Failed to check if restaurant {restaurant_id} exists: {e}")
        return False

def save_submission(restaurant_id: str, submission_data: Dict, user_id: str) -> None:
    """Save a user's reward submission to Firestore."""
    submission_data["id"] = user_id
    submission_data["submitted_at"] = datetime.utcnow()
    submission_data["currency"] = db.collection("restaurants").document(restaurant_id).get().to_dict().get("currency", "INR")
    db.collection("restaurants").document(restaurant_id).collection("users").document(user_id).set(submission_data)

def get_submissions(restaurant_id: str, page: int = 1, limit: int = 10, email: Optional[str] = None,
                   reward: Optional[str] = None, start_date: Optional[datetime] = None,
                   end_date: Optional[datetime] = None) -> Tuple[List[Dict], int]:
    """Retrieve paginated submissions with filters."""
    query = db.collection("restaurants").document(restaurant_id).collection("users")
    
    if email:
        query = query.where("email", "==", email)
    if reward:
        query = query.where("reward", "==", reward)
    if start_date:
        query = query.where("submitted_at", ">=", start_date)
    if end_date:
        query = query.where("submitted_at", "<=", end_date)

    query = query.order_by("submitted_at", direction=firestore.Query.DESCENDING)
    query = query.offset((page - 1) * limit).limit(limit)
    
    docs = query.stream()
    submissions = []
    for doc in docs:
        submission = doc.to_dict()
        if "submitted_at" in submission and isinstance(submission["submitted_at"], datetime):
            submission["submitted_at"] = submission["submitted_at"].isoformat()
        submissions.append(submission)
    return submissions, len(submissions)

def get_user_submission(restaurant_id: str, user_id: str) -> Optional[Dict]:
    """Retrieve a user's submission."""
    doc = db.collection("restaurants").document(restaurant_id).collection("users").document(user_id).get()
    if doc.exists:
        submission = doc.to_dict()
        if "submitted_at" in submission and isinstance(submission["submitted_at"], datetime):
            submission["submitted_at"] = submission["submitted_at"].isoformat()
        return submission
    return None

def get_offer(restaurant_id: str) -> Dict:
    """Retrieve the current offer for a restaurant."""
    doc = db.collection("restaurants").document(restaurant_id).collection("offers").document("current_offer").get()
    return doc.to_dict() if doc.exists else {"offer": "10% off your next visit"}


@cached(ttl=300, serializer=PickleSerializer())
async def get_loyalty_data(user_id: str) -> Dict:
    doc = db.collection("loyalty").document(user_id).get()
    doc_data = doc.to_dict() or {}
    default_data = {
        "total_points": 0, "spin_points": 0, "spend_points": 0, "punches": 0, "tier": "Bronze",
        "restaurant_points": {}, "referral_code": f"REF{user_id[:8]}", "redemption_history": [],
        "spin_history": [], "spend_history": [], "claim_history": [], "notification_preferences": {"email": True, "sms": True, "whatsapp": True}
    }
    return {**default_data, **doc_data}

def update_loyalty_data(user_id: str, updates: dict, batch=None) -> dict:
    doc_ref = db.collection("loyalty").document(user_id)
    if batch:
        batch.set(doc_ref, updates, merge=True)
        return get_loyalty_data(user_id)  # Return current state for consistency
    else:
        @firestore.transactional
        def update_with_transaction(transaction, doc_ref, updates):
            snapshot = doc_ref.get(transaction=transaction)
            current_data = snapshot.to_dict() or {
                "total_points": 0, "spin_points": 0, "spend_points": 0, "punches": 0, "tier": "Bronze",
                "restaurant_points": {}, "referral_code": f"REF{user_id[:8]}", "redemption_history": [],
                "spin_history": [], "spend_history": [], "claim_history": [], "notification_preferences": {"email": True, "sms": True, "whatsapp": True}
            }
            new_points = current_data.get("total_points", 0) + (updates.get("total_points", firestore.Increment(0))._value if isinstance(updates.get("total_points"), firestore.Increment) else 0)
            new_tier = "Gold" if new_points >= 300 else "Silver" if new_points >= 100 else "Bronze"
            transaction.set(doc_ref, {**updates, "tier": new_tier}, merge=True)
            return get_loyalty_data(user_id)
        transaction = db.transaction()
        return update_with_transaction(transaction, doc_ref, updates)

def save_referral(user_id: str, referred_by: str) -> None:
    """Save referral data and award bonus points."""
    db.collection("loyalty").document(user_id).set({"referred_by": referred_by}, merge=True)
    update_loyalty_data(referred_by, {"total_points": firestore.Increment(50)})

def update_loyalty_settings(restaurant_id: str, points_per_rupee: float, reward_thresholds: str) -> Dict:
    try:
        thresholds = {}
        for item in reward_thresholds.split(","):
            points, reward = item.split(":")
            thresholds[str(int(points))] = reward
        if not thresholds or min([int(k) for k in thresholds.keys()]) < 0:
            raise ValueError
    except ValueError:
        raise ValueError("Reward thresholds must be in format 'points:reward,points:reward'")

    restaurant_ref = db.collection("restaurants").document(restaurant_id)
    restaurant_data = restaurant_ref.get().to_dict()
    current_settings = restaurant_data.get("loyalty_settings", {}).get("current", {})
    current_thresholds = current_settings.get("reward_thresholds", {})
    history = restaurant_data.get("loyalty_settings", {}).get("history", [])

    with db.transaction() as transaction:
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

        new_settings = {"points_per_rupee": points_per_rupee, "reward_thresholds": thresholds}
        transaction.set(restaurant_ref.collection("loyalty_settings").document("current"), new_settings)
        history.append({
            "points_per_rupee": points_per_rupee,
            "reward_thresholds": thresholds,
            "timestamp": datetime.utcnow().isoformat()
        })
        transaction.update(restaurant_ref, {"loyalty_settings.history": history})

    logging.info(f"Updated loyalty settings for {restaurant_id}")
    return {"message": f"Loyalty settings updated for {restaurant_id}"}