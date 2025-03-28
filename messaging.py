import json
import requests
from requests.exceptions import ConnectionError
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from app.config import config

class OdooSession:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OdooSession, cls).__new__(cls)
            cls._instance.session = requests.Session()
            cls._instance.authenticated = False
            if all([config.ODOO_BASE_URL, config.ODOO_DB, config.ODOO_USERNAME, config.ODOO_PASSWORD]):
                try:
                    cls._instance._authenticate()
                except Exception as e:
                    print(f"Failed to authenticate with Odoo: {e}")
                    cls._instance = None  # Reset instance if authentication fails
        return cls._instance

    def _authenticate(self):
        login_url = f"{config.ODOO_BASE_URL}/web/session/authenticate"
        payload = {
            "jsonrpc": "2.0",
            "params": {
                "db": config.ODOO_DB,
                "login": config.ODOO_USERNAME,
                "password": config.ODOO_PASSWORD
            }
        }
        response = self.session.post(login_url, json=payload)
        response.raise_for_status()
        result = response.json()
        if "result" in result and "uid" in result["result"]:
            self.authenticated = True
        else:
            raise Exception("Odoo authentication failed")

def create_whatsapp_composer(session, partner_id, template_id, phone_number, variables):
    if not session or not session.authenticated:
        return None
    url = f"{config.ODOO_BASE_URL}/web/dataset/call_kw"
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "model": "mail.compose.message",
            "method": "create",
            "args": [{
                "partner_ids": [partner_id],
                "template_id": template_id,
                "phone_number": phone_number,
                "variables": variables
            }],
            "kwargs": {}
        }
    }
    response = session.session.post(url, json=payload)
    response.raise_for_status()
    result = response.json()
    return result["result"][0] if "result" in result else None

def send_whatsapp_message(session, composer_id, partner_id, phone_number):
    if not session or not session.authenticated:
        return False
    url = f"{config.ODOO_BASE_URL}/web/dataset/call_kw"
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "model": "mail.compose.message",
            "method": "send_whatsapp",
            "args": [composer_id, partner_id, phone_number],
            "kwargs": {}
        }
    }
    response = session.session.post(url, json=payload)
    response.raise_for_status()
    return True

def create_sms_composer(session, partner_id, phone_number, body):
    if not session or not session.authenticated:
        return None
    url = f"{config.ODOO_BASE_URL}/web/dataset/call_kw"
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "model": "sms.compose",
            "method": "create",
            "args": [{
                "partner_ids": [partner_id],
                "number": phone_number,
                "body": body
            }],
            "kwargs": {}
        }
    }
    response = session.session.post(url, json=payload)
    response.raise_for_status()
    result = response.json()
    return result["result"][0] if "result" in result else None

def send_sms_message(session, composer_id, partner_id, phone_number):
    if not session or not session.authenticated:
        return False
    url = f"{config.ODOO_BASE_URL}/web/dataset/call_kw"
    payload = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": {
            "model": "sms.compose",
            "method": "send_sms",
            "args": [composer_id, partner_id, phone_number],
            "kwargs": {}
        }
    }
    response = session.session.post(url, json=payload)
    response.raise_for_status()
    return True

def send_twilio_sms(to_number, body):
    client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)
    message = client.messages.create(
        body=body,
        from_=config.TWILIO_PHONE_NUMBER,
        to=to_number
    )
    return message.sid