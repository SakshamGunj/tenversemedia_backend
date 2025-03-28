from firebase_admin import credentials, firestore, initialize_app
from app.config import config
import logging

cred = credentials.Certificate(config.FIREBASE_CREDENTIALS)
initialize_app(cred)
db = firestore.client()
logging.info("Firebase service initialized")