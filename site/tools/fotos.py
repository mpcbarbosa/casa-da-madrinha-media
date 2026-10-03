#!/usr/bin/env python3
"""Prepara as fotografias da Casa da Madrinha para o site.

Lê a pasta do Google Drive do proprietário com uma conta de serviço (secret
GDRIVE_SA_JSON; a pasta tem de estar partilhada com o email da conta de serviço),
descarrega só o que ainda não está em cache, corrige a orientação, REMOVE os
metadados EXIF (incluindo GPS) e gera versões WebP em várias larguras.

Saídas (não versionadas, geradas na publicação):
  site/img/<id>-<largura>.webp      versões para o site
  site/img/<id>-1200.jpg            versão JPEG (partilhas e redes sociais)
  site/img/fotos.json               índice com dimensões e caminhos
  site/_revisao/folha-<n>.jpg       folhas de contacto numeradas, para escolher as fotos

Uso: python site/tools/fotos.py
"""
import io, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "img")
REV = os.path.join(ROOT, "_revisao")
CACHE = os.environ.get("FOTOS_CACHE", os.path.join(os.path.dirname(ROOT), ".cache", "fotos"))
LARGURAS = [480, 960, 1600, 2400]
API = "https://www.googleapis.com/drive/v3/files"


def sessao():
    raw = os.environ.get("GDRIVE_SA_JSON", "").strip()
    if not raw:
        return None
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    cred = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(cred)


def listar(s, pasta):
    itens, token = [], None
    while True:
        p = {"q": f"'{pasta}' in parents and trashed = false",
             "fields": "nextPageToken, files(id,name,mimeType,modifiedTime,md5Checksum,size)",
             "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if token:
            p["pageToken"] = token
        r = s.get(API, params=p, timeout=60)
        r.raise_for_status()
        j = r.json()
        itens += j.get("files", [])
        token = j.get("nextPageToken")
        if not token:
            return itens


def descarregar(s, fid):
    for tentativa in range(4):
        r = s.get(f"{API}/{fid}", params={"alt": "media", "supportsAllDrives": "true"}, timeout=180)
        if r.status_code == 200:
            return r.content
        time.sleep(2 * (tentativa + 1))
    r.raise_for_status()


def processar(dados, fid, destino):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    im = Image.open(io.BytesIO(dados))
    im = ImageOps.exif_transpose(im).convert("RGB")  # orientação correta; a imagem nova não leva EXIF
    w, h = im.size
    srcs = {}
    for lw in LARGURAS:
        if lw > w and lw != LARGURAS[0]:
            continue
        alvo = im if lw >= w else im.resize((lw, round(h * lw / w)), Image.LANCZOS)
        nome = f"{fid}-{lw}.webp"
        alvo.save(os.path.join(destino, nome), "WEBP", quality=78, method=6)
        srcs[str(min(lw, w))] = nome
    j = im if w <= 1200 else im.resize((1200, round(h * 1200 / w)), Image.LANCZOS)
    j.save(os.path.join(destino, f"{fid}-1200.jpg"), "JPEG", quality=82, optimize=True, progressive=True)
    return {"w": w, "h": h, "srcs": srcs, "jpg": f"{fid}-1200.jpg"}


def folhas(fotos):
    from PIL import Image, ImageDraw
    os.makedirs(REV, exist_ok=True)
    col, tw, th, por = 6, 320, 240, 36
    for n in range(0, len(fotos), por):
        lote = fotos[n:n + por]
        linhas = (len(lote) + col - 1) // col
        folha = Image.new("RGB", (col * tw, linhas * (th + 28)), (238, 241, 234))
        d = ImageDraw.Draw(folha)
        for i, f in enumerate(lote):
            menor = min(f["srcs"], key=int)
            im = Image.open(os.path.join(OUT, f["srcs"][menor]))
            im.thumbnail((tw - 8, th - 8))
            x, y = (i % col) * tw, (i // col) * (th + 28)
            folha.paste(im, (x + (tw - im.width) // 2, y + 4 + (th - 8 - im.height) // 2))
            d.text((x + 6, y + th + 4), f"{n + i + 1:03d}  {f['nome']}", fill=(30, 45, 36))
        folha.save(os.path.join(REV, f"folha-{n // por + 1}.jpg"), "JPEG", quality=80)


def main():
    pasta = (CFG.get("drive") or {}).get("pasta_fotos", "")
    s = sessao()
    if not s or not pasta:
        print("Fotografias: sem credenciais ou sem pasta configurada; o site usa a ilustração provisória.")
        return
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    itens = sorted((f for f in listar(s, pasta) if f.get("mimeType", "").startswith("image/")), key=lambda f: f["name"])
    print(f"{len(itens)} imagens na pasta")
    fotos, novas = [], 0
    for f in itens:
        chave = f"{f['id']}-{f.get('md5Checksum') or f.get('modifiedTime', '').replace(':', '')}"
        cdir = os.path.join(CACHE, chave)
        meta_p = os.path.join(cdir, "meta.json")
        if not os.path.exists(meta_p):
            try:
                os.makedirs(cdir, exist_ok=True)
                meta = processar(descarregar(s, f["id"]), f["id"], cdir)
                json.dump(meta, open(meta_p, "w"))
                novas += 1
            except Exception as e:
                print(f"  falhou {f['name']}: {type(e).__name__}", file=sys.stderr)
                continue
        meta = json.load(open(meta_p))
        for nome in list(meta["srcs"].values()) + [meta["jpg"]]:
            with open(os.path.join(cdir, nome), "rb") as a, open(os.path.join(OUT, nome), "wb") as b:
                b.write(a.read())
        fotos.append({"id": f["id"], "nome": f["name"], **meta})
    for i, f in enumerate(fotos, 1):
        f["n"] = i
    json.dump({"fotos": fotos}, open(os.path.join(OUT, "fotos.json"), "w"), ensure_ascii=False)
    folhas(fotos)
    print(f"{len(fotos)} fotografias prontas ({novas} novas)")


if __name__ == "__main__":
    main()
