#!/usr/bin/env python3
"""
SMTP Email Client Service
Provides asynchronous, non-blocking email transmission using standard Python smtplib.
Wraps blocking operations in asyncio.to_thread to maintain active voice/telephony concurrency.
Falls back to logging warning if SMTP credentials are not configured.
"""

import logging
import asyncio
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from config import Config

logger = logging.getLogger(__name__)

class SMTPClient:
    """Production SMTP Email Client with fallback logging"""
    
    @staticmethod
    def is_configured() -> bool:
        """Check if SMTP credentials are fully configured"""
        return bool(Config.SMTP_HOST and Config.SMTP_USER and Config.SMTP_PASSWORD)
        
    @classmethod
    async def send_email(
        cls,
        recipient_email: str,
        subject: str,
        body: str,
        cc_recipient: Optional[str] = None,
        cc_list: Optional[list] = None,
        override: Optional[dict] = None
    ) -> bool:
        """
        Send an email asynchronously in a background thread.

        override: optional per-agent SMTP creds — {host, port, user, password,
        from_name, from_email} — from the agent_email_credentials collection
        (see routes/agent_routes.py's save_email_credentials). When omitted,
        falls back to the global Config.SMTP_* values.
        """
        recipient_email = recipient_email.strip()
        if not recipient_email:
            logger.warning("⚠️ Email skipped: Empty recipient address.")
            return False

        all_cc = [c.strip() for c in (cc_list or []) if c and c.strip()]
        if cc_recipient and cc_recipient.strip():
            all_cc.append(cc_recipient.strip())

        host = (override or {}).get("host") or Config.SMTP_HOST
        port = (override or {}).get("port") or Config.SMTP_PORT or 587
        username = (override or {}).get("user") or Config.SMTP_USER
        password = (override or {}).get("password") or Config.SMTP_PASSWORD

        # If not configured, fall back to mock log reporter
        if not (host and username and password):
            cc_log = f" (CC: {', '.join(all_cc)})" if all_cc else ""
            logger.info(
                f"📧 [MOCK EMAIL CLIENT] To: {recipient_email}{cc_log}\n"
                f"   Subject: {subject}\n"
                f"   Body: {body}\n"
                f"   👉 SMTP details are missing. Mocking delivery."
            )
            return True

        # Run the blocking send operation in a background thread
        return await asyncio.to_thread(
            cls._send_email_sync,
            recipient_email,
            subject,
            body,
            all_cc,
            host,
            port,
            username,
            password,
            (override or {}).get("from_name"),
            (override or {}).get("from_email"),
        )

    @classmethod
    def _send_email_sync(
        cls,
        recipient_email: str,
        subject: str,
        body: str,
        cc_list: list,
        host: str,
        port: int,
        username: str,
        password: str,
        from_name: Optional[str] = None,
        from_email: Optional[str] = None,
    ) -> bool:
        """Synchronous SMTP email transmission helper"""
        try:
            # Create message container
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject

            # Setup sender
            from_name = from_name or Config.SMTP_FROM_NAME or "Chauwk Support"
            from_email = from_email or Config.SMTP_FROM_EMAIL or username
            msg['From'] = f"{from_name} <{from_email}>"

            # Setup recipients
            msg['To'] = recipient_email
            recipients = [recipient_email]

            if cc_list:
                msg['Cc'] = ", ".join(cc_list)
                recipients.extend(cc_list)

            # Record the MIME types of both parts - text/plain and text/html
            text_part = MIMEText(body, 'plain', 'utf-8')
            msg.attach(text_part)

            logger.info(f"📧 Connecting to SMTP host {host}:{port}...")

            # Connect via SSL (port 465) or standard TLS (port 587)
            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=10)
            else:
                server = smtplib.SMTP(host, port, timeout=10)
                server.ehlo()
                server.starttls()  # Upgrade connection to secure TLS
                server.ehlo()

            # Authenticate and send
            server.login(username, password)
            server.sendmail(from_email, recipients, msg.as_string())
            server.quit()

            cc_log = f" (CC: {', '.join(cc_list)})" if cc_list else ""
            logger.info(f"✅ Email successfully delivered to {recipient_email}{cc_log}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to deliver email to {recipient_email}: {e}", exc_info=True)
            return False
