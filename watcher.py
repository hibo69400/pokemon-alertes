#!/usr/bin/env python3
"""Alertes Pokémon : surveille le stock de produits et les prospectus,
puis envoie une notification sur le téléphone via ntfy."""
import argparse
import base64
import hashlib
import json
import os
import re
import time
import unicodedata
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
        base, anniv = keyword_hits(re.sub(r"<[^>]+>", " ", r.text), cfg)
        if base:
            hits.append(("page web", "", anniv))
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


def run_collection(c, cfg, state):
    ville = cfg.get("ville", "")
    try:
        r = requests.get(c["url"], headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        log(f"  erreur réseau : {e}")
        return
    if r.status_code != 200:
        log(f"  HTTP {r.status_code}")
        return
    products = extract_products(r.text, c["url"], cfg, c.get("deja_pokemon", False))
    if not products:
        log("  aucun produit 30 ans trouvé (page chargée en JavaScript ? lien trop général ?)")
        return
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
    save_state(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="envoie une notification de test")
    ap.add_argument("--boucle", type=int, metavar="MIN", help="relance toutes les MIN minutes")
    args = ap.parse_args()
    if args.test:
        ok = notify("✅ Test réussi", "Les notifications Pokémon arrivent bien sur ton téléphone.")
        raise SystemExit(0 if ok else 1)
    if args.boucle:
        while True:
            run_once()
            time.sleep(args.boucle * 60)
    run_once()


if __name__ == "__main__":
    main()
