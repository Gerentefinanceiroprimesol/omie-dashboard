#!/usr/bin/env python3
"""
Atualiza um dashboard HTML estático com dados de Estoque e Financeiro
puxados direto da API da Omie.

Como funciona:
- Lê as credenciais (APP_KEY / APP_SECRET) de variáveis de ambiente
  (nunca deixe a chave escrita no código).
- Chama os endpoints da Omie.
- Monta um arquivo index.html com os dados.
- O GitHub Actions roda esse script 2x/dia e publica o resultado no
  GitHub Pages.

Antes de rodar em produção:
1. Teste cada chamada no Portal do Desenvolvedor da Omie
   (https://developer.omie.com.br) com sua própria App Key/Secret
   para confirmar o nome exato do "call" e dos parâmetros — a Omie
   às vezes usa nomes diferentes dependendo do módulo contratado
   (ex: ListarPosicaoEstoque vs ConsultarEstoque).
2. Ajuste os endpoints/parâmetros abaixo conforme o retorno real.
"""

import os
import json
from datetime import datetime, timezone
import requests

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

APP_KEY = os.environ["OMIE_APP_KEY"]
APP_SECRET = os.environ["OMIE_APP_SECRET"]

BASE_URL = "https://app.omie.com.br/api/v1"

HEADERS = {"Content-Type": "application/json"}


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


# ---------------------------------------------------------------------------
# Financeiro
# ---------------------------------------------------------------------------

def buscar_contas_pagar(pagina: int = 1) -> dict:
    return chamar_omie(
        "financas/contapagar",
        "ListarContasPagar",
        {
            "pagina": pagina,
            "registros_por_pagina": 200,
            "apenas_importado_api": "N",
        },
    )


def buscar_contas_receber(pagina: int = 1) -> dict:
    return chamar_omie(
        "financas/contareceber",
        "ListarContasReceber",
        {
            "pagina": pagina,
            "registros_por_pagina": 200,
            "apenas_importado_api": "N",
        },
    )


def resumo_financeiro() -> dict:
    pagar = buscar_contas_pagar()
    receber = buscar_contas_receber()

    contas_pagar = pagar.get("conta_pagar_cadastro", [])
    contas_receber = receber.get("conta_receber_cadastro", [])

    total_pagar_aberto = sum(
        c.get("valor_documento", 0)
        for c in contas_pagar
        if c.get("status_titulo") in ("ABERTO", "ATRASADO")
    )
    total_receber_aberto = sum(
        c.get("valor_documento", 0)
        for c in contas_receber
        if c.get("status_titulo") in ("ABERTO", "ATRASADO")
    )

    return {
        "total_pagar_aberto": total_pagar_aberto,
        "total_receber_aberto": total_receber_aberto,
        "qtd_contas_pagar": len(contas_pagar),
        "qtd_contas_receber": len(contas_receber),
        "contas_pagar": contas_pagar[:20],   # amostra p/ tabela
        "contas_receber": contas_receber[:20],
    }


# ---------------------------------------------------------------------------
# Estoque
# ---------------------------------------------------------------------------

def resumo_estoque() -> dict:
    # ATENÇÃO: confirme o call correto no Portal do Desenvolvedor.
    # ListarPosicaoEstoque é o mais comum para posição atual de estoque.
    data = chamar_omie(
        "estoque/consulta",
        "ListarPosicaoEstoque",
        {
            "nPagina": 1,
            "nRegPorPagina": 200,
            "dDataPosicao": datetime.now().strftime("%d/%m/%Y"),
        },
    )
    produtos = data.get("produtos", [])
    qtd_itens = len(produtos)
    qtd_negativo = sum(1 for p in produtos if p.get("fisico", 0) < 0)
    qtd_baixo = sum(
        1 for p in produtos
        if 0 <= p.get("fisico", 0) <= p.get("estoqueMinimo", 0)
    )
    return {
        "qtd_itens": qtd_itens,
        "qtd_negativo": qtd_negativo,
        "qtd_baixo": qtd_baixo,
        "produtos": produtos[:30],  # amostra p/ tabela
    }


# ---------------------------------------------------------------------------
# Geração do HTML
# ---------------------------------------------------------------------------

def gerar_html(fin: dict, est: dict) -> str:
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    def fmt_moeda(v):
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    linhas_pagar = "".join(
        f"<tr><td>{c.get('fornecedor', {}).get('nome', '-') if isinstance(c.get('fornecedor'), dict) else '-'}</td>"
        f"<td>{c.get('data_vencimento','-')}</td>"
        f"<td>{fmt_moeda(c.get('valor_documento',0))}</td>"
        f"<td>{c.get('status_titulo','-')}</td></tr>"
        for c in fin["contas_pagar"]
    )
    linhas_receber = "".join(
        f"<tr><td>{c.get('cliente', {}).get('nome', '-') if isinstance(c.get('cliente'), dict) else '-'}</td>"
        f"<td>{c.get('data_vencimento','-')}</td>"
        f"<td>{fmt_moeda(c.get('valor_documento',0))}</td>"
        f"<td>{c.get('status_titulo','-')}</td></tr>"
        for c in fin["contas_receber"]
    )
    linhas_estoque = "".join(
        f"<tr><td>{p.get('descricao','-')}</td>"
        f"<td>{p.get('codigo','-')}</td>"
        f"<td>{p.get('fisico','-')}</td>"
        f"<td>{p.get('estoqueMinimo','-')}</td></tr>"
        for p in est["produtos"]
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dashboard Prime Sol — Financeiro & Estoque</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #0f172a; color: #e2e8f0; }}
  header {{ padding: 24px 32px; background: #1e293b; border-bottom: 1px solid #334155; }}
  h1 {{ margin: 0; font-size: 20px; }}
  .atualizado {{ color: #94a3b8; font-size: 13px; margin-top: 4px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; padding: 24px 32px; }}
  .card {{ background: #1e293b; border-radius: 10px; padding: 18px 22px; min-width: 180px; border: 1px solid #334155; }}
  .card .label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; }}
  .card .valor {{ font-size: 24px; font-weight: 700; margin-top: 6px; }}
  .card.alerta .valor {{ color: #f87171; }}
  section {{ padding: 8px 32px 32px; }}
  h2 {{ font-size: 16px; color: #cbd5e1; border-bottom: 1px solid #334155; padding-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 32px; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e293b; }}
  th {{ color: #94a3b8; font-weight: 600; }}
</style>
</head>
<body>
<header>
  <h1>Dashboard Prime Sol — Financeiro & Estoque</h1>
  <div class="atualizado">Última atualização: {agora}</div>
</header>

<div class="cards">
  <div class="card"><div class="label">Contas a pagar (aberto)</div><div class="valor">{fmt_moeda(fin['total_pagar_aberto'])}</div></div>
  <div class="card"><div class="label">Contas a receber (aberto)</div><div class="valor">{fmt_moeda(fin['total_receber_aberto'])}</div></div>
  <div class="card"><div class="label">Itens em estoque</div><div class="valor">{est['qtd_itens']}</div></div>
  <div class="card alerta"><div class="label">Estoque negativo</div><div class="valor">{est['qtd_negativo']}</div></div>
  <div class="card alerta"><div class="label">Estoque baixo</div><div class="valor">{est['qtd_baixo']}</div></div>
</div>

<section>
  <h2>Contas a Pagar (amostra)</h2>
  <table>
    <tr><th>Fornecedor</th><th>Vencimento</th><th>Valor</th><th>Status</th></tr>
    {linhas_pagar}
  </table>

  <h2>Contas a Receber (amostra)</h2>
  <table>
    <tr><th>Cliente</th><th>Vencimento</th><th>Valor</th><th>Status</th></tr>
    {linhas_receber}
  </table>

  <h2>Estoque (amostra)</h2>
  <table>
    <tr><th>Produto</th><th>Código</th><th>Físico</th><th>Estoque mínimo</th></tr>
    {linhas_estoque}
  </table>
</section>

</body>
</html>
"""


def main():
    fin = resumo_financeiro()
    est = resumo_estoque()
    html = gerar_html(fin, est)

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Dashboard gerado em public/index.html")


if __name__ == "__main__":
    main()
