"""Versioned email rendering from saved, redacted content. No provider calls."""
import re
from html import escape
from urllib.parse import urlencode
from . import config
from .imports import redact

VERSION = "tracedesk-email-v1"
SUBJECTS = {
    "verify": "Verify your TraceDesk email", "reset": "Reset your TraceDesk password",
    "password_changed": "Your TraceDesk password was changed",
    "incident_alert": "TraceDesk: incident detected", "incident_result": "TraceDesk: assessment ready",
}


def concise(value, limit=650):
    # Never include multiline raw logs/stack frames in an email.
    cleaned = redact(str(value or ""))[0].splitlines()[0:1]
    value = " ".join(cleaned)
    return value if len(value) <= limit else value[:limit-3].rsplit(' ', 1)[0] + '...'


def shell(title, sections, action, url, *, incident=False, citations=()):
    content, plain = [], [title]
    for heading, body in sections:
        body = str(body)
        content.append('<tr><td style="padding:12px 24px;word-break:break-word;overflow-wrap:anywhere">'
            + (f'<h2 style="margin:0 0 8px;font-size:15px;color:#F6FF81">{escape(heading)}</h2>' if heading else "")
            + f'<p style="margin:0;line-height:1.6;color:#D6E3E4">{escape(body).replace(chr(10), "<br>")}</p></td></tr>')
        plain.append((heading + "\n" if heading else "") + body)
    for finding, links in citations:
        content.append('<tr><td style="padding:8px 24px;word-break:break-word">'
            + escape(finding) + '<br>' + ' '.join(f'<a style="color:#31CB53" href="{escape(link, quote=True)}">Source {i+1}</a>' for i, link in enumerate(links)) + '</td></tr>')
        plain.append(finding + "\n" + "\n".join(links))
    footer = ("You received this email because you opted in to incident notifications for your Sentry connection."
              if incident else "TraceDesk account security. If you did not request this action, follow the guidance above.")
    settings = config.PUBLIC_URL + "/connections" if incident else config.PUBLIC_URL + "/forgot"
    settings_label = "Notification settings" if incident else "Account recovery"
    html = ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="dark light"><title>' + escape(title) + '</title></head>'
        '<body bgcolor="#000000" style="margin:0;padding:0;background-color:#000000;color:#D6E3E4;font-family:Arial,Helvetica,sans-serif">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" bgcolor="#000000"><tr><td align="center" style="padding:24px 8px">'
        '<!--[if mso]><table role="presentation" width="600"><tr><td><![endif]-->'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" bgcolor="#000000" style="max-width:600px;border:1px solid #D6E3E2;color:#D6E3E4">'
        '<tr><td style="padding:24px;font-size:24px;font-weight:bold;color:#D6E3E4">TraceDesk</td></tr>'
        '<tr><td style="padding:0 24px 12px"><h1 style="font-size:26px;line-height:1.25;margin:0;color:#D6E3E4">' + escape(title) + '</h1></td></tr>'
        + ''.join(content) + '<tr><td style="padding:24px"><table role="presentation" cellspacing="0" cellpadding="0"><tr><td bgcolor="#31CB53" style="border-radius:4px">'
        + f'<a href="{escape(url, quote=True)}" style="display:inline-block;padding:14px 20px;font-weight:bold;color:#000000;text-decoration:none">{escape(action)}</a>'
        + '</td></tr></table></td></tr><tr><td style="padding:20px 24px;border-top:1px solid #D6E3E2;font-size:12px;line-height:1.6;color:#D6E3E2">'
        + escape(footer) + f'<br><a style="color:#D6E3E2" href="{escape(settings, quote=True)}">{settings_label}</a>'
        + '</td></tr></table><!--[if mso]></td></tr></table><![endif]--></td></tr></table></body></html>')
    return {"subject": title, "html": html, "text": "\n\n".join(plain + [action + ": " + url, footer, settings_label + ": " + settings])}


def authentication(kind, text):
    links = re.findall(r'https?://[^\s]+', text)
    url = links[0] if links else config.PUBLIC_URL + "/forgot"
    action = {"verify": "Confirm email", "reset": "Reset password", "password_changed": "Open TraceDesk"}[kind]
    result = shell(SUBJECTS[kind], [("", text)], action, url)
    # Keep the established plain-text instructions and fragment token exactly.
    result["text"] = text
    return result


def incident(episode, attempt, report=None, *, alert=False, coverage=""):
    link = config.PUBLIC_URL + "/incidents/" + episode["id"]
    base = [("Project", concise(episode["project_name"], 120)), ("Observed problem", concise(episode["title"], 240)),
            ("Notification received (UTC)" if episode.get('event_time_known') is False else "Event time (UTC)", episode["triggered_at"])]
    if alert:
        return shell(SUBJECTS["incident_alert"], base + [("Status", episode["state"].capitalize() + ". " + (episode.get("error") or "Evidence collection begins after a 60-second telemetry delay. No cause has been assessed yet."))], "Open incident", link, incident=True)
    if report:
        supported = report.get("assessment") == "supported"
        title = SUBJECTS["incident_result"] if supported else "TraceDesk: assessment inconclusive"
        sections = base + [("Assessed root cause and observed impact" if supported else "Cause not established", concise(report.get("summary")))]
        sections += [("Next checks", "\n".join(concise(x, 300) for x in report.get("recommendations", [])[:2])),
                     ("Limitations", "\n".join(concise(x, 350) for x in report.get("limitations", [])[:2]) + ("\n" + concise(coverage, 450) if coverage else ""))]
        citations = []
        for finding in report.get("supporting_evidence", [])[:2]:
            links = [config.PUBLIC_URL + "/logs?" + urlencode({"investigation": attempt["investigation_id"], "evidence": source, "correlate": "1"}) for source in finding.get("evidence_ids", [])[:2]]
            citations.append((concise(finding.get("finding"), 350), links))
        return shell(title, sections, "View assessment", config.PUBLIC_URL + "/investigations/" + attempt["investigation_id"], incident=True, citations=citations)
    return shell("TraceDesk: investigation could not finish", base + [("What happened", concise(attempt.get("error") or episode.get("error") or "The investigation did not finish.")),
        ("Retained evidence", "Available observations and earlier attempts remain in your authenticated workspace. " + concise(coverage, 450)),
        ("Next check", "Open the incident and explicitly retry when capacity and access are available. Opening this link does not start analysis.")],
        "Open incident to retry", link, incident=True)
