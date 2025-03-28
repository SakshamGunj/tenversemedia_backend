from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from app.routes.auth import get_current_user
from app.routes.reward import router as reward_router
from app.routes.loyalty import router as loyalty_router
from app.routes.messaging import router as messaging_router
from app.routes.admin import router as admin_router
from app.routes.user import router as user_router
from app.config import config
import logging
import uvicorn


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="Restro Hub API",
    description="API for Spin the Wheel loyalty and engagement system",
    version="1.0.0",
    openapi_tags=[
        {"name": "Public", "description": "Public endpoints"},
        {"name": "User", "description": "Authenticated user endpoints"},
        {"name": "Admin", "description": "Admin-only endpoints"}
    ]
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Templates
templates = Jinja2Templates(directory="templates")

# Include routers
app.include_router(reward_router, tags=["User"])
app.include_router(loyalty_router, tags=["User"])
app.include_router(messaging_router, tags=["User"])
app.include_router(user_router, tags=["User"])
app.include_router(admin_router, tags=["Admin"])

# Health check
@app.get("/health", tags=["Public"])
async def health_check():
    logger.info("Health check requested")
    return {"status": "healthy"}

# Test endpoint
@app.get("/test", tags=["Public"])
async def test_endpoint():
    logger.info("Test endpoint accessed")
    return {"message": "Server is running"}



if __name__ == "__main__":

    uvicorn.run(app, host="0.0.0.0", port=8000)