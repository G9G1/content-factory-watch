"""
Surveillance des chaînes YouTube (option "alerte seule").

À chaque exécution :
  - lit config/channels.json
  - interroge le flux RSS de chaque chaîne (gratuit, sans clé, sans quota)
  - si une NOUVELLE vidéo est apparue depuis la dernière fois -> notification
    ntfy sur ton téléphone (titre + lien).

Le premier lancement se contente d'enregistrer l'état actuel SANS notifier
(sinon tu recevrais 22 notifications d'un coup). Ensuite, seules les vraies
nouveautés déclenchent une alerte.

Les YouTube Shorts sont IGNORÉS : seules les vraies vidéos (format long)
déclenchent une notification.

Usage :
    python watch.py           # vérifie et notifie les nouveautés
    python watch.py --test    # envoie une notif de test sur ton téléphone
    python watch.py --reset   # ré-enregistre l'état actuel comme référence

Tourne dans le cloud (GitHub Actions) toutes les 5 min : voir
.github/workflows/watch.yml.
"""
import os
import sys
import json
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
# En local on lit .env ; dans le cloud (GitHub Actions) python-dotenv n'est pas
# installé et NTFY_TOPIC vient des variables d'environnement/secrets -> l'import
# est optionnel pour que le script tourne sans aucune dépendance.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"), override=True)
except Exception:
    pass

CHANNELS_PATH = os.path.join(ROOT, "config", "channels.json")
STATE_PATH = os.path.join(ROOT, "data", "watch_state.json")
LOG_PATH = os.path.join(ROOT, "data", "watch.log")


def log(msg: str) -> None:
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + msg
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
NS = {"atom": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}


def _ntfy_topic() -> str:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    return topic


def send_ntfy(title: str, message: str, click_url: str = "") -> bool:
    """Envoie une notification via ntfy (publication JSON, UTF-8 OK)."""
    topic = _ntfy_topic()
    if not topic:
        print("⚠️  NTFY_TOPIC vide dans .env : notification non envoyée.")
        return False
    payload = {"topic": topic, "title": title, "message": message}
    if click_url:
        payload["click"] = click_url
    payload["tags"] = ["clapper"]
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh/", data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"⚠️  Échec envoi ntfy : {e}")
        return False


def load_channels() -> list:
    with open(CHANNELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _is_short(entry) -> bool:
    """
    Un Short ou une vraie vidéo ? Le flux RSS le dit déjà : le lien
    <link rel="alternate"> pointe vers .../shorts/ID pour un Short et vers
    .../watch?v=ID pour une vidéo classique. Aucune requête en plus, fiable.
    """
    for link in entry.findall("atom:link", NS):
        if link.get("rel") == "alternate":
            return "/shorts/" in (link.get("href") or "")
    return False


def fetch_entries(channel_id: str) -> list:
    """
    Retourne les vidéos de la chaîne, de la plus récente à la plus ancienne.
    Chaque élément : {video_id, title, url, is_short}.
    """
    url = RSS_URL.format(cid=channel_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml = resp.read()
    root = ET.fromstring(xml)
    entries = []
    for entry in root.findall("atom:entry", NS):
        vid = entry.find("yt:videoId", NS)
        if vid is None or not vid.text:
            continue
        title = entry.find("atom:title", NS)
        entries.append({
            "video_id": vid.text,
            "title": title.text if title is not None else "",
            "url": f"https://www.youtube.com/watch?v={vid.text}",
            "is_short": _is_short(entry),
        })
    return entries


def main():
    if "--test" in sys.argv:
        ok = send_ntfy("Content Factory ✅",
                       "Test de notification : si tu vois ce message, tout marche !",
                       "https://www.youtube.com")
        print("Notif de test envoyée." if ok else "Échec (voir ci-dessus).")
        return

    channels = load_channels()
    state = load_state()
    first_run = len(state) == 0
    reset = "--reset" in sys.argv

    if first_run:
        print("Premier lancement : enregistrement de l'état de référence "
              "(aucune notification).")
    if reset:
        print("Réinitialisation de la référence.")

    new_count = 0
    for ch in channels:
        cid = ch["channel_id"]
        name = ch["name"]
        try:
            entries = fetch_entries(cid)
        except Exception as e:
            print(f"  [{name}] erreur RSS : {e}")
            continue
        if not entries:
            print(f"  [{name}] pas de vidéo trouvée")
            continue

        newest_id = entries[0]["video_id"]
        known = state.get(cid)
        # Chaîne jamais vue (premier lancement, reset, ou chaîne ajoutée
        # après coup) : on enregistre sa vidéo actuelle SANS notifier.
        if first_run or reset or known is None:
            state[cid] = newest_id
            continue

        if newest_id == known:
            time.sleep(0.2)
            continue

        # Nouveautés = entrées plus récentes que la dernière connue. On
        # s'arrête à `known`. Si `known` est tombé du flux (trop ancien), on
        # ne prend que la plus récente pour éviter un envoi massif.
        new_entries = []
        for e in entries:
            if e["video_id"] == known:
                break
            new_entries.append(e)
        else:
            new_entries = entries[:1]

        # On notifie du plus ancien au plus récent (ordre chronologique), et
        # UNIQUEMENT les vraies vidéos : les Shorts sont ignorés.
        for e in reversed(new_entries):
            if e["is_short"]:
                log(f"⏭️  [{name}] Short ignoré : {e['title']}")
                continue
            new_count += 1
            log(f"🆕 [{name}] {e['title']} ({e['url']})")
            send_ntfy(f"🎬 {name} a posté !", e["title"], e["url"])

        state[cid] = newest_id
        time.sleep(0.2)  # léger délai pour ne pas marteler YouTube

    save_state(state)
    if first_run or reset:
        log(f"État enregistré pour {len(state)} chaîne(s). "
            f"Les prochaines vérifications notifieront les nouveautés.")
    else:
        log(f"Vérification terminée : {new_count} nouvelle(s) vidéo(s).")


if __name__ == "__main__":
    main()
