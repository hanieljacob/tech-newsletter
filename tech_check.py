#!/usr/bin/env python3
"""
Tech Check newsletter generator.
Can be run directly (CLI) or imported by app.py for the web UI.
"""

import os
import re
import smtplib
import urllib.request
import urllib.error
import json
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
RECIPIENT_EMAIL = "sujathom@gmail.com"
DEFAULT_MODEL = "openai/gpt-oss-120b:free"

RSS_FEEDS = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "Wired": "https://www.wired.com/feed/rss",
    "Reuters Technology": "https://feeds.reuters.com/reuters/technologyNews",
}

DEFAULT_EDITORIAL = """From the list above, select the top 5 articles most relevant to the "Tech Check" section of the TS Weekly newsletter.

IMPORTANT: Before including any article, call check_url to verify its link works. If a link is broken, skip it and pick the next best article.

For each selected article:
- Use the exact URL from the list above.
- Write a concise summary of no more than 30 words — engaging and click-worthy for a tech-savvy professional audience.

Also write a newsletter-style catchy title for the whole digest.

Prioritize:
- Relevance to technology trends, innovation, policy, or business impact.
- Novelty and professional interest.
- Clear implications for enterprise, digital transformation, or emerging tech.

Avoid repeating background information or overly academic language.

Output ONLY valid HTML — no Markdown, no asterisks, no backticks. Use exactly this structure:

<h2>Your Catchy Newsletter Title Here</h2>
<ol>
  <li>
    <strong><a href="ARTICLE_URL">Article Title</a></strong> &mdash; <em>Source Name</em><br>
    30-word summary here.
  </li>
</ol>

Replace the placeholders with real content. Do not wrap in html code fences. Do not use ** for bold."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_url",
            "description": (
                "Verify whether a URL is accessible. Call this for every article link "
                "before including it in the newsletter. If it returns false, skip that article."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full URL to verify"}
                },
                "required": ["url"],
            },
        },
    }
]


def fetch_rss_articles(days: int = 6) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    articles = []

    for source, feed_url in RSS_FEEDS.items():
        try:
            req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            for item in root.findall(".//item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                pub_date_str = (item.findtext("pubDate") or "").strip()
                description = (item.findtext("description") or "").strip()[:300]

                if not title or not link:
                    continue
                try:
                    pub_date = parsedate_to_datetime(pub_date_str)
                    if pub_date.tzinfo is None:
                        pub_date = pub_date.replace(tzinfo=timezone.utc)
                    if pub_date < cutoff:
                        continue
                except Exception:
                    pass

                articles.append({
                    "source": source,
                    "title": title,
                    "link": link,
                    "published": pub_date_str,
                    "description": description,
                })

            count = sum(1 for a in articles if a["source"] == source)
            print(f"[{datetime.now().isoformat()}] {source}: fetched {count} recent articles")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] RSS fetch failed for {source}: {e}")

    return articles


def build_prompt(articles: list[dict], editorial: str = None) -> str:
    today = datetime.now().strftime("%B %d, %Y")
    articles_block = "\n\n".join(
        f"[{i+1}] Source: {a['source']}\n"
        f"    Title: {a['title']}\n"
        f"    URL: {a['link']}\n"
        f"    Published: {a['published']}\n"
        f"    Description: {a['description']}"
        for i, a in enumerate(articles)
    )
    instructions = editorial or DEFAULT_EDITORIAL
    return f"Today is {today}. Below are real articles published in the last 6 days, fetched from RSS feeds.\n\n{articles_block}\n\n{instructions}"


def check_url(url: str) -> bool:
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status < 400
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return True
            if e.code < 400:
                return True
        except Exception:
            pass
    return False


def call_openrouter(prompt: str, model: str = None, api_key: str = None) -> str:
    _model = model or DEFAULT_MODEL
    _key = api_key or OPENROUTER_API_KEY
    messages = [{"role": "user", "content": prompt}]

    for _ in range(30):
        payload = json.dumps({
            "model": _model,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://ts-weekly-newsletter",
                "X-Title": "TS Weekly Tech Check",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))

        message = result["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            return message.get("content", "")

        messages.append(message)
        for call in tool_calls:
            url = json.loads(call["function"]["arguments"]).get("url", "")
            accessible = check_url(url)
            print(f"[{datetime.now().isoformat()}] check_url({url}) -> {accessible}")
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps({"accessible": accessible, "url": url}),
            })

    return messages[-1].get("content", "")


def markdown_to_html(text: str) -> str:
    text = re.sub(r'^```(?:html)?\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text.strip(), flags=re.MULTILINE)
    text = text.strip()
    if re.search(r'<(h[1-6]|ol|ul|li|p|strong|em|a)\b', text):
        return text
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text, flags=re.DOTALL)
    text = re.sub(r'^#{3} (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^#{2} (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^#{1} (.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    lines, out, in_list = text.split('\n'), [], False
    for line in lines:
        m = re.match(r'^\d+\.\s+(.*)', line)
        if m:
            if not in_list:
                out.append('<ol>')
                in_list = True
            out.append(f'  <li>{m.group(1)}</li>')
        else:
            if in_list:
                out.append('</ol>')
                in_list = False
            out.append(line)
    if in_list:
        out.append('</ol>')
    text = '\n'.join(out)
    text = re.sub(r'\n{2,}', '</p><p>', text)
    text = f'<p>{text}</p>'
    return re.sub(r'<p>\s*</p>', '', text)


def strip_broken_links(html: str) -> str:
    def verify(match):
        url, text = match.group(1), match.group(2)
        ok = check_url(url)
        print(f"[{datetime.now().isoformat()}] final check_url({url}) -> {ok}")
        return match.group(0) if ok else f"<strong>{text}</strong>"
    return re.sub(r'<a\s+href="([^"]+)"[^>]*>(.+?)</a>', verify, html, flags=re.DOTALL)


def build_email_html(content: str) -> str:
    date_str = datetime.now().strftime("%B %d, %Y")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: Georgia, serif; max-width: 680px; margin: 0 auto; color: #222; background: #fff; }}
    .header {{ background: #1a1a2e; color: #fff; padding: 24px 32px; }}
    .header h1 {{ margin: 0; font-size: 22px; letter-spacing: 1px; }}
    .header p {{ margin: 6px 0 0; font-size: 13px; color: #aaa; }}
    .body {{ padding: 28px 32px; }}
    .footer {{ padding: 16px 32px; font-size: 12px; color: #999; border-top: 1px solid #eee; }}
    a {{ color: #0066cc; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>TS Weekly — Tech Check</h1>
    <p>Week of {date_str}</p>
  </div>
  <div class="body">{content}</div>
  <div class="footer">
    You're receiving this because you subscribed to TS Weekly. Generated automatically every Sunday.
  </div>
</body>
</html>"""


def send_email(subject: str, html_body: str, smtp_user: str = None, smtp_password: str = None, recipient: str = None) -> None:
    _user = smtp_user or SMTP_USER
    _pass = smtp_password or SMTP_PASSWORD
    _to = recipient or RECIPIENT_EMAIL
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = _user
    msg["To"] = _to
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(_user, _pass)
        server.sendmail(_user, _to, msg.as_string())


def run_newsletter(model: str = None, editorial: str = None, recipient: str = None) -> None:
    """Entry point for both CLI and web UI."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP_USER or SMTP_PASSWORD is not set")

    print(f"[{datetime.now().isoformat()}] Fetching RSS articles from the last 6 days...")
    articles = fetch_rss_articles(days=6)
    if not articles:
        raise RuntimeError("No articles fetched from RSS feeds")

    print(f"[{datetime.now().isoformat()}] {len(articles)} articles fetched. Calling LLM ({model or DEFAULT_MODEL})...")
    content = call_openrouter(build_prompt(articles, editorial), model=model, api_key=OPENROUTER_API_KEY)
    content = markdown_to_html(content)

    print(f"[{datetime.now().isoformat()}] Running final link verification...")
    content = strip_broken_links(content)

    subject = f"TS Weekly — Tech Check | {datetime.now().strftime('%b %d, %Y')}"
    html = build_email_html(content)

    print(f"[{datetime.now().isoformat()}] Sending email to {recipient or RECIPIENT_EMAIL}...")
    send_email(subject, html, recipient=recipient)
    print(f"[{datetime.now().isoformat()}] Done.")


def main():
    run_newsletter()


if __name__ == "__main__":
    main()
