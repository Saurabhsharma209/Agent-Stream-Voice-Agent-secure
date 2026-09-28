"""
Google Calendar client — checks real availability and books real meetings on
the Enterprise Admin's connected Google Calendar (OAuth, same connection
already used for Gmail sending — see core/gmail_client.py for the shared
design rationale: one connection per enterprise, reused by every agent).
"""

import logging
import asyncio
import datetime
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import Config

logger = logging.getLogger(__name__)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class CalendarClient:
    """Checks availability and creates events on a connected Google Calendar."""

    @staticmethod
    def is_configured() -> bool:
        return bool(Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET)

    @classmethod
    async def get_refresh_token_for_enterprise(cls, enterprise_id: str) -> Optional[str]:
        """Same oauthtokens lookup as GmailOAuthClient — one shared connection."""
        try:
            from core.mongo_manager import mongo_db
            if mongo_db.client is None:
                return None
            db = mongo_db.client.get_default_database()
            doc = await db['oauthtokens'].find_one({"agent_id": enterprise_id, "provider": "google"})
            return doc.get("refreshToken") if doc else None
        except Exception as e:
            logger.error(f"❌ Failed to load Calendar OAuth token for enterprise '{enterprise_id}': {e}")
            return None

    @classmethod
    async def schedule_meeting(
        cls,
        enterprise_id: str,
        attendee_email: str,
        meeting_date: str,
        meeting_time: str,
        duration_minutes: int = 30,
        summary: str = "Meeting with Chauwk",
        description: str = "",
    ) -> dict:
        """
        Books a real Calendar event if the requested slot is free.

        meeting_date: "YYYY-MM-DD"
        meeting_time: "HH:MM" in 24-hour format, interpreted as IST.

        Returns {"success": bool, "reason": str, "event_link": str|None}
        """
        if not cls.is_configured():
            return {"success": False, "reason": "Calendar integration is not configured.", "event_link": None}

        refresh_token = await cls.get_refresh_token_for_enterprise(enterprise_id)
        if not refresh_token:
            return {"success": False, "reason": "No connected Google Calendar found for this enterprise.", "event_link": None}

        try:
            start_dt = datetime.datetime.strptime(f"{meeting_date} {meeting_time}", "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        except ValueError:
            return {"success": False, "reason": "Invalid date/time format.", "event_link": None}

        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)

        return await asyncio.to_thread(
            cls._schedule_sync, refresh_token, attendee_email, start_dt, end_dt, summary, description
        )

    @classmethod
    def _schedule_sync(
        cls,
        refresh_token: str,
        attendee_email: str,
        start_dt: datetime.datetime,
        end_dt: datetime.datetime,
        summary: str,
        description: str,
    ) -> dict:
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri=Config.GOOGLE_TOKEN_URI,
                client_id=Config.GOOGLE_CLIENT_ID,
                client_secret=Config.GOOGLE_CLIENT_SECRET,
                scopes=["https://www.googleapis.com/auth/calendar"],
            )
            creds.refresh(Request())
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)

            # 1) Check real availability on the connected calendar.
            existing = service.events().list(
                calendarId="primary",
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True,
            ).execute()
            if existing.get("items"):
                busy_with = existing["items"][0].get("summary", "another event")
                return {
                    "success": False,
                    "reason": f"That time is already booked ({busy_with}). Please suggest a different time.",
                    "event_link": None,
                }

            # 2) Slot is free — create the event and invite the caller.
            event_body = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
                "attendees": [{"email": attendee_email}],
            }
            created = service.events().insert(
                calendarId="primary", body=event_body, sendUpdates="all"
            ).execute()

            logger.info(f"✅ Calendar event created: {created.get('htmlLink')}")
            return {"success": True, "reason": "Booked successfully.", "event_link": created.get("htmlLink")}

        except Exception as e:
            logger.error(f"❌ Calendar booking failed: {e}", exc_info=True)
            return {"success": False, "reason": f"Booking failed: {e}", "event_link": None}
