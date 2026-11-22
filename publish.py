"""PastryPower - file d'attente auto : chaque video deposee dans le dossier Drive sort au prochain creneau.
Titre = nom du fichier. Creneaux reglables dans HEURES (workflow). Tourne sur GitHub Actions."""
import json, os, sys, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gdown
import requests

G = "https://graph.facebook.com/v26.0"
TZ = ZoneInfo("Europe/Paris")
STATE = "state.json"
VIDEOS = (".mp4", ".mov", ".m4v")
FENETRE = timedelta(hours=6)  # un creneau rate de plus de 6 h est saute


def api(method, url, **kw):
    r = requests.request(method, url, timeout=900, **kw)
    data = r.json() if r.content else {}
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(data.get("error", {}).get("message", r.text[:300]))
    return data


def upload(url, tok, chemin):
    with open(chemin, "rb") as f:
        api("POST", url, data=f, headers={
            "Authorization": f"OAuth {tok}", "offset": "0",
            "file_size": str(os.path.getsize(chemin)),
        })


def publier_insta(c, chemin, titre):
    tok = c["page_token"]
    cont = api("POST", f"{G}/{c['ig_id']}/media", data={
        "media_type": "REELS", "upload_type": "resumable", "caption": titre,
        "share_to_feed": "true", "access_token": tok,
    })
    upload(cont["uri"], tok, chemin)
    for _ in range(120):
        st = api("GET", f"{G}/{cont['id']}", params={"fields": "status_code", "access_token": tok})["status_code"]
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"traitement Insta : {st}")
        time.sleep(10)
    else:
        raise RuntimeError("traitement Insta trop long")
    api("POST", f"{G}/{c['ig_id']}/media_publish", data={"creation_id": cont["id"], "access_token": tok})


def publier_fb(c, chemin, titre):
    tok, pid = c["page_token"], c["page_id"]
    start = api("POST", f"{G}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok})
    upload(start["upload_url"], tok, chemin)
    api("POST", f"{G}/{pid}/video_reels", data={
        "upload_phase": "finish", "video_id": start["video_id"],
        "video_state": "PUBLISHED", "description": titre, "access_token": tok,
    })


def main():
    compte = next(iter(json.loads(os.environ["META_COMPTES"]).values()))
    etat = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    etat.setdefault("publiees", {})
    etat.setdefault("creneaux", {})
    maintenant = datetime.now(TZ)

    # creneau du moment : le plus recent deja passe, dans la fenetre, pas encore utilise
    creneau = None
    for jour in (maintenant.date(), maintenant.date() - timedelta(days=1)):
        for h in sorted(os.environ.get("HEURES", "18:30").split(","), reverse=True):
            hh, mm = map(int, h.strip().split(":"))
            t = datetime(jour.year, jour.month, jour.day, hh, mm, tzinfo=TZ)
            cle = t.strftime("%Y-%m-%d %H:%M")
            if t <= maintenant < t + FENETRE and cle not in etat["creneaux"]:
                creneau = cle
                break
        if creneau:
            break
    if not creneau:
        print("Pas de créneau à servir maintenant.")
        return

    fichiers = gdown.download_folder(os.environ["DRIVE_FOLDER_URL"], skip_download=True, quiet=True) or []
    file = sorted([f for f in fichiers if f.path.lower().endswith(VIDEOS)
                   and f.path.split("/")[-1] not in etat["publiees"]], key=lambda f: f.path.lower())
    if not file:
        print("File d'attente vide.")
        return

    v = file[0]
    nom = v.path.split("/")[-1]
    titre = os.path.splitext(nom)[0].replace("_", " ").strip()
    chemin = os.path.join("/tmp", nom)
    try:
        gdown.download(id=v.id, output=chemin, quiet=True)
        insta_ok = etat.setdefault("insta_ok", {})
        if nom not in insta_ok:  # évite un double post Insta si Facebook plante
            publier_insta(compte, chemin, titre)
            insta_ok[nom] = True
        publier_fb(compte, chemin, titre)
        etat["publiees"][nom] = maintenant.strftime("%d/%m/%Y %H:%M")
        etat["creneaux"][creneau] = nom
        print(f"Publié : {nom} (créneau {creneau}). Reste {len(file) - 1} vidéo(s) en attente.")
    except Exception as e:
        print(f"ERREUR sur {nom} : {e}")
        json.dump(etat, open(STATE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        sys.exit(1)  # GitHub t'envoie un mail
    json.dump(etat, open(STATE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
