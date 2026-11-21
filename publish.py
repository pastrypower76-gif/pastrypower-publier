"""PastryPower - publication auto Insta + Facebook depuis Google Drive (tourne sur GitHub Actions)."""
import csv, io, json, os, sys, time, unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import gdown
import requests

G = "https://graph.facebook.com/v26.0"
TZ = ZoneInfo("Europe/Paris")
STATE = "state.json"
MAX_RETARD = timedelta(hours=12)  # au-delà, on ne publie plus une vidéo ratée


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


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
    return api("POST", f"{G}/{c['ig_id']}/media_publish",
               data={"creation_id": cont["id"], "access_token": tok})["id"]


def publier_fb(c, chemin, titre):
    tok, pid = c["page_token"], c["page_id"]
    start = api("POST", f"{G}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok})
    upload(start["upload_url"], tok, chemin)
    api("POST", f"{G}/{pid}/video_reels", data={
        "upload_phase": "finish", "video_id": start["video_id"],
        "video_state": "PUBLISHED", "description": titre, "access_token": tok,
    })
    return start["video_id"]


def lire_date(d, h):
    d, h = d.strip(), h.strip().lower().replace("h", ":")
    if h.endswith(":"):
        h += "00"
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{d} {h}", fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    raise ValueError(f"date/heure illisible : {d} {h}")


def main():
    comptes = {norm(k): v for k, v in json.loads(os.environ["META_COMPTES"]).items()}
    etat = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    csv_txt = requests.get(os.environ["SHEET_CSV_URL"], timeout=60)
    csv_txt.encoding = "utf-8"
    lignes = list(csv.DictReader(io.StringIO(csv_txt.text)))
    maintenant = datetime.now(TZ)

    a_faire = []
    for l in lignes:
        l = {norm(k): (v or "").strip() for k, v in l.items() if k}
        if not l.get("fichier"):
            continue
        cle = f"{l['fichier']}|{l.get('compte','')}|{l.get('date','')}|{l.get('heure','')}"
        if etat.get(cle, {}).get("statut") == "publié":
            continue
        try:
            quand = lire_date(l.get("date", ""), l.get("heure", ""))
        except ValueError as e:
            etat[cle] = {"statut": f"erreur : {e}"}
            continue
        if quand > maintenant:
            continue
        if maintenant - quand > MAX_RETARD:
            etat[cle] = {"statut": "trop tard (plus de 12 h de retard), non publié"}
            continue
        a_faire.append((cle, l))

    erreurs = 0
    if a_faire:
        fichiers = {f.path.split("/")[-1]: f.id for f in
                    gdown.download_folder(os.environ["DRIVE_FOLDER_URL"], skip_download=True, quiet=True)}
        for cle, l in a_faire:
            deja = etat.get(cle, {}).get("faits", [])
            res = {"faits": list(deja)}
            try:
                c = comptes.get(norm(l.get("compte", "")))
                if not c:
                    raise RuntimeError(f"compte « {l.get('compte')} » inconnu")
                fid = fichiers.get(l["fichier"])
                if not fid:
                    raise RuntimeError("vidéo introuvable dans le dossier Drive")
                chemin = os.path.join("/tmp", l["fichier"])
                gdown.download(id=fid, output=chemin, quiet=True)
                for r in [x.strip().lower() for x in (l.get("reseaux") or "insta,fb").split(",") if x.strip()]:
                    if r in res["faits"]:
                        continue
                    if r == "insta":
                        publier_insta(c, chemin, l.get("titre", ""))
                    elif r == "fb":
                        publier_fb(c, chemin, l.get("titre", ""))
                    else:
                        raise RuntimeError(f"réseau inconnu : {r}")
                    res["faits"].append(r)
                    print(f"OK {r} : {l['fichier']}")
                res["statut"] = "publié"
                res["le"] = maintenant.strftime("%d/%m/%Y %H:%M")
            except Exception as e:
                erreurs += 1
                res["statut"] = f"erreur : {e}"
                print(f"ERREUR {l['fichier']} : {e}")
            etat[cle] = res

    json.dump(etat, open(STATE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    sys.exit(1 if erreurs else 0)  # en cas d'erreur, GitHub t'envoie un mail


if __name__ == "__main__":
    main()
