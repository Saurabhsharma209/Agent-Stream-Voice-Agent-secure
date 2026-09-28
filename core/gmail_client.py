"""
Gmail API email client — sends email as an Enterprise Admin's own connected
Gmail account via OAuth (no App Password stored anywhere).

Design: one Gmail connection per enterprise, shared across every agent that
enterprise owns. The connection itself happens on ai-webhooks.chauwk.com's
existing /auth/google/{id} flow (the same one already used for Calendar);
we just point the frontend at it with the enterprise's own id instead of an
agent id, so the resulting refresh token in the shared `oauthtokens` Mongo
collection is naturally reusable across all of that enterprise's agents.

Requires the OAuth app's Calendar-connect flow to also request the
`gmail.send` scope — a token issued for `calendar` alone cannot send mail
(Google enforces scopes per-token, not per-account).
"""

import logging
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import Config

logger = logging.getLogger(__name__)


class GmailOAuthClient:
    """Sends email via the Gmail API using a stored per-enterprise OAuth refresh token."""

    @staticmethod
    def is_configured() -> bool:
        return bool(Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET)

    @classmethod
    async def get_refresh_token_for_enterprise(cls, enterprise_id: str) -> Optional[str]:
        """Look up the Gmail refresh token connected for this enterprise.

        Reads the same `oauthtokens` collection ai-webhooks.chauwk.com's
        Calendar OAuth flow already writes to — keyed by whatever id was
        passed as the OAuth `state` param when the admin clicked "Connect
        Gmail" (here, the enterprise id, not an agent id).
        """
        try:
            from core.mongo_manager import mongo_db
            if mongo_db.client is None:
                return None
            db = mongo_db.client.get_default_database()
            doc = await db['oauthtokens'].find_one({"agent_id": enterprise_id, "provider": "google"})
            return doc.get("refreshToken") if doc else None
        except Exception as e:
            logger.error(f"❌ Failed to load Gmail OAuth token for enterprise '{enterprise_id}': {e}")
            return None

    @classmethod
    async def send_email(
        cls,
        enterprise_id: str,
        sender_email: str,
        sender_name: str,
        recipient_email: str,
        subject: str,
        body: str,
        cc_list: Optional[list] = None,
    ) -> bool:
        """Send an email as the enterprise admin's connected Gmail account."""
        if not cls.is_configured():
            logger.warning("⚠️ Gmail OAuth skipped: GOOGLE_CLIENT_ID/SECRET not configured in .env.")
            return False

        refresh_token = await cls.get_refresh_token_for_enterprise(enterprise_id)
        if not refresh_token:
            logger.warning(f"⚠️ No connected Gmail account found for enterprise '{enterprise_id}'.")
            return False

        import asyncio
        return await asyncio.to_thread(
            cls._send_sync,
            refresh_token,
            sender_email,
            sender_name,
            recipient_email,
            subject,
            body,
            cc_list or [],
        )

    @classmethod
    def _send_sync(
        cls,
        refresh_token: str,
        sender_email: str,
        sender_name: str,
        recipient_email: str,
        subject: str,
        body: str,
        cc_list: list,
    ) -> bool:
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri=Config.GOOGLE_TOKEN_URI,
                client_id=Config.GOOGLE_CLIENT_ID,
                client_secret=Config.GOOGLE_CLIENT_SECRET,
                scopes=["https://www.googleapis.com/auth/gmail.send"],
            )
            creds.refresh(Request())  # exchange refresh_token -> a fresh access_token

            service = build("gmail", "v1", credentials=creds, cache_discovery=False)

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{sender_name} <{sender_email}>" if sender_name else sender_email
            msg["To"] = recipient_email
            if cc_list:
                msg["Cc"] = ", ".join(cc_list)
            msg.attach(MIMEText(body, "plain", "utf-8"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            service.users().messages().send(userId="me", body={"raw": raw}).execute()

            cc_log = f" (CC: {', '.join(cc_list)})" if cc_list else ""
            logger.info(f"✅ Gmail API email sent to {recipient_email}{cc_log} as {sender_email}")
            return True

        except Exception as e:
            logger.error(f"❌ Gmail API send failed for {recipient_email}: {e}", exc_info=True)
            return False
