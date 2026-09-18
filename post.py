"""
Rappels de publication dans le cloud (GitHub Actions).

À chaque créneau (08:00, 10:00, ... 22:00, heure de Paris), envoie une notif
ntfy avec le prochain clip à poster : titre + légende + hashtags prêts à
copier-coller. Tu publies MANUELLEMENT depuis ton iPhone le clip correspondant
de ta pellicule (Photos), sur YouTube Short + Instagram + TikTok.

Tourne dans le cloud (indépendant du PC de Gaëtan, souvent éteint). La file
d'attente (data/publish_queue.json) est alimentée depuis le PC par
« planner.py --push ». Les clips vidéo eux-mêmes ne montent PAS ici : ils sont
déjà dans la pellicule iPhone via iCloud.

Usage :
    python post.py            # si on est dans un créneau, notifie le prochain clip
    python post.py --force    # notifie tout de suite (ignore l'heure), pour tester
    python post.py --test     # simple notif de test
"""
import os
import sys
import json
import time
import urllib.request
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    PARIS = ZoneInfo("Europe/Paris")
except Exception:
    PARIS = None

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(ROOT, "data", "publish_queue.json")

PLATFORMS = ["YouTube Short", "Instagram", "TikTok"]
# Créneaux (heure de Paris) : toutes les 2 h de 8 h à 22 h -> 8 heures paires.
SLOT_HOURS = {8, 10, 12, 14, 16, 18, 20, 22}
# Garde anti double-envoi : si un post a eu lieu il y a moins de ça, on saute
# (le workflow tourne plusieurs fois par créneau ; ça garantit 1 notif/créneau).
MIN_GAP_SECONDS = 90 * 60


def now_paris() -> datetime:
    return datetime.now(PARIS) if PARIS else datetime.now()


def send_ntfy(title: str, message: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC vide : notification non envoyée.")
        return False
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": ["calendar"],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://ntfy.sh/", data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"Echec envoi ntfy : {e}")
        return False


def load_queue() -> dict:
    if os.path.exists(QUEUE_PATH):
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"items": [], "last_post_ts": 0}


def save_queue(q: dict) -> None:
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)


def _caption(item: dict) -> str:
    tags = " ".join(item.get("hashtags", []))
    parts = [item.get("description", "").strip()]
    if item.get("cta"):
        parts.append(item["cta"].strip())
    if tags:
        parts.append(tags)
    return "\n\n".join(p for p in parts if p)


def post_next(force: bool = False) -> None:
    q = load_queue()
    now = time.time()
    t = now_paris()

    if not force:
        if t.hour not in SLOT_HOURS:
            print(f"Hors créneau (il est {t.strftime('%H:%M')} à Paris). Rien à envoyer.")
            return
        if now - q.get("last_post_ts", 0) < MIN_GAP_SECONDS:
            print("Un rappel a déjà été envoyé récemment (<90 min). On saute.")
            return

    nxt = next((it for it in q["items"] if it.get("posted_at") is None), None)
    if nxt is None:
        print("File d'attente vide : plus de clips à publier.")
        return

    slot = t.strftime("%H:%M")
    body = (
        f"🎬 {nxt['title']}\n"
        f"📲 Poste le prochain clip de ta pellicule sur : {' + '.join(PLATFORMS)}\n\n"
        f"— Légende à copier —\n{_caption(nxt)}"
    )
    ok = send_ntfy(f"📅 {slot} — C'est l'heure de poster !", body)

    nxt["posted_at"] = t.strftime("%Y-%m-%d %H:%M:%S")
    nxt["slot"] = slot
    q["last_post_ts"] = now
    save_queue(q)
    pending = sum(1 for it in q["items"] if it.get("posted_at") is None)
    print(f"{'Notif envoyée' if ok else 'Echec notif'} — « {nxt['title']} » "
          f"({slot}). Restant : {pending}.")


def main():
    if "--test" in sys.argv:
        ok = send_ntfy(
            "📅 Rappels de publication ✅",
            "Test : à chaque créneau (8h→22h, heure de Paris) tu recevras ici "
            "le clip à poster depuis ta pellicule.",
        )
        print("Notif de test envoyée." if ok else "Echec (voir ci-dessus).")
        return
    post_next(force="--force" in sys.argv)


if __name__ == "__main__":
    main()
