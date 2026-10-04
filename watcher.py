#!/usr/bin/env python3
"""Alertes Pokémon : surveille le stock de produits, les prospectus et la veille
communautaire (RSS, Telegram, forums), puis notifie le téléphone via ntfy."""
import argparse
import base64
import hashlib
import html as html_lib
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urldefrag, urljoin

import requests
import yaml

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "config.yml"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"),
    "Accept-Language": "fr-FR,fr;q=0.9",
}

DEFAULT_OUT = ["rupture de stock", "indisponible", "epuise", "out of stock", "sold out"]
DEFAULT_IN = ["ajouter au panier", "add to cart"]
IN_SCHEMA = {"instock", "onlineonly", "limitedavailability", "instoreonly", "preorder", "presale"}
OUT_SCHEMA = {"outofstock", "soldout", "discontinued"}
AVAIL_PATTERNS = [
    re.compile(r'"availability"\s*:\s*"(?:https?:)?(?://)?schema\.org/(\w+)"', re.I),
    re.compile(r'itemprop=["\']availability["\'][^>]*?(?:href|content)=["\'](?:https?:)?(?://)?schema\.org/(\w+)', re.I),
]
ANCHOR_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
HREF_RE = re.compile(r"href=(?:\"([^\"]*)\"|'([^']*)')", re.I)
ATTR_TEXT_RE = re.compile(r"(?:title|aria-label)=(?:\"([^\"]*)\"|'([^']*)')", re.I)
DEFAULT_ANNIV_WORDS = ["30 ans", "30th", "30e", "30eme", "anniversaire", "celebration"]
VISION_PROMPT = (
    "Cette image est une page d'un prospectus de magasin français. Contient-elle des "
    "produits Pokémon (cartes à collectionner, boosters, displays, coffrets), notamment "
    "liés aux 30 ans de Pokémon ? Réponds uniquement avec un JSON : "
    '{"pokemon": true/false, "trentieme": true/false, "resume": "produits et prix en une phrase"}'
)


def log(msg):
    print(msg, flush=True)


def norm(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def load_state():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    state.setdefault("stock", {})
    state.setdefault("flyers", {})
    state.setdefault("collections", {})
    state.setdefault("erreurs", {})
    state.setdefault("veille", {})
    state.setdefault("veille_titres", {})
    state.setdefault("veille_echecs", {})
    return state


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def notify(title, message, click=None, priority=4, tags=None):
    log(f"NOTIF : {title} | {message}")
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        log("  (NTFY_TOPIC absent : notification non envoyée)")
        return False
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    payload = {"topic": topic, "title": title, "message": message,
               "priority": priority, "tags": tags or []}
    if click:
        payload["click"] = click
    try:
        requests.post(server, json=payload, timeout=20).raise_for_status()
        return True
    except requests.RequestException as e:
        log(f"  échec de l'envoi ntfy : {e}")
        return False


# ── Stock ────────────────────────────────────────────────────
def check_stock(p):
    try:
        r = requests.get(p["url"], headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        log(f"  erreur réseau : {e}")
        return "unknown"
    if r.status_code != 200:
        log(f"  HTTP {r.status_code} (site qui bloque ou page introuvable ?)")
        return "unknown"
    html = r.text
    for pattern in AVAIL_PATTERNS:
        found = pattern.findall(html)
        if found:
            v = found[0].lower()
            if v in IN_SCHEMA:
                return "in_stock"
            if v in OUT_SCHEMA:
                return "out_of_stock"
    text = norm(html)
    if any(norm(m) in text for m in p.get("marqueurs_rupture", DEFAULT_OUT)):
        return "out_of_stock"
    if any(norm(m) in text for m in p.get("marqueurs_dispo", DEFAULT_IN)):
        return "in_stock"
    return "unknown"


# ── Prospectus ───────────────────────────────────────────────
def keyword_hits(text, cfg):
    t = norm(text)
    base = any(norm(k) in t for k in cfg.get("mots_cles", ["pokemon"]))
    anniv = any(norm(k) in t for k in cfg.get("mots_cles_30ans", ["30 ans"]))
    return base, anniv


def keyword_snippets(text, cfg):
    """Phrases/lignes contenant un mot-clé (pour ne notifier que si elles changent)."""
    words = [norm(k) for k in cfg.get("mots_cles", ["pokemon"])]
    seen, out = set(), []
    for seg in re.split(r"\n+|(?<=[.!?])\s+", text):
        seg = re.sub(r"\s+", " ", seg).strip()
        if len(seg) < 8 or seg in seen:
            continue
        if any(w in norm(seg) for w in words):
            seen.add(seg)
            out.append(seg[:160])
    return out[:5]


def vision_check(api_key, model, data, media_type):
    body = {
        "model": model, "max_tokens": 300,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                         "data": base64.b64encode(data).decode()}},
            {"type": "text", "text": VISION_PROMPT},
        ]}],
    }
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=90,
                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                                   "content-type": "application/json"})
    except requests.RequestException as e:
        log(f"  erreur API : {e}")
        return None
    if r.status_code != 200:
        log(f"  API HTTP {r.status_code} : {r.text[:200]}")
        return None
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def scan_pdf(content, cfg, api_key):
    import fitz  # pymupdf
    hits, errors, ia_calls = [], 0, 0
    doc = fitz.open(stream=content, filetype="pdf")
    limit = cfg.get("max_pages_ia", 40)
    for i, page in enumerate(doc, 1):
        text = page.get_text()
        base, anniv = keyword_hits(text, cfg)
        if base:
            hits.append((f"page {i}", "", anniv))
            continue
        if len(text.strip()) >= 400:      # page riche en texte, sans mot-clé : on passe
            continue
        if not api_key or ia_calls >= limit:
            continue
        ia_calls += 1
        img = page.get_pixmap(dpi=110).tobytes("jpeg")
        res = vision_check(api_key, cfg.get("modele_ia", "claude-haiku-4-5-20251001"), img, "image/jpeg")
        if res is None:
            errors += 1
        elif res.get("pokemon"):
            hits.append((f"page {i}", res.get("resume", ""), bool(res.get("trentieme"))))
    return hits, errors


def analyse_flyer(f, cfg, state, api_key):
    url = f["url"]
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
    except requests.RequestException as e:
        log(f"  erreur réseau : {e}")
        return
    if r.status_code != 200:
        log(f"  HTTP {r.status_code}")
        return
    content = r.content
    digest = hashlib.sha256(content).hexdigest()
    if state["flyers"].get(url) == digest:
        log("  déjà analysé, rien de nouveau")
        return
    ctype = r.headers.get("content-type", "").lower().split(";")[0]
    hits, errors = [], 0
    if content[:4] == b"%PDF":
        hits, errors = scan_pdf(content, cfg, api_key)
    elif ctype in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        if not api_key:
            log("  image : ANTHROPIC_API_KEY nécessaire pour la lire")
            return
        if len(content) > 5_000_000:
            log("  image trop lourde (> 5 Mo)")
            return
        res = vision_check(api_key, cfg.get("modele_ia", "claude-haiku-4-5-20251001"), content, ctype)
        if res is None:
            errors = 1
        elif res.get("pokemon"):
            hits.append(("image", res.get("resume", ""), bool(res.get("trentieme"))))
    else:
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S | re.I)
        text = re.sub(r"</?(?:p|div|li|h\d|br|tr|td|section|article)\b[^>]*>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        snips = keyword_snippets(text, cfg)
        if snips:
            snip_digest = hashlib.sha256("|".join(snips).encode()).hexdigest()
            if state["flyers"].get(url + "#extraits") == snip_digest:
                state["flyers"][url] = digest
                log("  mêmes extraits Pokémon qu'avant, rien de nouveau")
                return
            state["flyers"][url + "#extraits"] = snip_digest
            anniv = any(keyword_hits(s, cfg)[1] for s in snips)
            hits.append(("page web", snips[0], anniv))
    if errors == 0:
        state["flyers"][url] = digest
    if hits:
        anniv = any(h[2] for h in hits)
        titre = f"📰 {f['nom']} : Pokémon" + (" 30 ans !" if anniv else " !")
        lignes = [f"{h[0]}" + (f" — {h[1]}" if h[1] else "") for h in hits[:6]]
        notify(titre, f"{cfg.get('ville', '')}\n" + "\n".join(lignes), click=url,
               priority=5 if anniv else 4, tags=["newspaper"])
    else:
        log("  aucun produit Pokémon repéré")


# ── Pages de collection (tous les produits 30 ans d'une enseigne) ──
def extract_products(html, base_url, cfg, deja_pokemon=False):
    """Repère, dans une page de liste, les liens de produits Pokémon 30 ans."""
    anniv_words = [norm(w) for w in cfg.get("mots_cles_collections", DEFAULT_ANNIV_WORDS)]
    poke_words = [norm(w) for w in cfg.get("mots_cles", ["pokemon"])]
    found = {}
    for attrs, inner in ANCHOR_RE.findall(html):
        m = HREF_RE.search(attrs)
        if not m:
            continue
        href = (m.group(1) or m.group(2) or "").strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        label = " ".join(a or b for a, b in ATTR_TEXT_RE.findall(attrs)) + " " + re.sub(r"<[^>]+>", " ", inner)
        label = re.sub(r"\s+", " ", label).strip()
        hay = norm(label + " " + re.sub(r"[-_/.]", " ", url))
        if not deja_pokemon and not any(w in hay for w in poke_words):
            continue
        if not any(w in hay for w in anniv_words):
            continue
        found.setdefault(url, label[:80] or url)
    return found


def signale_probleme(c, state, raison):
    """Prévient UNE fois (par enseigne) que la page n'est pas lisible."""
    log(f"  {raison}")
    if not state["erreurs"].get(c["url"]):
        notify(f"⚠️ {c['nom']} : page illisible",
               f"{raison}. Ce site n'est pas surveillé pour l'instant.",
               click=c["url"], priority=3, tags=["warning"])
        state["erreurs"][c["url"]] = True


def run_collection(c, cfg, state):
    ville = cfg.get("ville", "")
    try:
        r = requests.get(c["url"], headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        signale_probleme(c, state, f"erreur réseau ({type(e).__name__})")
        return
    if r.status_code != 200:
        signale_probleme(c, state, f"le site répond HTTP {r.status_code} (il bloque le script ou l'adresse est fausse)")
        return
    products = extract_products(r.text, c["url"], cfg, c.get("deja_pokemon", False))
    if not products:
        signale_probleme(c, state, "aucun produit 30 ans repéré (page vide, chargée en JavaScript, ou mauvaise adresse)")
        return
    state["erreurs"].pop(c["url"], None)
    first = c["url"] not in state["collections"]
    known = set(state["collections"].get(c["url"], []))
    limit = cfg.get("max_produits_par_page", 40)
    in_stock_now = []
    for purl, name in list(products.items())[:limit]:
        status = check_stock({"url": purl})
        prev = state["stock"].get(purl)
        log(f"  {name[:50]} : {status} (avant : {prev})")
        if status != "unknown":
            state["stock"][purl] = status
        if status == "in_stock":
            in_stock_now.append(name)
            if not first and prev != "in_stock":
                notify(f"🟢 En stock : {name}", f"{c['nom']} — disponible ({ville})",
                       click=purl, priority=5, tags=["rotating_light"])
        elif not first and purl not in known:
            notify(f"🆕 Nouveau produit 30 ans : {name}",
                   f"{c['nom']} — vient d'apparaître (stock non confirmé)",
                   click=purl, priority=3, tags=["new"])
        time.sleep(1.5)
    if first:
        lignes = "\n".join(in_stock_now[:8]) or "aucun détecté en stock pour l'instant"
        notify(f"📋 {c['nom']} : {len(products)} produits 30 ans repérés",
               f"En stock maintenant :\n{lignes}", click=c["url"], priority=3, tags=["clipboard"])
    state["collections"][c["url"]] = sorted(known | set(products))


# ── Veille communautaire : internet, forums, réseaux sociaux ─────
# Trois types de sources, toutes lisibles sans clé API :
#   rss      : flux RSS/Atom (Reddit, Google Actualités, Bluesky, blogs, forums…)
#   telegram : canal Telegram PUBLIC (aperçu web t.me/s/<canal>)
#   page     : n'importe quelle page web (on repère les nouvelles lignes utiles)
DEFAULT_RESTOCK = ["restock", "reassort", "dispo", "en stock", "retour en stock", "drop",
                   "disponible", "precommande", "pre-commande", "ouverture", "30 ans",
                   "30th", "etb", "display", "booster", "coffret", "tin", "ultra premium"]
DEFAULT_EXCLUS = ["vends", "vend", "wts", "wtb", "echange", "echanges", "recherche",
                  "cherche", "estimation"]
TAG_RE = re.compile(r"<[^>]+>")
TG_POST_RE = re.compile(r'data-post="([^"]+)"')
TG_TEXT_RE = re.compile(r'class="(tgme_widget_message_text[^"]*)"[^>]*>(.*?)</div>', re.S)


def clean_html(s):
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", s or "", flags=re.I)
    s = html_lib.unescape(TAG_RE.sub(" ", s))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*", "\n", s).strip()


def one_line(s):
    return re.sub(r"\s+", " ", s or "").strip()


def src_key(src):
    return src.get("url") or "telegram:" + str(src.get("canal", "")).lstrip("@")


def http_get(url, timeout=30):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        return None, f"erreur réseau ({type(e).__name__})"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} (le site bloque le script ou l'adresse est fausse)"
    return r, None


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_feed(content):
    """RSS 2.0 ou Atom -> liste de {id, title, link, summary}. None si illisible."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        d = {"id": "", "title": "", "link": "", "summary": ""}
        for ch in el:
            n = _local(ch.tag)
            if n == "title":
                d["title"] = one_line(clean_html("".join(ch.itertext())))
            elif n == "link":
                href = ch.get("href")
                if href and ch.get("rel") in (None, "alternate"):
                    d["link"] = d["link"] or href
                elif ch.text and ch.text.strip():
                    d["link"] = d["link"] or ch.text.strip()
            elif n in ("guid", "id"):
                d["id"] = d["id"] or (ch.text or "").strip()
            elif n in ("description", "summary", "content", "encoded"):
                d["summary"] = d["summary"] or clean_html("".join(ch.itertext()))
        d["id"] = d["id"] or d["link"] or d["title"]
        if d["title"] or d["summary"]:
            items.append(d)
    return items


def parse_telegram(html):
    items, seen = [], set()
    for part in html.split('data-post="')[1:]:
        post = part.split('"', 1)[0]
        if post in seen:
            continue
        text = ""
        for cls, body in TG_TEXT_RE.findall(part):
            if "reply" not in cls:          # on ignore l'aperçu du message cité
                text = clean_html(body)
                break
        if not text:
            continue
        seen.add(post)
        items.append({"id": post, "title": one_line(text)[:110], "summary": text,
                      "link": f"https://t.me/{post}"})
    return items


def parse_page(html, url):
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ",
                  html, flags=re.S | re.I)
    items, seen = [], set()
    for seg in clean_html(text).split("\n"):
        seg = one_line(seg)
        if len(seg) < 15 or seg in seen:
            continue
        seen.add(seg)
        items.append({"id": hashlib.sha1(seg.encode()).hexdigest()[:16],
                      "title": seg[:110], "summary": seg, "link": url})
    return items


def fetch_items(src):
    """Retourne (items, erreur). Un flux RSS vide n'est pas une erreur."""
    kind = src.get("type", "rss")
    if kind == "telegram":
        canal = str(src.get("canal", "")).lstrip("@").strip()
        if not canal:
            return None, "champ « canal » manquant"
        r, err = http_get(f"https://t.me/s/{canal}")
        if err:
            return None, err
        items = parse_telegram(r.text)
        return (items, None) if items else (None, "aucun message lisible (canal privé, ou sans aperçu web public ?)")
    if not src.get("url"):
        return None, "champ « url » manquant"
    r, err = http_get(src["url"])
    if err:
        return None, err
    if kind == "page":
        items = parse_page(r.text, src["url"])
        return (items, None) if items else (None, "page vide (chargée en JavaScript ?)")
    items = parse_feed(r.content)
    if items is None:
        return None, "ce n'est pas un flux RSS/Atom lisible"
    return items, None


def relevance(text, src, cfg):
    """(utile, trentieme) : l'élément parle-t-il d'un restock Pokémon ?"""
    opts = cfg.get("veille_options") or {}
    t = norm(text)
    if not src.get("deja_pokemon", False):
        if not any(norm(k) in t for k in cfg.get("mots_cles", ["pokemon"])):
            return False, False
    for w in src.get("mots_exclus", opts.get("mots_exclus", DEFAULT_EXCLUS)):
        if re.search(r"(?<!\w)" + re.escape(norm(w)) + r"(?!\w)", t):
            return False, False
    if src.get("exiger_restock", True):
        words = src.get("mots_restock", opts.get("mots_restock", DEFAULT_RESTOCK))
        if not any(re.search(r"(?<!\w)" + re.escape(norm(w)), t) for w in words):
            return False, False
    anniv = any(norm(k) in t for k in cfg.get("mots_cles_30ans", ["30 ans"]))
    return True, anniv


def title_hash(title):
    return hashlib.sha1(re.sub(r"[^a-z0-9]", "", norm(title))[:80].encode()).hexdigest()[:16]


def veille_echec(src, state, raison):
    """Prévient une seule fois, après 3 échecs de suite (≈ 45 min), qu'une source est muette."""
    key = src_key(src)
    n = state["veille_echecs"].get(key, 0) + 1
    state["veille_echecs"][key] = n
    log(f"  {raison} (échec n°{n})")
    if n == 3:
        notify(f"⚠️ {src['nom']} : source illisible", f"{raison}. Cette source n'est pas surveillée pour l'instant.",
               click=src.get("url"), priority=3, tags=["warning"])


def run_veille_source(src, cfg, state, nouvelles):
    key, nom = src_key(src), src["nom"]
    opts = cfg.get("veille_options") or {}
    items, err = fetch_items(src)
    if err:
        veille_echec(src, state, err)
        return
    state["veille_echecs"].pop(key, None)
    seen = state["veille"].get(key)
    first = seen is None
    seen_set = set(seen or [])
    new = [it for it in items if it["id"] not in seen_set]
    current = [it["id"] for it in items]
    cap = max(300, 2 * len(current))
    state["veille"][key] = (current + [s for s in (seen or []) if s not in set(current)])[:cap]
    if first:                       # 1er passage : on mémorise sans alerter
        nouvelles.append(nom)
        log(f"  {len(items)} éléments mémorisés (1er passage)")
        return
    matches = []
    for it in new:
        ok, anniv = relevance(it["title"] + "\n" + it["summary"], src, cfg)
        if not ok:
            continue
        th = title_hash(it["title"])
        if th in state["veille_titres"]:      # même info déjà reçue d'une autre source
            log(f"  doublon ignoré : {it['title'][:60]}")
            continue
        state["veille_titres"][th] = int(time.time())
        matches.append((it, anniv))
    log(f"  {len(new)} nouveaux éléments, {len(matches)} utiles")
    limit = opts.get("max_notifs_par_source", 3)
    for it, anniv in matches[:limit]:
        msg = it["title"]
        if it["summary"] and not it["summary"].startswith(it["title"][:30]):
            msg += "\n" + one_line(it["summary"])[:250]
        notify(f"{'🔥' if anniv else '🌐'} {nom}", msg, click=it["link"] or src.get("url"),
               priority=5 if anniv else src.get("priorite", 4), tags=["mega"])
    if len(matches) > limit:
        notify(f"🌐 {nom} : {len(matches) - limit} autres alertes",
               "Trop d'alertes d'un coup, ouvre la source pour tout voir.",
               click=src.get("url") or matches[0][0]["link"], priority=3, tags=["mega"])


def run_veille(cfg, state):
    cutoff = time.time() - 3 * 86400
    state["veille_titres"] = {h: t for h, t in state["veille_titres"].items() if t > cutoff}
    nouvelles = []
    for src in cfg.get("veille") or []:
        log(f"[veille] {src['nom']}")
        run_veille_source(src, cfg, state, nouvelles)
        time.sleep(1.5)
    if nouvelles:
        notify("📡 Veille communautaire activée",
               "Sources branchées :\n" + "\n".join(nouvelles[:10]) +
               "\nTu recevras les prochains posts sur les restocks Pokémon.",
               priority=3, tags=["satellite"])


def veille_test(cfg):
    """Mode diagnostic : montre ce que chaque source renvoie et si ça déclencherait une alerte.
    N'envoie aucune notification et n'écrit rien dans state.json."""
    for src in cfg.get("veille") or []:
        log(f"\n[veille] {src['nom']}  ({src.get('type', 'rss')})")
        items, err = fetch_items(src)
        if err:
            log(f"  ✗ ILLISIBLE : {err}")
            continue
        n_ok = 0
        for it in items[:15]:
            ok, anniv = relevance(it["title"] + "\n" + it["summary"], src, cfg)
            n_ok += ok
            log(f"  {'✅' if ok else '·'} {'🔥' if anniv else '  '} {it['title'][:90]}")
        log(f"  → {len(items)} éléments lus, {n_ok} utiles parmi les 15 premiers")
        time.sleep(1)


# ── Boucle principale ────────────────────────────────────────
def run_once():
    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    state = load_state()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    for p in cfg.get("produits") or []:
        if "example.com" in p["url"]:
            continue
        status = check_stock(p)
        prev = state["stock"].get(p["url"])
        log(f"[stock] {p['nom']} : {status} (avant : {prev})")
        if status != "unknown":
            state["stock"][p["url"]] = status
            if status == "in_stock" and prev != "in_stock":
                notify(f"🟢 En stock : {p['nom']}",
                       f"{p.get('enseigne', '')} — disponible maintenant ({cfg.get('ville', '')})",
                       click=p["url"], priority=5, tags=["rotating_light"])
        time.sleep(2)
    for c in cfg.get("collections") or []:
        if "example.com" in c["url"]:
            continue
        log(f"[collection] {c['nom']}")
        run_collection(c, cfg, state)
        time.sleep(2)
    for f in cfg.get("prospectus") or []:
        if "example.com" in f["url"]:
            continue
        log(f"[prospectus] {f['nom']}")
        analyse_flyer(f, cfg, state, api_key)
        time.sleep(2)
    run_veille(cfg, state)
    save_state(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="envoie une notification de test")
    ap.add_argument("--veille-test", action="store_true",
                    help="diagnostic des sources de veille (aucune notification, rien de sauvegardé)")
    ap.add_argument("--boucle", type=int, metavar="MIN", help="relance toutes les MIN minutes")
    args = ap.parse_args()
    if args.test:
        ok = notify("✅ Test réussi", "Les notifications Pokémon arrivent bien sur ton téléphone.")
        raise SystemExit(0 if ok else 1)
    if args.veille_test:
        veille_test(yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")))
        return
    if args.boucle:
        while True:
            run_once()
            time.sleep(args.boucle * 60)
    run_once()


if __name__ == "__main__":
    main()
