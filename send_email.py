"""Email the current "This Week in Cyber" headlines.

Settings come from environment variables:
  SMTP_HOST       SMTP server (default smtp.resend.com)
  SMTP_PORT       SMTP port, STARTTLS (default 587)
  SMTP_USER       SMTP login ("resend" for Resend)
  SMTP_PASSWORD   SMTP key/password (the API key for Resend)
  EMAIL_FROM      Verified sender address shown in the From line
  EMAIL_TO        Comma-separated recipients
  SITE_URL        Public URL of the website
  ONLY_AT_HOUR    Optional. Only send if the current US/Eastern hour matches
                  (lets the workflow run at two UTC times to cover daylight saving)
  DRY_RUN         Optional. If set, write email_preview.html instead of sending
"""
import html
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage

import pytz

from main import clean_title, convert_entry_published_gmt_to_est, get_recent_entries


def build_email(entries, site_url: str):
    rows = ""
    for entry in entries:
        title = html.escape(clean_title(entry))
        rows += (
            "<tr>"
            '<td style="padding:7px 12px 7px 0;border-bottom:1px solid #eee;'
            'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
            f'<a href="{html.escape(entry.link)}" '
            f'style="color:#2C3E50;text-decoration:none;">{title}</a></td>'
            '<td style="width:135px;padding:7px 0;border-bottom:1px solid #eee;'
            'white-space:nowrap;text-align:right;font-size:12px;color:#888;">'
            f"{convert_entry_published_gmt_to_est(entry.published)}</td>"
            "</tr>"
        )

    html_body = f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#2C3E50;max-width:1000px;margin:auto;">
  <h1 style="margin-bottom:16px;">This Week in <span style="color:#D68910;">Cyber</span></h1>
  <table style="width:100%;table-layout:fixed;border-collapse:collapse;font-size:15px;">{rows}</table>
</body></html>"""

    text_body = "This Week in Cyber\n\n" + "\n\n".join(
        f"{clean_title(e)}\n{e.link}" for e in entries
    )
    return html_body, text_body


def main():
    only_hour = os.environ.get("ONLY_AT_HOUR")
    if only_hour:
        now = datetime.now(pytz.timezone("US/Eastern"))
        if now.hour != int(only_hour):
            print(f"Eastern hour is {now.hour}, not {only_hour}; skipping.")
            return

    site_url = os.environ.get("SITE_URL", "")
    entries = get_recent_entries()
    if not entries:
        print("No entries found; not sending.")
        return

    html_body, text_body = build_email(entries, site_url)

    if os.environ.get("DRY_RUN"):
        with open("email_preview.html", "w", encoding="utf-8") as f:
            f.write(html_body)
        print("Wrote email_preview.html")
        return

    user = os.environ["SMTP_USER"]
    recipients = [r.strip() for r in os.environ["EMAIL_TO"].split(",") if r.strip()]

    msg = EmailMessage()
    sent_at = datetime.now(pytz.timezone("US/Eastern"))
    msg["Subject"] = f"This Week in Cyber - {sent_at.strftime('%m-%d %H:%M')} ET"
    msg["From"] = os.environ["EMAIL_FROM"]
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    host = os.environ.get("SMTP_HOST") or "smtp.resend.com"
    port = int(os.environ.get("SMTP_PORT") or "587")
    sent, failed = 0, []
    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, os.environ["SMTP_PASSWORD"])
        # One copy per person, so recipients never see each other's addresses.
        # A failure for one address doesn't stop the others.
        for recipient in recipients:
            try:
                del msg["To"]
                msg["To"] = recipient
                smtp.send_message(msg)
                sent += 1
            except Exception as exc:
                failed.append(recipient)
                print(f"Failed to send to {recipient}: {exc}")
    print(f"Sent to {sent} of {len(recipients)} recipient(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
