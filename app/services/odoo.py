import requests
from app.config import config
import logging

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
                    logging.error(f"Failed to authenticate with Odoo: {e}")
                    cls._instance = None
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

    def create_whatsapp_composer(self, partner_id, template_id, phone_number, variables):
        if not self.authenticated:
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
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("result", [None])[0]

    def send_whatsapp_message(self, composer_id, partner_id, phone_number):
        if not self.authenticated:
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
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return True

    def create_sms_composer(self, partner_id, phone_number, body):
        if not self.authenticated:
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
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return response.json().get("result", [None])[0]

    def send_sms_message(self, composer_id, partner_id, phone_number):
        if not self.authenticated:
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
        response = self.session.post(url, json=payload)
        response.raise_for_status()
        return True