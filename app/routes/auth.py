from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from firebase_admin import auth
from fastapi import status
from app.config import config
import logging

logger = logging.getLogger(__name__)

firebase_auth = APIKeyHeader(name="Authorization", auto_error=False)

async def get_current_user(authorization: str = Security(firebase_auth)) -> dict:
    """Verify Firebase authentication token."""
    logger.info("Verifying Firebase token")
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")
    try:
        token = authorization.replace("Bearer ", "")
        decoded_token = auth.verify_id_token(token)
        logger.info(f"User authenticated with UID: {decoded_token['uid']}")
        return decoded_token
    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")

async def is_admin(current_user: dict = Depends(get_current_user)):
    try:
        # Check if the user has an admin custom claim
        decoded_token = auth.verify_id_token(await oauth2_scheme())
        if not decoded_token.get("custom_claims", {}).get("admin", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        logger.info(f"Admin authenticated: {current_user['uid']}")
        return current_user
    except Exception as e:
        logger.error(f"Admin check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )