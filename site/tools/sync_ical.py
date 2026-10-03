#!/usr/bin/env python3
"""Sincroniza a disponibilidade da Casa da Madrinha a partir dos calendários iCal.

Lê os URLs das variáveis de ambiente ICAL_AIRBNB e ICAL_BOOKING (secrets do
GitHub; nunca no repositório nem nos logs) e escreve site/data/disponibilidade.json
com os intervalos ocupados, já fundidos, sem nomes, notas nem URLs.

O ficheiro é gerado só no momento da publicação e não é guardado no repositório.
Uso: python site/tools/sync_ical.py
"""
import json, os, re, sys, urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "disponibilidade.json")
FONTES = {"airbnb": "ICAL_AIRBNB", "booking": "ICAL_BOOKING"}
HORIZONTE_DIAS = 540  # cerca de 18 meses


def ler(url):
    req = urllib.request.Request(url, headers={"User-Agent": "casa-da-madrinha-sync/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if getattr(r, "status", None) not in (None, 200):
            raise RuntimeError(f"HTTP {r.status}")
        return r.read().decode("utf-8", "replace")


def data_ical(valor):
    v = valor.strip()[:8]
    return date(int(v[:4]), int(v[4:6]), int(v[6:8]))


def eventos(texto):
    # desdobrar linhas continuadas (RFC 5545)
    texto = re.sub(r"\r?\n[ \t]", "", texto)
    out, cur = [], None
    for linha in texto.splitlines():
        if linha.startswith("BEGIN:VEVENT"):
            cur = {}
        elif linha.startswith("END:VEVENT"):
            if cur and "ini" in cur:
                fim = cur.get("fim") or cur["ini"] + timedelta(days=1)
                if fim > cur["ini"]:
                    out.append((cur["ini"], fim))
            cur = None
        elif cur is not None:
            chave, _, valor = linha.partition(":")
            nome = chave.split(";")[0].upper()
            if nome == "DTSTART":
                cur["ini"] = data_ical(valor)
            elif nome == "DTEND":
                cur["fim"] = data_ical(valor)
    return out


def fundir(intervalos):
    res = []
    for ini, fim in sorted(intervalos):
        if res and ini <= res[-1][1]:
            res[-1] = (res[-1][0], max(res[-1][1], fim))
        else:
            res.append((ini, fim))
    return res


def main():
    hoje = date.today()
    limite = hoje + timedelta(days=HORIZONTE_DIAS)
    todos, estado = [], {}
    for nome, var in FONTES.items():
        url = os.environ.get(var, "").strip()
        if not url:
            estado[nome] = "sem_fonte"
            continue
        try:
            evs = eventos(ler(url))
            todos += evs
            estado[nome] = "ok"
            print(f"{nome}: {len(evs)} eventos")
        except Exception as e:  # nunca imprimir o URL
            estado[nome] = "erro"
            print(f"{nome}: falhou ({type(e).__name__})", file=sys.stderr)
    ocupado = [(max(i, hoje), min(f, limite)) for i, f in fundir(todos) if f > hoje and i < limite]
    dados = {
        "atualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fontes": estado,
        "nota": "Disponibilidade conhecida à data da última sincronização; a confirmar com o proprietário.",
        # intervalos [inicio, fim): fim é o dia de saída, que fica livre para nova entrada
        "ocupado": [{"inicio": i.isoformat(), "fim": f.isoformat()} for i, f in ocupado],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=1)
    print(f"{len(ocupado)} intervalos ocupados; fontes: {estado}")


if __name__ == "__main__":
    main()
