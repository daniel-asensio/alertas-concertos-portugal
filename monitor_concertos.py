import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
TOKEN_FILE = ROOT / "telegram_token.txt"
CHAT_FILE = ROOT / "telegram_chat_id.txt"
SEEN_FILE = ROOT / "eventos_oficiais_vistos.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ConcertosPortugal/2.0"

# Agendas oficiais. Os padrões identificam os links de eventos de cada página.
SOURCES = [
    {"name": "Casa da Música", "kind": "Porto",
     "urls": [f"https://casadamusica.com/agenda/?paged={n}" for n in range(1, 16)],
     "patterns": [r"casadamusica\.com/event/"]},
    {"name": "Super Bock Arena", "kind": "Porto",
     "urls": ["https://www.superbockarena.pt/agenda/category/concertos-en/"],
     "patterns": [r"superbockarena\.pt/(?:evento|event)/"]},
    {"name": "Hard Club", "kind": "Porto",
     "urls": ["https://www.hardclubporto.com/PT/agenda/"],
     "patterns": [r"hardclubporto\.com/PT/evento/"]},
    {"name": "Coliseu Porto Ageas", "kind": "Porto",
     "urls": ["https://coliseu.pt/agenda"],
     "patterns": [r"coliseu\.pt/(?!agenda(?:/|$)|bilheteira|contactos|espacos|sobre)[^?#]+"]},
    {"name": "Europarque", "kind": "Aveiro",
     "urls": ["https://www.europarque.pt/", "https://www.europarque.pt/agenda-completa/"],
     "patterns": [r"bol\.pt/Comprar/Bilhetes/[^?#]+-europarque/"]},
    {"name": "Teatro Aveirense", "kind": "Aveiro",
     "urls": ["https://www.teatroaveirense.pt/index.php/programacao/"],
     "patterns": [r"teatroaveirense\.pt/index\.php/evento/[^?#]+"]},
    {"name": "Casa da Criatividade", "kind": "São João da Madeira",
     "urls": ["https://www.casadacriatividade.com/", "https://www.casadacriatividade.com/musica"],
     "patterns": [r"casadacriatividade\.com/(?:musica|infantil|teatro|danca|somos-nos)/?$"],
     "watch_page": True},
    {"name": "Everything Is New", "kind": "Promotora",
     "urls": ["https://everythingisnew.pt/"],
     "patterns": [r"everythingisnew\.pt/(?!arquivo|contactos|sobre|politica|$)[^?#]+"]},
    {"name": "PEV Entertainment", "kind": "Promotora Porto",
     "urls": ["https://peventertainment.pt/"],
     "patterns": [r"peventertainment\.pt/(?!contactos|sobre-nos|servicos|$)[^?#]+"]},
    {"name": "House of Fun", "kind": "Promotora",
     "urls": ["https://houseoffun.pt/"],
     "patterns": [r"houseoffun\.pt/(?!arquivo|sobre|contactos|$)[^?#]+"]},
    {"name": "Música no Coração", "kind": "Promotora",
     "urls": ["https://musicanocoracao.com/"],
     "patterns": [r"musicanocoracao\.com/(?!contactos|sobre|$)[^?#]+"]},
    {"name": "MEO Arena", "kind": "Grande sala",
     "urls": ["https://arena.meo.pt/agenda/"],
     "patterns": [r"arena\.meo\.pt/agenda/[^?#]+"]},
    {"name": "Coliseu dos Recreios", "kind": "Grande sala",
     "urls": ["https://coliseulisboa.com/o-coliseu/programacao/"],
     "patterns": [r"coliseulisboa\.com/eventos/"]},
    {"name": "BLITZ Agenda", "kind": "Agenda nacional",
     "urls": ["https://rss.impresa.pt/feed/latest/expresso.rss?type=ARTICLE,VIDEO,STREAM,PLAYLIST,EVENT&limit=100&pubsubhub=true"],
     "patterns": [r"expresso\.pt/blitz/(?:agenda|musica/ao-vivo)/\d{4}-"],
     "rss": True},
]

GENERIC_TITLES = {
    "mais info", "saber mais", "detalhes", "comprar", "bilhetes", "info",
    "ver mais", "read more", "agenda", "programação", "programacao",
}


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.href, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.text)))
            self.href, self.text = None, []


def clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip(" -|\t\r\n")


def canonical_url(url):
    parts = urlsplit(url)
    path = re.sub(r"/+", "/", parts.path).rstrip("/") + "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def title_from_url(url):
    slug = urlsplit(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", slug)
    return clean(slug.replace("-", " ").replace("_", " ")).title()


def get_bytes(url):
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-PT,pt;q=0.9"})
    with urlopen(req, timeout=35) as response:
        return response.read()


def extract_source(source):
    found, failures = {}, []
    compiled = [re.compile(p, re.I) for p in source["patterns"]]
    for page_url in source["urls"]:
        try:
            raw = get_bytes(page_url)
            if source.get("rss"):
                root = ET.fromstring(raw)
                candidates = []
                for item in root.findall(".//item"):
                    candidates.append((item.findtext("link") or "", item.findtext("title") or ""))
            else:
                parser = LinkParser()
                parser.feed(raw.decode("utf-8", errors="replace"))
                candidates = parser.links
            page_count = 0
            for href, anchor_text in candidates:
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absolute = canonical_url(urljoin(page_url, href))
                if not any(p.search(absolute) for p in compiled):
                    continue
                title = clean(anchor_text)
                if not title or title.casefold() in GENERIC_TITLES or len(title) < 3:
                    title = title_from_url(absolute)
                if len(title) > 180:
                    title = title[:177] + "..."
                key = hashlib.sha256(absolute.encode("utf-8")).hexdigest()[:24]
                found[key] = {"id": key, "title": title, "link": absolute,
                              "source": source["name"], "kind": source["kind"]}
                page_count += 1
            if page_count == 0 and source.get("watch_page"):
                digest = hashlib.sha256(re.sub(rb"\s+", b" ", raw).strip()).hexdigest()[:24]
                found[digest] = {
                    "id": digest,
                    "title": f"Programação atualizada — {source['name']}",
                    "link": canonical_url(page_url),
                    "source": source["name"],
                    "kind": source["kind"],
                }
                page_count = 1
            if page_count == 0 and "paged=" not in page_url:
                failures.append(f"sem eventos reconhecidos em {page_url}")
        except Exception as exc:
            if "paged=" not in page_url:
                failures.append(f"{type(exc).__name__}: {exc}")
    return list(found.values()), failures


def telegram_api(token, method, data=None):
    endpoint = f"https://api.telegram.org/bot{token}/{method}"
    req = Request(endpoint, data=urlencode(data or {}).encode("utf-8"), headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=35) as response:
        return json.load(response)


def get_chat_id(token):
    environment_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if environment_chat:
        return environment_chat
    if CHAT_FILE.exists():
        return CHAT_FILE.read_text(encoding="utf-8").strip()
    updates = telegram_api(token, "getUpdates").get("result", [])
    chats = [u.get("message", {}).get("chat", {}) for u in updates if u.get("message", {}).get("chat")]
    if not chats:
        raise RuntimeError("Envie /start ao bot e execute novamente.")
    chat_id = str(chats[-1]["id"])
    CHAT_FILE.write_text(chat_id, encoding="utf-8")
    return chat_id


def load_seen():
    if not SEEN_FILE.exists():
        return {}
    try:
        return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_seen(events):
    SEEN_FILE.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")


def split_messages(lines, limit=3800):
    messages, current = [], ""
    for line in lines:
        candidate = f"{current}\n{line}".strip()
        if current and len(candidate) > limit:
            messages.append(current)
            current = line
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def build_lines(events, failures, initial):
    by_source = {}
    for event in events:
        by_source.setdefault(event["source"], []).append(event)
    heading = "🎵 Agenda inicial de concertos" if initial else "🎵 Novos concertos encontrados"
    lines = [heading, f"{len(events)} resultados em {len(by_source)} fontes", ""]
    for source_name in sorted(by_source):
        group = sorted(by_source[source_name], key=lambda e: e["title"].casefold())
        lines.append(f"📍 {source_name} ({len(group)})")
        for event in group:
            lines.append(f"• {event['title']}\n{event['link']}")
        lines.append("")
    if failures:
        lines.extend(["⚠️ Fontes a rever:"] + [f"• {failure}" for failure in failures[:15]])
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet-baseline", action="store_true")
    args = parser.parse_args()

    all_events, failures, counts = [], [], []
    for source in SOURCES:
        events, source_failures = extract_source(source)
        all_events.extend(events)
        failures.extend([f"{source['name']}: {item}" for item in source_failures])
        counts.append((source["name"], len(events)))

    all_events = list({event["id"]: event for event in all_events}.values())
    print(f"Encontrados {len(all_events)} resultados oficiais.")
    for name, count in counts:
        print(f"  {name}: {count}")
    if failures:
        print(f"Fontes com aviso: {len(failures)}")
        for failure in failures:
            print(f"  AVISO: {failure}")
    if args.dry_run:
        return 0

    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token and TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("Falta o token do Telegram.")
    chat_id = get_chat_id(token)
    seen = load_seen()
    initial = not bool(seen)
    new_events = all_events if initial else [e for e in all_events if e["id"] not in seen]
    save_seen({event["id"]: event for event in all_events})

    if initial and args.quiet_baseline:
        print("Agenda inicial guardada sem envio.")
        return 0
    if not new_events:
        print("Sem novidades; nenhuma mensagem enviada.")
        return 0

    messages = split_messages(build_lines(new_events, failures, initial))
    for message in messages:
        telegram_api(token, "sendMessage", {"chat_id": chat_id, "text": message,
                                             "disable_web_page_preview": "true"})
        time.sleep(0.15)
    print(f"Enviados {len(new_events)} resultados em {len(messages)} mensagens.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
