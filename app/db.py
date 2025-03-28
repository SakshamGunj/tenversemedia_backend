from firebase_admin import credentials, firestore, initialize_app
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging
import os

# Define the path to your service account key file
SERVICE_ACCOUNT_KEY_PATH = "spinthewheel-e14a6-firebase-adminsdk-fbsvc-f9262a6501.json"

try:
    # Load the service account key directly
    cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
    # Initialize the app with explicit credentials and project ID
    initialize_app(cred, {
        'projectId': 'spinthewheel-e14a6'  # Your Firebase project ID
    })
    # Initialize Firestore client using Firebase Admin SDK
    db = firestore.client()
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

def get_loyalty_data(user_id: str) -> Dict:
    """Retrieve or initialize loyalty data for a user."""
    doc = db.collection("loyalty").document(user_id).get()
    if doc.exists:
        return doc.to_dict()
    return {
        "total_points": 0,
        "punches": 0,
        "tier": "Bronze",
        "restaurant_points": {},
        "referral_code": f"REF{user_id[:8]}",
        "redemption_history": []
    }

def update_loyalty_data(user_id: str, updates: Dict) -> Dict:
    """Update loyalty data and return the updated state."""
    doc_ref = db.collection("loyalty").document(user_id)

    # Use a transaction to ensure tier is updated based on the final total_points
    @firestore.transactional
    def update_with_transaction(transaction, doc_ref, updates):
        # Read the current state
        snapshot = doc_ref.get(transaction=transaction)
        current_data = snapshot.to_dict() if snapshot.exists else {
            "total_points": 0,
            "punches": 0,
            "tier": "Bronze",
            "restaurant_points": {},
            "referral_code": f"REF{user_id[:8]}",
            "redemption_history": []
        }

        # Prepare updates
        new_updates = updates.copy()

        # Calculate new total_points for tier determination
        if "total_points" in new_updates and isinstance(new_updates["total_points"], firestore.Increment):
            current_points = current_data.get("total_points", 0)
            increment_value = new_updates["total_points"]._value  # Access the internal value
            new_points = current_points + increment_value
            new_updates["tier"] = "Gold" if new_points >= 300 else "Silver" if new_points >= 100 else "Bronze"

        # Apply the update with Increment objects
        transaction.set(doc_ref, new_updates, merge=True)
        return new_points if "total_points" in new_updates else current_data.get("total_points", 0)

    # Run the transaction
    transaction = db.transaction()
    update_with_transaction(transaction, doc_ref, updates)

    # Fetch and return the updated data
    return get_loyalty_data(user_id)

def save_referral(user_id: str, referred_by: str) -> None:
    """Save referral data and award bonus points."""
    db.collection("loyalty").document(user_id).set({"referred_by": referred_by}, merge=True)
    update_loyalty_data(referred_by, {"total_points": firestore.Increment(50)})