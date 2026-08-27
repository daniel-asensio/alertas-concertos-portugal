import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
TOKEN_FILE = ROOT / "telegram_token.txt"
CHAT_FILE = ROOT / "telegram_chat_id.txt"
SEEN_FILE = ROOT / "eventos_oficiais_vistos.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ConcertosPortugal/2.0"

# Agendas oficiais e agregadores de confirmação. Os padrões identificam os
# links de eventos de cada página.
SOURCES = [
    {"name": "Casa da Música", "kind": "Porto",
     "urls": [f"https://casadamusica.com/agenda/?paged={n}" for n in range(1, 16)],
     "patterns": [r"casadamusica\.com/event/"]},
    {"name": "Super Bock Arena", "kind": "Porto",
     "urls": ["https://www.superbockarena.pt/agenda/"],
     "patterns": [r"superbockarena\.pt/(?:evento|event)/"]},
    {"name": "Hard Club", "kind": "Porto",
     "urls": ["https://www.hardclubporto.com/PT/agenda/"],
     "patterns": [r"hardclubporto\.com/PT/evento/"]},
    {"name": "Coliseu Porto Ageas", "kind": "Porto",
     "urls": ["https://coliseu.pt/agenda"],
     "patterns": [r"coliseu\.pt/(?!agenda(?:/|$)|bilheteira|contactos|espacos|sobre)[^?#]+"]},
    {"name": "Teatro Nacional São João", "kind": "Teatro, dança e ópera — Porto",
     "urls": ["https://www.tnsj.pt/pt/"],
     "patterns": [r"tnsj\.pt/pt/espetaculos/\d+/[^?#]+"]},
    {"name": "Teatro Municipal do Porto", "kind": "Teatro e dança — Porto",
     "urls": ["https://www.teatromunicipaldoporto.pt/pt/?ano=2026"],
     "patterns": [r"teatromunicipaldoporto\.pt/pt/programa/[^?#]+"]},
    {"name": "Europarque", "kind": "Aveiro",
     "urls": ["https://www.europarque.pt/", "https://www.europarque.pt/agenda-completa/"],
     "patterns": [r"bol\.pt/Comprar/Bilhetes/[^?#]+-europarque/"]},
    {"name": "Teatro Aveirense", "kind": "Aveiro",
     "urls": ["https://www.teatroaveirense.pt/index.php/programacao/",
              "https://www.teatroaveirense.pt/pt/programacao/"],
     "patterns": [r"teatroaveirense\.pt/index\.php/evento/[^?#]+"]},
    {"name": "Cineteatro António Lamoso", "kind": "Música, teatro, dança e ópera — Aveiro",
     "urls": ["https://cineteatro.cm-feira.pt/"],
     "patterns": [], "heading_tags": ["h2"],
     "heading_exclude": [r"^Agenda\b", r"^Contactos$", r"^Localização$", r"^Bilheteira$"]},
    {"name": "Casa da Criatividade", "kind": "São João da Madeira",
     "urls": ["https://www.casadacriatividade.com/infantil",
              "https://www.casadacriatividade.com/teatro",
              "https://www.casadacriatividade.com/danca",
              "https://www.casadacriatividade.com/somos-nos"],
     "patterns": [], "heading_tags": ["h2", "h3"],
     "heading_exclude": [r"^Casa da Criatividade\b", r"^Programa$", r"^Contactos$"],
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
     "patterns": [r"arena\.meo\.pt/agenda/[^/?#]+/\d+/?$"]},
    {"name": "Coliseu dos Recreios", "kind": "Grande sala",
     "urls": ["https://coliseulisboa.com/o-coliseu/programacao/"],
     "patterns": [r"coliseulisboa\.com/eventos/"]},
    {"name": "Teatro Nacional de São Carlos", "kind": "Ópera — grandes espetáculos",
     "urls": ["https://www.saocarlos.pt/"],
     "patterns": [r"saocarlos\.pt/(?:pt/)?program/[^?#]+"]},
    {"name": "Companhia Nacional de Bailado", "kind": "Ballet e dança — grandes espetáculos",
     "urls": ["https://www.cnb.pt/"],
     "patterns": [r"cnb\.pt/(?:pt/)?program/[^?#]+"]},
    {"name": "Centro Cultural de Belém", "kind": "Grandes espetáculos nacionais",
     "urls": ["https://www.ccb.pt/agenda/"],
     "patterns": [r"ccb\.pt/evento/[^?#]+"]},
    {"name": "Agenda do Pedro", "kind": "Agenda complementar do Porto",
     "urls": ["https://agendadopedro.pt/concertos-no-porto/agenda"],
     "patterns": [r"agendadopedro\.pt/concertos/[^?#]+"]},
    {"name": "BLITZ Agenda", "kind": "Agenda nacional",
     "urls": ["https://rss.impresa.pt/feed/latest/expresso.rss?type=ARTICLE,VIDEO,STREAM,PLAYLIST,EVENT&limit=100&pubsubhub=true"],
     "patterns": [r"expresso\.pt/blitz/(?:agenda|musica/ao-vivo)/\d{4}-"],
     "rss": True},
]

GENERIC_TITLES = {
    "mais info", "saber mais", "detalhes", "comprar", "bilhetes", "info",
    "ver mais", "read more", "agenda", "programação", "programacao",
    "ler mais", "eventos", "arquivo", "imprensa", "contacto", "contactos",
}

FEATURED_EVENTS = [
    {
        "title": "Sigur Rós — The Orchestral Tour",
        "date": "13 setembro 2026, 21h00",
        "link": "https://sigurros.com/tour/",
        "source": "Sigur Rós / Coliseu Porto Ageas",
        "kind": "Concerto prioritário — Porto",
        "expires": "2026-09-14",
    },
    {
        "title": "Relicário perpétuo — ópera de Luís Tinoco",
        "date": "11 e 12 setembro 2026",
        "link": "https://www.saocarlos.pt/en/program/relicario-perpetuo/",
        "source": "Teatro Nacional São João / São Carlos",
        "kind": "Ópera — Porto",
        "expires": "2026-09-13",
    },
    {
        "title": "Gala Lírica com Olga Kulchynska",
        "date": "13 setembro 2026, 17h00",
        "link": "https://www.europarque.pt/agenda-completa/",
        "source": "Europarque",
        "kind": "Ópera e música clássica — Aveiro",
        "expires": "2026-09-14",
    },
    {
        "title": "Os dias levantados — ópera em versão de concerto",
        "date": "7 outubro 2026, 21h00",
        "link": "https://www.saocarlos.pt/calendar/",
        "source": "Coliseu Porto Ageas / São Carlos",
        "kind": "Ópera — Porto",
        "expires": "2026-10-08",
    },
    {
        "title": "Turandot, de Giacomo Puccini",
        "date": "31 outubro 2026, 21h30",
        "link": "https://cineteatro.cm-feira.pt/",
        "source": "Cineteatro António Lamoso",
        "kind": "Ópera — Aveiro",
        "expires": "2026-11-01",
    },
    {
        "title": "O Quebra-Nozes — Imperial Heritage Ballet",
        "date": "17 janeiro 2027",
        "link": "https://www.europarque.pt/agenda-completa/",
        "source": "Europarque",
        "kind": "Ballet — Aveiro",
        "expires": "2027-01-18",
    },
]


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


class PageInfoParser(HTMLParser):
    def __init__(self, heading_tags=None):
        super().__init__(convert_charrefs=True)
        self.meta_titles, self.page_title, self.h1 = [], [], []
        self.heading_tags = {tag.lower() for tag in (heading_tags or [])}
        self.headings, self.capture, self.buffer = [], None, []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        values = {str(k).lower(): v for k, v in attrs}
        meta_name = (values.get("property") or values.get("name") or "").lower()
        if tag == "meta" and meta_name in {"og:title", "twitter:title"} and values.get("content"):
            self.meta_titles.append(values["content"])
        if tag in {"title", "h1"} or tag in self.heading_tags:
            self.capture, self.buffer = tag, []

    def handle_data(self, data):
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag != self.capture:
            return
        value = clean(" ".join(self.buffer))
        if value:
            if tag == "title":
                self.page_title.append(value)
            elif tag == "h1":
                self.h1.append(value)
            elif tag in self.heading_tags:
                self.headings.append(value)
        self.capture, self.buffer = None, []


def clean(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip(" -|\t\r\n")


def canonical_url(url):
    parts = urlsplit(url)
    path = re.sub(r"/+", "/", parts.path).rstrip("/") + "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def title_from_url(url):
    parts = [part for part in urlsplit(url).path.rstrip("/").split("/") if part]
    slug = parts[-1] if parts else ""
    if re.fullmatch(r"\d+", slug) and len(parts) > 1:
        slug = parts[-2]
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", slug)
    slug = re.sub(r"^\d{5,}[-_]", "", slug)
    slug = re.sub(r"_pt$", "", slug, flags=re.I)
    return clean(slug.replace("-", " ").replace("_", " ")).title()


def title_needs_detail(title):
    normalized = clean(title).casefold()
    return (
        not normalized
        or normalized in GENERIC_TITLES
        or len(normalized) < 3
        or not re.search(r"[a-záàâãéêíóôõúç]", normalized, re.I)
        or bool(re.fullmatch(r"(?:\d+[\s./:-]*)+", normalized))
    )


def detail_title(raw):
    parser = PageInfoParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    choices = parser.meta_titles + parser.h1 + parser.page_title
    for choice in choices:
        value = clean(choice)
        value = re.split(r"\s+[|–—]\s+", value, maxsplit=1)[0]
        if not title_needs_detail(value):
            return value
    return ""


def get_bytes(url):
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-PT,pt;q=0.9"})
    with urlopen(req, timeout=18) as response:
        return response.read()


def extract_source(source):
    found, failures = {}, []
    compiled = [re.compile(p, re.I) for p in source.get("patterns", [])]
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
            if source.get("heading_tags"):
                heading_parser = PageInfoParser(source["heading_tags"])
                heading_parser.feed(raw.decode("utf-8", errors="replace"))
                excluded = [re.compile(p, re.I) for p in source.get("heading_exclude", [])]
                for heading in heading_parser.headings:
                    title = clean(heading)
                    if title_needs_detail(title) or any(p.search(title) for p in excluded):
                        continue
                    absolute = canonical_url(page_url)
                    key = hashlib.sha256(f"{absolute}\n{title.casefold()}".encode("utf-8")).hexdigest()[:24]
                    found[key] = {"id": key, "title": title, "link": absolute,
                                  "source": source["name"], "kind": source["kind"]}
                    page_count += 1
            for href, anchor_text in candidates:
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                absolute = canonical_url(urljoin(page_url, href))
                if not compiled or not any(p.search(absolute) for p in compiled):
                    continue
                title = clean(anchor_text)
                weak_anchor = title_needs_detail(title)
                if weak_anchor:
                    title = title_from_url(absolute)
                    if title_needs_detail(title):
                        try:
                            better = detail_title(get_bytes(absolute))
                            if better:
                                title = better
                        except Exception:
                            pass
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


def active_featured_events():
    today = date.today().isoformat()
    result = []
    for item in FEATURED_EVENTS:
        if item.get("expires", "9999-12-31") < today:
            continue
        event = {key: value for key, value in item.items() if key != "expires"}
        event["link"] = canonical_url(event["link"])
        event["id"] = hashlib.sha256(
            f"featured\n{event['link']}\n{event.get('date', '')}".encode("utf-8")
        ).hexdigest()[:24]
        result.append(event)
    return result


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
    heading = "🎭 Agenda inicial de espetáculos" if initial else "🎭 Novos espetáculos encontrados"
    lines = [heading, f"{len(events)} resultados em {len(by_source)} fontes", ""]
    for source_name in sorted(by_source):
        group = sorted(by_source[source_name], key=lambda e: e["title"].casefold())
        lines.append(f"📍 {source_name} ({len(group)})")
        for event in group:
            when = f" — {event['date']}" if event.get("date") else ""
            lines.append(f"• {event['title']}{when}\n{event['link']}")
        lines.append("")
    if failures:
        lines.extend(["⚠️ Fontes a rever:"] + [f"• {failure}" for failure in failures[:15]])
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet-baseline", action="store_true")
    parser.add_argument("--refresh-baseline", action="store_true")
    args = parser.parse_args()

    all_events, failures, counts = [], [], []
    for source in SOURCES:
        events, source_failures = extract_source(source)
        all_events.extend(events)
        failures.extend([f"{source['name']}: {item}" for item in source_failures])
        counts.append((source["name"], len(events)))

    featured = active_featured_events()
    all_events.extend(featured)
    if featured:
        counts.append(("Eventos prioritários", len(featured)))

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

    if args.refresh_baseline:
        featured_ids = {event["id"] for event in featured}
        save_seen({event["id"]: event for event in all_events if event["id"] not in featured_ids})
        print("Agenda atual guardada como base, sem envio para o Telegram.")
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
