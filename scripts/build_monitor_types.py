from bs4 import BeautifulSoup
import re

from utils import parse_vue_template, write_to_file


# Override titles for options whose label text in the template is not a clean
# display name (e.g. contains i18n markers, is too terse, or differs from the
# historically shipped docstring).  Entries here win over the auto-extracted
# label; types NOT listed here derive their title from the <option> text.
title_overrides = {
    "http": "HTTP(s)",
    "port": "TCP Port",
    "ping": "Ping",
    "keyword": "HTTP(s) - Keyword",
    "grpc-keyword": "gRPC(s) - Keyword",
    "dns": "DNS",
    "docker": "Docker Container",
    "push": "Push",
    "steam": "Steam Game Server",
    "gamedig": "GameDig",
    "mqtt": "MQTT",
    "sqlserver": "Microsoft SQL Server",
    "postgres": "PostgreSQL",
    "mysql": "MySQL/MariaDB",
    "mongodb": "MongoDB",
    "radius": "Radius",
    "redis": "Redis",
    "group": "Group",
    "json-query": "HTTP(s) - Json Query",
    "real-browser": "HTTP(s) - Browser Engine (Chrome/Chromium)",
    "kafka-producer": "Kafka Producer",
    "tailscale-ping": "Tailscale Ping",
}


def _extract_label(option_element):
    """Extract a clean title from an <option> element's text content.

    Handles Vue i18n markers like {{ $t("...") }} by extracting the string
    argument, and strips leading/trailing whitespace.
    """
    text = option_element.get_text(strip=True)
    # Strip Vue i18n wrapper: {{ $t("Label") }} -> Label
    match = re.match(r'\{\{\s*\$t\(["\'](.+?)["\']\)\s*\}\}', text)
    if match:
        return match.group(1)
    # Strip bare mustache interpolation: {{ "Label" }} -> Label
    match = re.match(r'\{\{\s*["\'](.+?)["\']\s*\}\}', text)
    if match:
        return match.group(1)
    return text if text else None


def parse_monitor_types():
    content = parse_vue_template("uptime-kuma/src/pages/EditMonitor.vue")

    soup = BeautifulSoup(content, "html.parser")
    select = soup.find("select", id="type")
    options = select.find_all("option")

    types = {}
    for o in options:
        type_ = o.attrs["value"]
        # Override wins; otherwise extract from the template label.
        title = title_overrides.get(type_) or _extract_label(o)
        if not title:
            # Last resort: derive from the value itself.
            title = type_.replace("-", " ").title()
        types[type_] = {
            "value": type_,
            "title": title,
        }
    return types


monitor_types = parse_monitor_types()

write_to_file(
    "monitor_type.py.j2", "./../uptime_kuma_api/monitor_type.py",
    monitor_types=monitor_types
)
