import feedparser
from datetime import datetime, timedelta
import pytz
from difflib import SequenceMatcher
from typing import List, Dict
import html
import os

class NewsFilter:
    def __init__(self):
        # Define source tiers for more nuanced scoring
        self.source_tiers = {
            'tier1': {  # Original reporting, highest authority
                'BBC', 'Reuters', 'Associated Press', 'NPR',
                'Wall Street Journal', 'New York Times'
            },
            'tier2': {  # Respected specialist sources
                'CPO Magazine', 'The Register', 'Bleeping Computer',
                'Dark Reading', 'SecurityWeek'
            },
            'tier3': {  # Major news outlets
                'Guardian', 'Independent', 'Bloomberg', 'CNBC',
                'Washington Post'
            }
        }

        # Keywords indicating original vs derivative coverage
        self.original_indicators = {
            'confirms', 'reveals', 'reports', 'announces',
            'according to sources', 'investigation shows'
        }
        self.derivative_indicators = {
            'previously reported', 'as reported by',
            'according to reports', 'reports suggest'
        }

    def extract_source(self, entry: Dict) -> str:
        """Extract clean source name from entry."""
        if 'source' in entry and isinstance(entry['source'], dict):
            return entry['source'].get('title', '').replace('.com', '')
        return ''

    def calculate_source_score(self, source: str) -> float:
        """Calculate score based on source credibility."""
        score = 0
        source = source.lower()

        # Score based on source tier
        if any(s.lower() in source for s in self.source_tiers['tier1']):
            score += 40
        elif any(s.lower() in source for s in self.source_tiers['tier2']):
            score += 30
        elif any(s.lower() in source for s in self.source_tiers['tier3']):
            score += 20

        return score

    def calculate_content_score(self, entry: Dict) -> float:
        """Analyze content quality and originality."""
        score = 0
        title = entry.get('title', '').lower()
        summary = entry.get('summary', '').lower()

        # Check for indicators of original reporting
        if any(indicator in title.lower() or indicator in summary.lower()
               for indicator in self.original_indicators):
            score += 15

        # Penalize obvious aggregation/derivative content
        if any(indicator in title.lower() or indicator in summary.lower()
               for indicator in self.derivative_indicators):
            score -= 10

        return score

    def calculate_entry_score(self, entry: Dict) -> float:
        """Calculate overall quality score for an entry."""
        score = 0.0

        # Get source score
        source = self.extract_source(entry)
        score += self.calculate_source_score(source)

        # Get content score
        score += self.calculate_content_score(entry)

        # Score based on timing
        if 'published_parsed' in entry:
            pub_time = datetime(*entry['published_parsed'][:6])
            time_delta = (datetime.now() - pub_time).total_seconds() / 3600
            time_score = max(24 - time_delta, 0)  # More recent is better
            score += time_score

        return score

    def are_entries_similar(self, entry1: Dict, entry2: Dict) -> bool:
        """Determine if two entries cover the same story."""
        # Extract titles without source info
        title1 = entry1.get('title', '').split(' - ')[0].lower()
        title2 = entry2.get('title', '').split(' - ')[0].lower()

        # Compare core story content
        similarity = SequenceMatcher(None, title1, title2).ratio()

        # Use a stricter similarity threshold for important stories
        threshold = 0.6
        if any(keyword in title1.lower() for keyword in ['breach', 'hack', 'attack']):
            threshold = 0.7  # Require higher similarity for security incidents

        return similarity > threshold

    def filter_entries(self, entries: List[Dict]) -> List[Dict]:
        """Filter feed entries preserving only the best coverage."""
        # Score all entries
        scored_entries = [(entry, self.calculate_entry_score(entry))
                          for entry in entries]

        # Sort by score
        scored_entries.sort(key=lambda x: x[1], reverse=True)

        # Filter similar stories, keeping highest scored version
        filtered_entries = []
        for entry, score in scored_entries:
            if not any(self.are_entries_similar(entry, existing)
                       for existing in filtered_entries):
                filtered_entries.append(entry)

        return filtered_entries


def filter_news_feed(entries: List[Dict]) -> List[Dict]:
    """Main function to filter news feed entries."""
    news_filter = NewsFilter()
    return news_filter.filter_entries(entries)


def convert_entry_published_gmt_to_est(entry_published):
    """Convert GMT timestamp to EST format."""
    gmt = pytz.timezone('GMT')
    eastern = pytz.timezone('US/Eastern')
    date = datetime.strptime(entry_published, '%a, %d %b %Y %H:%M:%S GMT')
    dategmt = gmt.localize(date)
    dateeastern = dategmt.astimezone(eastern)
    fmt = "%Y-%m-%d %H:%M EST"
    dateeastern_formatted = dateeastern.strftime(fmt)
    return dateeastern_formatted


def filter_headline(headline):
    """Filter headlines based on security-related keywords."""
    keywords = [
        "attack", "incident", "breach", "compromise", "leak", "hack",
        "infiltration", "exfiltration", "cyber attack", "ransomware",
        "malware", "phishing", "spear phishing", "denial of service",
        "DoS", "DDoS", "SQL injection", "cross-site scripting", "XSS",
        "brute force attack", "zero-day", "APT", "trojan", "botnet",
        "worm", "spyware", "rootkit", "data breach", "data leak",
        "personal information compromise", "data theft", "information disclosure",
        "unauthorized access", "credential theft", "database leak", "PII",
        "Emotet", "WannaCry", "NotPetya", "Ryuk", "Maze", "Conti",
        "LockBit", "TrickBot", "REvil", "DarkSide", "exploit",
        "vulnerability", "CVE", "security hole", "patch", "unpatched system",
        "vulnerability exploited", "buffer overflow", "privilege escalation",
        "social engineering", "scam", "identity theft", "fraud", "fake email",
        "vishing", "smishing", "supply chain attack", "critical infrastructure attack",
        "industrial control system", "SCADA attack", "cyber extortion",
        "blackmail", "encryption", "decryptor key", "ransom payment",
        "darknet", "dark web", "cybersecurity incident", "cyber event",
        "threat actor", "malicious actor", "state-sponsored attack",
        "outage", "shutdown", "service disruption", "financial loss",
        "reputational damage", "operational impact", "stolen credentials"
    ]
    return any(keyword.lower() in str(headline).lower() for keyword in keywords)


def generate_html_page(entries: List[Dict], output_dir: str = 'dist'):
    """
    Generate a complete HTML page with the cyber events list.
    Features a refined yellow-themed design with subtle gradients and professional styling.
    Uses doubled curly braces {{}} for CSS rules to avoid conflicts with Python's string formatting.
    """
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    html_template = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>This Week in Cyber</title>
        <style>
            :root {{
                /* Refined color palette with more subtle yellows */
                --primary-yellow: #F5B041;
                --light-yellow: #FEF9E7;
                --dark-yellow: #9C640C;
                --accent-yellow: #FCF3CF;
                --background: #FFFFFF;
                --text-dark: #2C3E50;
                --text-light: #666666;
                --border-light: #E8E8E8;
            }}

            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
                line-height: 1.6;
                max-width: 1800px;
                margin: 0 auto;
                padding: 20px;
                background: var(--background);
                color: var(--text-dark);
            }}

            .container {{
                background: white;
                padding: 30px;
                border-radius: 12px;
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.05);
            }}

            .header {{
                margin-bottom: 30px;
                padding: 25px 30px;
                position: relative;
                background: white;
                border-bottom: 3px solid var(--primary-yellow);
            }}

            .header::after {{
                content: '';
                position: absolute;
                bottom: -3px;
                left: 0;
                right: 0;
                height: 3px;
                background: linear-gradient(to right, var(--primary-yellow), var(--light-yellow));
            }}

            .header h1 {{
                color: var(--text-dark);
                margin: 0;
                font-size: 2.5em;
                font-weight: 700;
                letter-spacing: -0.5px;
            }}

            .event-list {{
                list-style: none;
                padding: 0;
            }}

            .event-item {{
                display: flex;
                align-items: center;
                gap: 24px;
                margin-bottom: 10px;
                padding: 14px 20px;
                border-bottom: 1px solid var(--border-light);
                transition: all 0.2s ease;
                border-radius: 6px;
            }}

            .event-item:hover {{
                background-color: var(--light-yellow);
                transform: translateX(3px);
            }}

            .event-title {{
                color: var(--text-dark);
                text-decoration: none;
                font-weight: 500;
                display: block;
                flex: 1 1 auto;
                min-width: 0;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                font-size: 1.1em;
            }}

            .event-title:hover {{
                color: var(--primary-yellow);
            }}

            .event-meta {{
                font-size: 0.9em;
                color: var(--text-light);
                flex: 0 0 auto;
                white-space: nowrap;
                padding-left: 12px;
                border-left: 2px solid var(--primary-yellow);
            }}

            .last-updated {{
                color: var(--text-light);
                font-size: 0.9em;
                margin-top: 15px;
                padding: 8px 12px;
                border-radius: 4px;
                display: inline-block;
                background: var(--light-yellow);
                border: 1px solid var(--primary-yellow);
            }}

            @media (max-width: 768px) {{
                body {{
                    padding: 10px;
                }}
                .container {{
                    padding: 15px;
                }}
                .header {{
                    padding: 20px;
                }}
                .header h1 {{
                    font-size: 2em;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>This Week in Cyber</h1>
                <p class="last-updated">Last updated: {update_time}</p>
            </div>
            <ul class="event-list">
                {events_html}
            </ul>
        </div>
    </body>
    </html>
    """

    events_html = ""
    for entry in entries:
        published_time = convert_entry_published_gmt_to_est(entry.published)
        events_html += f"""
            <li class="event-item">
                <a href="{entry.link}" class="event-title" title="{html.escape(entry.title)}" target="_blank">
                    {entry.title}
                </a>
                <div class="event-meta">
                    {published_time}
                </div>
            </li>
        """

    complete_html = html_template.format(
        update_time=datetime.now().strftime("%Y-%m-%d %H:%M %Z"),
        events_html=events_html
    )

    # Write the generated HTML to index.html in the output directory
    with open(os.path.join(output_dir, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(complete_html)


def main():
    # Feed fetching and filtering
    search = "Cyber"
    rss_url = f"https://news.google.com/news/feeds?q={search}&output=rss"

    now = datetime.now()
    feed = feedparser.parse(rss_url)

    recent_entries = []
    number_of_days = 7

    for entry in feed.entries:
        if hasattr(entry, 'published_parsed'):
            published_time = datetime(*entry.published_parsed[:6])
            if published_time > now - timedelta(days=number_of_days):
                if filter_headline(entry):
                    recent_entries.append(entry)

    # Apply comprehensive filtering
    recent_entries = filter_news_feed(recent_entries)
    recent_entries = sorted(recent_entries,
                            key=lambda entry: entry.published_parsed,
                            reverse=True)

    # Generate the static site
    generate_html_page(recent_entries)


if __name__ == "__main__":
    main()