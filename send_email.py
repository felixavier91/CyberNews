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

from curate import check_articles, curate, summary_parts
from main import (CURATED_FILE, clean_title, convert_entry_published_gmt_to_est,
                  get_recent_entries, load_entries)


DISCLAIMER = "Prepared automatically by AI. The code is maintained by Felix Landa (Corporate ERM)."


def build_email(entries, site_url: str, update_time: str):
    rows = ""
    for entry in entries:
        title = html.escape(clean_title(entry))
        link = html.escape(entry.link)
        summary, headline_only = summary_parts(entry)
        border = "" if summary else "border-bottom:1px solid #eee;"
        rows += (
            "<tr>"
            f'<td style="padding:9px 12px 3px 0;{border}">'
            f'<a href="{link}" style="color:#2C3E50;text-decoration:none;font-weight:600;">{title}</a></td>'
            f'<td style="width:135px;padding:9px 0 3px;{border}'
            'white-space:nowrap;text-align:right;vertical-align:top;font-size:12px;color:#888;">'
            f"{convert_entry_published_gmt_to_est(entry.published)}</td>"
            "</tr>"
        )
        if summary:
            note = ' <i>(summary from headline only)</i>' if headline_only else ""
            rows += (
                '<tr><td colspan="2" style="padding:0 0 9px;border-bottom:1px solid #eee;'
                'font-size:14px;line-height:1.5;color:#555;">'
                f'{html.escape(summary)}{note}'
                "</td></tr>"
            )

    html_body = f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#2C3E50;max-width:1000px;margin:auto;">
  <h1 style="margin-bottom:4px;">This Week in <span style="color:#D68910;">Cyber</span></h1>
  <p style="margin:4px 0 8px;font-size:13px;color:#888;">Last updated: {html.escape(update_time)}</p>
  <table style="width:100%;table-layout:fixed;border-collapse:collapse;font-size:15px;">{rows}</table>
  <p style="margin-top:20px;font-size:12px;color:#888;text-align:center;">{html.escape(DISCLAIMER)}
    <a href="{html.escape(site_url)}" style="color:#aaa;text-decoration:none;">Web version</a></p>
</body></html>"""

    text_body = (
        "This Week in Cyber\n"
        + f"Last updated: {update_time}\n\n"
        + "\n\n".join(
            f"{clean_title(e)}\n" + (f"{summary_parts(e)[0]}\n" if summary_parts(e)[0] else "") + e.link
            for e in entries
        )
        + "\n\n" + DISCLAIMER
        + f"\nWeb version: {site_url}"
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
    if os.path.exists(CURATED_FILE):
        # main.py already fetched and curated this run's list for the website
        entries = load_entries()
    else:
        entries = get_recent_entries()
        if entries:
            entries, _ = curate(entries)
            entries = check_articles(entries)
    if not entries:
        # Fail the run so the missed email shows up in GitHub (and its failure notice)
        sys.exit("No entries found; not sending.")

    # Same format as the website's "Last updated" tag
    update_time = datetime.now(pytz.timezone("US/Eastern")).strftime("%m-%d %H:%M")
    html_body, text_body = build_email(entries, site_url, update_time)

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
