#!/usr/bin/env python3
"""
Atualiza um dashboard HTML estático com dados Financeiros (extrato de
contas bancárias conciliadas + extrato de cartões de crédito) puxados
direto da API da Omie.
"""

import os
from datetime import datetime, timezone
import requests

APP_KEY = os.environ["OMIE_APP_KEY"]
APP_SECRET = os.environ["OMIE_APP_SECRET"]

BASE_URL = "https://app.omie.com.br/api/v1"
HEADERS = {"Content-Type": "application/json"}

# Período de análise
PERIODO_INICIAL = "01/01/2026"
PERIODO_FINAL = "30/06/2026"

# Contas bancárias "de verdade" — só entram lançamentos CONCILIADOS
CONTAS_BANCARIAS = [
    {"nome": "Sicoob Matriz", "nCodCC": 11213271714},
    {"nome": "Itaú Unibanco", "nCodCC": 11214288866},
    {"nome": "Santander", "nCodCC": 11236843385},
    {"nome": "Sicredi", "nCodCC": 11239054225},
    {"nome": "Bradesco", "nCodCC": 11431467344},
]

# Cartões de crédito — TODOS os lançamentos do período, sem filtro de conciliação
CONTAS_CARTAO = [
    {"nome": "Cartão Sicoob", "nCodCC": 11574015480},
    {"nome": "Cartão Itaú", "nCodCC": 11693512362},
]


def chamar_omie(modulo: str, call: str, param: dict) -> dict:
    """Faz uma chamada genérica à API da Omie."""
    url = f"{BASE_URL}/{modulo}/"
    payload = {
        "call": call,
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "param": [param],
    }
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "faultstring" in data:
        raise RuntimeError(f"Erro Omie ({call}): {data['faultstring']}")
    return data


def buscar_extrato(nCodCC: int) -> list:
    """Busca o extrato completo de uma conta no período definido."""
    data = chamar_omie(
        "financas/extrato",
        "ListarExtrato",
        {
            "nCodCC": nCodCC,
            "cCodIntCC": "",
            "dPeriodoInicial": PERIODO_INICIAL,
            "dPeriodoFinal": PERIODO_FINAL,
        },
    )
    return data.get("listaMovimentos", []) or []


def resumo_financeiro() -> dict:
    contas_resultado = []

    # --- Contas bancárias: só lançamentos conciliados ---
    for conta in CONTAS_BANCARIAS:
        movimentos = buscar_extrato(conta["nCodCC"])
        conciliados = [
            m for m in movimentos
            if (m.get("dDataConciliacao") or "").strip()
        ]
        total = sum(m.get("nValorDocumento", 0) for m in conciliados)
        contas_resultado.append({
            "nome": conta["nome"],
            "tipo": "Conta Bancária (conciliado)",
            "qtd": len(conciliados),
            "total": total,
            "lancamentos": conciliados[:15],
        })

    # --- Cartões de crédito: todos os lançamentos do período ---
    for conta in CONTAS_CARTAO:
        movimentos = buscar_extrato(conta["nCodCC"])
        total = sum(m.get("nValorDocumento", 0) for m in movimentos)
        contas_resultado.append({
            "nome": conta["nome"],
            "tipo": "Cartão de Crédito",
            "qtd": len(movimentos),
            "total": total,
            "lancamentos": movimentos[:15],
        })

    return {"contas": contas_resultado}


def gerar_html(fin: dict) -> str:
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    def fmt_moeda(v):
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    cards_html = "".join(
        f'<div class="card"><div class="label">{c["nome"]} — {c["tipo"]}</div>'
        f'<div class="valor">{fmt_moeda(c["total"])}</div>'
        f'<div class="qtd">{c["qtd"]} lançamento(s)</div></div>'
        for c in fin["contas"]
    )

    secoes_html = ""
    for c in fin["contas"]:
        linhas = "".join(
            f"<tr><td>{m.get('dDataLancamento','-')}</td>"
            f"<td>{m.get('cDesCliente','-')}</td>"
            f"<td>{m.get('cTipoDocumento','-')}</td>"
            f"<td>{fmt_moeda(m.get('nValorDocumento',0))}</td>"
            f"<td>{m.get('cSituacao','-')}</td>"
            f"<td>{m.get('dDataConciliacao','-') or '-'}</td></tr>"
            for m in c["lancamentos"]
        )
        secoes_html += f"""
  <h2>{c['nome']} — {c['tipo']} (amostra)</h2>
  <table>
    <tr><th>Data</th><th>Cliente/Fornecedor</th><th>Tipo Doc.</th><th>Valor</th><th>Situação</th><th>Data Conciliação</th></tr>
    {linhas if linhas else '<tr><td colspan="6">Nenhum lançamento no período.</td></tr>'}
  </table>
"""

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dashboard Prime Sol — Financeiro</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }}
  header {{ padding: 24px 32px; background: #1e293b; border-bottom: 1px solid #334155; }}
  h1 {{ margin: 0; font-size: 20px; }}
  .atualizado {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}
  .periodo {{ color: #64748b; font-size: 12px; margin-top: 2px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; padding: 24px 32px; }}
  .card {{ background: #1e293b; border-radius: 10px; padding: 18px 22px; min-width: 200px; border: 1px solid #334155; }}
  .card .label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; }}
  .card .valor {{ font-size: 22px; font-weight: 700; margin-top: 6px; }}
  .card .qtd {{ font-size: 11px; color: #64748b; margin-top: 4px; }}
  section {{ padding: 8px 32px 32px; }}
  h2 {{ font-size: 16px; color: #cbd5e1; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-top: 28px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e293b; }}
  th {{ color: #94a3b8; font-weight: 600; }}
</style>
</head>
<body>
<header>
  <h1>Dashboard Prime Sol — Financeiro</h1>
  <div class="atualizado">Última atualização: {agora}</div>
  <div class="periodo">Período: {PERIODO_INICIAL} a {PERIODO_FINAL}</div>
</header>

<div class="cards">
  {cards_html}
</div>

<section>
  {secoes_html}
</section>

</body>
</html>
"""


def main():
    fin = resumo_financeiro()
    html = gerar_html(fin)

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Dashboard gerado em public/index.html")


if __name__ == "__main__":
    main()
