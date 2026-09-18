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

Usage :
    python watch.py           # vérifie et notifie les nouveautés
    python watch.py --test    # envoie une notif de test sur ton téléphone
    python watch.py --reset   # ré-enregistre l'état actuel comme référence

À planifier toutes les 15-30 min via le Planificateur de tâches Windows
(voir watch.bat).
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


def fetch_latest(channel_id: str) -> dict:
    """Retourne la vidéo la plus récente d'une chaîne, ou None."""
    url = RSS_URL.format(cid=channel_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml = resp.read()
    root = ET.fromstring(xml)
    entry = root.find("atom:entry", NS)
    if entry is None:
        return None
    vid = entry.find("yt:videoId", NS)
    title = entry.find("atom:title", NS)
    published = entry.find("atom:published", NS)
    return {
        "video_id": vid.text if vid is not None else None,
        "title": title.text if title is not None else "",
        "published": published.text if published is not None else "",
        "url": f"https://www.youtube.com/watch?v={vid.text}" if vid is not None else "",
    }


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
            latest = fetch_latest(cid)
        except Exception as e:
            print(f"  [{name}] erreur RSS : {e}")
            continue
        if not latest or not latest["video_id"]:
            print(f"  [{name}] pas de vidéo trouvée")
            continue

        known = state.get(cid)
        # Chaîne jamais vue (premier lancement, reset, ou chaîne ajoutée
        # après coup) : on enregistre sa vidéo actuelle SANS notifier.
        if first_run or reset or known is None:
            state[cid] = latest["video_id"]
            continue

        if latest["video_id"] != known:
            new_count += 1
            log(f"🆕 [{name}] {latest['title']} ({latest['url']})")
            send_ntfy(
                f"🎬 {name} a posté !",
                latest["title"],
                latest["url"],
            )
            state[cid] = latest["video_id"]
        time.sleep(0.2)  # léger délai pour ne pas marteler YouTube

    save_state(state)
    if first_run or reset:
        log(f"État enregistré pour {len(state)} chaîne(s). "
            f"Les prochaines vérifications notifieront les nouveautés.")
    else:
        log(f"Vérification terminée : {new_count} nouvelle(s) vidéo(s).")


if __name__ == "__main__":
    main()
