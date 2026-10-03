#!/usr/bin/env python3
"""Prepara as fotografias da Casa da Madrinha para o site.

Lê o Google Drive do proprietário com uma conta de serviço (secret GDRIVE_SA_JSON;
a pasta tem de estar partilhada com o email da conta de serviço), descarrega só o
que ainda não está em cache, corrige a orientação, REMOVE os metadados EXIF
(incluindo GPS) e gera versões WebP em várias larguras.

Organização no Drive (modo «divisões»), dentro da pasta config.drive.pasta_fotos:

  Site/                         nome em config.drive.pasta_site (por omissão «Site»)
    Foto Principal/             1 foto = imagem fixa; 2 ou mais = carrossel no topo
    Quarto 1/                   uma pasta por divisão; o nome da pasta é o título
    Cozinha/                    no site (tradução automática para inglês)
    ...

  - A ordem das fotos dentro de cada pasta é a ordem do nome do ficheiro; as duas
    primeiras são as que aparecem na página principal.
  - A ordem das divisões segue uma ordem natural (ver ORDEM); um número no início
    do nome («01 Sala», «2. Cozinha») sobrepõe-se a essa ordem e não aparece no site.
  - «Quarto 1 | Master bedroom» define o título em inglês à mão.
  - Pastas que comecem por «_» ou «#» são ignoradas (rascunhos).

Se não existir a pasta «Site», usa o modo antigo: imagens soltas na pasta principal
e a seleção em site/content/galeria.json.

Saídas (não versionadas, geradas na publicação):
  site/img/<id>-<largura>.webp      versões para o site
  site/img/<id>-1200.jpg            versão JPEG (partilhas e redes sociais)
  site/img/fotos.json               índice: hero, divisões e lista completa
  site/_revisao/folha-<n>.jpg       folhas de contacto numeradas

Uso: python site/tools/fotos.py
"""
import io, json, os, re, sys, time, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "img")
REV = os.path.join(ROOT, "_revisao")
CACHE = os.environ.get("FOTOS_CACHE", os.path.join(os.path.dirname(ROOT), ".cache", "fotos"))
LARGURAS = [480, 960, 1600, 2400]
API = "https://www.googleapis.com/drive/v3/files"
PASTA = "application/vnd.google-apps.folder"

# ordem por omissão das divisões (pela palavra com que o nome da pasta começa)
ORDEM = ["quarto", "suite", "cozinha", "sala", "wc", "casa de banho", "vista", "piscina",
         "terraco", "varanda", "jardim", "exterior", "churrasqueira", "vila", "praia"]

# tradução automática do início do nome da pasta (o resto, p. ex. o número, mantém-se)
EN = {
    "quarto": "Bedroom", "suite": "Suite", "cozinha": "Kitchen",
    "sala de jantar": "Dining room", "sala de estar": "Living room", "sala": "Living room",
    "wc": "Bathroom", "casa de banho": "Bathroom", "vistas": "Views", "vista": "View",
    "piscina": "Pool", "terraco": "Terrace", "varanda": "Balcony", "jardim": "Garden",
    "exterior": "Outdoors", "churrasqueira": "Barbecue", "vila": "The town",
    "entrada": "Entrance", "hall": "Hall", "lavandaria": "Laundry", "garagem": "Garage",
    "escritorio": "Study", "alpendre": "Porch", "sotao": "Attic", "praia": "Beach",
    "zona de refeicoes": "Dining area", "corredor": "Hallway",
}


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def chave_natural(s):
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", norm(s))]


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


def imagens(itens):
    return sorted((f for f in itens if f.get("mimeType", "").startswith("image/")),
                  key=lambda f: chave_natural(f["name"]))


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


class Preparador:
    """Converte (com cache) e copia para site/img; devolve a entrada do índice."""

    def __init__(self, s):
        self.s, self.novas, self.feitas = s, 0, {}

    def __call__(self, f):
        if f["id"] in self.feitas:
            return self.feitas[f["id"]]
        chave = f"{f['id']}-{f.get('md5Checksum') or f.get('modifiedTime', '').replace(':', '')}"
        cdir = os.path.join(CACHE, chave)
        meta_p = os.path.join(cdir, "meta.json")
        if not os.path.exists(meta_p):
            try:
                os.makedirs(cdir, exist_ok=True)
                meta = processar(descarregar(self.s, f["id"]), f["id"], cdir)
                json.dump(meta, open(meta_p, "w"))
                self.novas += 1
            except Exception as e:
                print(f"  falhou {f['name']}: {type(e).__name__}", file=sys.stderr)
                return None
        meta = json.load(open(meta_p))
        for nome in list(meta["srcs"].values()) + [meta["jpg"]]:
            with open(os.path.join(cdir, nome), "rb") as a, open(os.path.join(OUT, nome), "wb") as b:
                b.write(a.read())
        out = {"id": f["id"], "nome": f["name"], **meta}
        self.feitas[f["id"]] = out
        return out


def titulos(nome_pasta):
    """«02 Quarto 1» → ordem 2, «Quarto 1», «Bedroom 1»; «Sala | Lounge» → EN manual."""
    m = re.match(r"^\s*(\d+)\s*[.\-–)]?\s*(.+)$", nome_pasta)
    ordem_manual, nome = (int(m.group(1)), m.group(2).strip()) if m else (None, nome_pasta.strip())
    if "|" in nome:
        pt, en = (x.strip() for x in nome.split("|", 1))
        return ordem_manual, pt, en or pt
    n = norm(nome)
    en = nome
    for k in sorted(EN, key=len, reverse=True):
        if n == k or n.startswith(k + " "):
            resto = nome.split()[len(k.split()):]
            en = " ".join([EN[k]] + resto)
            break
    return ordem_manual, nome, en


def ordem_divisao(ordem_manual, nome):
    if ordem_manual is not None:
        return (0, ordem_manual, chave_natural(nome))
    n = norm(nome)
    for i, k in enumerate(ORDEM):
        if n == k or n.startswith(k + " "):
            return (1, i, chave_natural(nome))
    return (2, 0, chave_natural(nome))


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", norm(s)).strip("-") or "divisao"


def e_principal(nome):
    n = norm(nome)
    return n.startswith("foto principal") or n.startswith("fotos principa")


def modo_divisoes(s, pasta_site, prep):
    hero, divisoes, slugs = [], [], set()
    pastas = [p for p in listar(s, pasta_site) if p.get("mimeType") == PASTA]
    for p in pastas:
        if p["name"].strip()[:1] in ("_", "#"):
            continue
        fotos = [x for x in (prep(f) for f in imagens(listar(s, p["id"]))) if x]
        if e_principal(p["name"]):
            hero = fotos
            print(f"  Foto Principal: {len(fotos)}")
            continue
        if not fotos:
            print(f"  {p['name']}: sem imagens (ignorada)")
            continue
        om, pt, en = titulos(p["name"])
        sl = slug(pt)
        while sl in slugs:
            sl += "-2"
        slugs.add(sl)
        divisoes.append({"slug": sl, "pt": pt, "en": en, "_ord": ordem_divisao(om, pt), "fotos": fotos})
        print(f"  {pt}: {len(fotos)}")
    divisoes.sort(key=lambda d: d.pop("_ord"))
    return hero, divisoes


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
            d.text((x + 6, y + th + 4), f"{n + i + 1:03d}  {f.get('_div', '')} {f['nome']}"[:46], fill=(30, 45, 36))
        folha.save(os.path.join(REV, f"folha-{n // por + 1}.jpg"), "JPEG", quality=80)


def main():
    drive = CFG.get("drive") or {}
    pasta = drive.get("pasta_fotos", "")
    s = sessao()
    if not s or not pasta:
        print("Fotografias: sem credenciais ou sem pasta configurada; o site usa a ilustração provisória.")
        return
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    prep = Preparador(s)
    raiz = listar(s, pasta)
    nome_site = norm(drive.get("pasta_site", "Site"))
    site = next((p for p in raiz if p.get("mimeType") == PASTA and norm(p["name"]) == nome_site), None)

    if site:
        print("Modo divisões (pasta «%s»)" % site["name"])
        hero, divisoes = modo_divisoes(s, site["id"], prep)
        todas = [dict(f, _div=d["pt"]) for d in divisoes for f in d["fotos"]]
        indice = {"modo": "divisoes", "hero": hero, "divisoes": divisoes}
    else:
        itens = imagens(raiz)
        print(f"Modo lista: {len(itens)} imagens na pasta principal")
        todas = [x for x in (prep(f) for f in itens) if x]
        indice = {"modo": "lista", "fotos": todas}

    for i, f in enumerate(todas, 1):
        f["n"] = i
    json.dump(indice, open(os.path.join(OUT, "fotos.json"), "w"), ensure_ascii=False)
    folhas(todas)
    print(f"{len(prep.feitas)} fotografias prontas ({prep.novas} novas)")


if __name__ == "__main__":
    main()
