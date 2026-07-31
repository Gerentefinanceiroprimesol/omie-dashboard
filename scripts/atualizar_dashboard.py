#!/usr/bin/env python3
"""
Atualiza um dashboard HTML com dados Financeiros (extrato de contas
bancárias + cartões de crédito) puxados direto da API da Omie.

A página gerada tem filtros INTERATIVOS (em JavaScript, no navegador):
- Quais contas/cartões exibir
- Período (data inicial e final)
- Apenas conciliados / apenas não conciliados / todos

Para isso, o script busca TODOS os lançamentos brutos de 01/01/2026 até
a data de hoje (a cada execução), e embute esses dados na própria página.
Os filtros só reorganizam o que já foi baixado — não fazem nova chamada
à Omie a cada clique.
"""

import os
import json as jsonlib
from datetime import datetime, timezone
import requests

APP_KEY = os.environ["OMIE_APP_KEY"]
APP_SECRET = os.environ["OMIE_APP_SECRET"]

BASE_URL = "https://app.omie.com.br/api/v1"
HEADERS = {"Content-Type": "application/json"}

# Período de busca: sempre de 01/01/2026 até o dia da execução
PERIODO_INICIAL = "01/01/2026"
PERIODO_FINAL = datetime.now().strftime("%d/%m/%Y")

# Todas as contas monitoradas (bancárias + cartões)
CONTAS = [
    {"id": "sicoob_matriz", "nome": "Sicoob Matriz", "tipo": "banco", "nCodCC": 11213271714},
    {"id": "itau_unibanco", "nome": "Itaú Unibanco", "tipo": "banco", "nCodCC": 11214288866},
    {"id": "santander", "nome": "Santander", "tipo": "banco", "nCodCC": 11236843385},
    {"id": "sicredi", "nome": "Sicredi", "tipo": "banco", "nCodCC": 11239054225},
    {"id": "bradesco", "nome": "Bradesco", "tipo": "banco", "nCodCC": 11431467344},
    {"id": "cartao_sicoob", "nome": "Cartão Sicoob", "tipo": "cartao", "nCodCC": 11574015480},
    {"id": "cartao_itau", "nome": "Cartão Itaú", "tipo": "cartao", "nCodCC": 11693512362},
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


def buscar_extrato(nCodCC: int) -> dict:
    """Busca o extrato completo (bruto, sem filtro) de uma conta no período.

    Retorna também o saldo anterior (antes do período inicial), necessário
    para calcular o saldo em datas onde ainda não há lançamento no período.
    """
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
    return {
        "saldoAnterior": data.get("nSaldoAnterior", 0),
        "movimentos": data.get("listaMovimentos", []) or [],
    }


def coletar_dados() -> list:
    """Coleta os dados brutos de todas as contas, sem aplicar nenhum filtro."""
    resultado = []
    for conta in CONTAS:
        extrato = buscar_extrato(conta["nCodCC"])
        lancamentos = [
            {
                "data": m.get("dDataLancamento", ""),
                "cliente": m.get("cDesCliente", "-") or "-",
                "tipoDoc": m.get("cTipoDocumento", "-") or "-",
                "valor": m.get("nValorDocumento", 0),
                "situacao": m.get("cSituacao", "-") or "-",
                "dataConciliacao": (m.get("dDataConciliacao") or "").strip(),
                "saldo": m.get("nSaldo", 0),
            }
            for m in extrato["movimentos"]
        ]
        resultado.append({
            "id": conta["id"],
            "nome": conta["nome"],
            "tipo": conta["tipo"],
            "saldoAnterior": extrato["saldoAnterior"],
            "lancamentos": lancamentos,
        })
    return resultado


def gerar_html(contas: list) -> str:
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    dados_json = jsonlib.dumps(contas, ensure_ascii=False)

    checkboxes_html = ""
    for grupo, titulo in (("banco", "Contas Bancárias"), ("cartao", "Cartões de Crédito")):
        itens = [c for c in contas if c["tipo"] == grupo]
        if not itens:
            continue
        checkboxes_html += f'<div class="grupo-check"><strong>{titulo}</strong>'
        for c in itens:
            checkboxes_html += (
                f'<label><input type="checkbox" class="chk-conta" value="{c["id"]}" checked> {c["nome"]}</label>'
            )
        checkboxes_html += "</div>"

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

  .filtros {{ padding: 20px 32px; background: #16213a; border-bottom: 1px solid #334155; display: flex; gap: 32px; flex-wrap: wrap; align-items: flex-start; }}
  .filtro-bloco {{ display: flex; flex-direction: column; gap: 8px; }}
  .filtro-bloco h3 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: #94a3b8; margin: 0 0 4px; }}
  .grupo-check {{ display: flex; flex-direction: column; gap: 4px; margin-bottom: 10px; font-size: 13px; }}
  .grupo-check strong {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
  .grupo-check label {{ display: flex; align-items: center; gap: 6px; cursor: pointer; }}
  .datas {{ display: flex; gap: 10px; align-items: center; font-size: 13px; }}
  .datas input {{ background: #0f172a; border: 1px solid #334155; color: #e2e8f0; border-radius: 6px; padding: 6px 8px; }}
  .radios {{ display: flex; flex-direction: column; gap: 6px; font-size: 13px; }}
  .radios label {{ display: flex; align-items: center; gap: 6px; cursor: pointer; }}
  #btnAplicar {{ background: #2563eb; color: white; border: none; border-radius: 8px; padding: 10px 18px; font-size: 13px; font-weight: 600; cursor: pointer; align-self: flex-end; }}
  #btnAplicar:hover {{ background: #1d4ed8; }}

  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; padding: 24px 32px 0; }}
  .card {{ background: #1e293b; border-radius: 10px; padding: 18px 22px; min-width: 200px; border: 1px solid #334155; }}
  .card .label {{ font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; margin-bottom: 8px; }}
  .linha-valor {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-top: 2px; }}
  .mini-label {{ font-size: 11px; color: #64748b; }}
  .valor {{ font-size: 15px; font-weight: 700; }}
  .valor.entrada {{ color: #4ade80; }}
  .valor.saida {{ color: #f87171; }}
  .valor.saldo {{ color: #e2e8f0; font-size: 17px; }}
  .card .qtd {{ font-size: 11px; color: #64748b; margin-top: 8px; }}

  .saldos {{ display: flex; gap: 16px; flex-wrap: wrap; padding: 24px 32px 0; }}
  .box-saldo {{ background: #0f2942; border: 1px solid #1d4d7a; border-radius: 10px; padding: 16px 22px; min-width: 200px; }}
  .box-saldo .label {{ font-size: 12px; color: #7dd3fc; text-transform: uppercase; letter-spacing: .05em; }}
  .box-saldo .valor-saldo {{ font-size: 24px; font-weight: 800; margin-top: 6px; color: #e0f2fe; }}
  .box-saldo .ref {{ font-size: 11px; color: #64748b; margin-top: 4px; }}

  section {{ padding: 8px 32px 32px; }}
  h2 {{ font-size: 16px; color: #cbd5e1; border-bottom: 1px solid #334155; padding-bottom: 8px; margin-top: 28px; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #1e293b; }}
  th {{ color: #94a3b8; font-weight: 600; }}
  .vazio {{ color: #64748b; font-size: 13px; padding: 12px 0; }}
</style>
</head>
<body>
<header>
  <h1>Dashboard Prime Sol — Financeiro</h1>
  <div class="atualizado">Última atualização: {agora} · Dados brutos coletados de {PERIODO_INICIAL} até {PERIODO_FINAL}</div>
</header>

<div class="filtros">
  <div class="filtro-bloco">
    <h3>Contas / Cartões</h3>
    {checkboxes_html}
  </div>

  <div class="filtro-bloco">
    <h3>Período (data de pagamento/recebimento)</h3>
    <div class="datas">
      <label>De: <input type="date" id="dataDe" value="2026-01-01"></label>
      <label>Até: <input type="date" id="dataAte"></label>
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Conciliação</h3>
    <div class="radios">
      <label><input type="radio" name="conciliacao" value="todos"> Todos</label>
      <label><input type="radio" name="conciliacao" value="conciliados" checked> Apenas conciliados</label>
      <label><input type="radio" name="conciliacao" value="nao_conciliados"> Apenas não conciliados</label>
    </div>
  </div>

  <button id="btnAplicar">Aplicar filtros</button>
</div>

<div class="saldos" id="saldosContainer"></div>

<div class="cards" id="cardsContainer"></div>

<section id="secoesContainer"></section>

<script>
const DADOS = {dados_json};

function paraDataISO(brDate) {{
  if (!brDate) return null;
  const partes = brDate.split('/');
  if (partes.length !== 3) return null;
  return `${{partes[2]}}-${{partes[1]}}-${{partes[0]}}`;
}}

function fmtMoeda(v) {{
  return 'R$ ' + v.toLocaleString('pt-BR', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
}}

function fmtDataBR(iso) {{
  if (!iso) return '';
  const [ano, mes, dia] = iso.split('-');
  return `${{dia}}/${{mes}}/${{ano}}`;
}}

/**
 * Calcula o saldo real da conta na data informada (dataAteISO), usando
 * TODOS os lançamentos da conta (não filtrados por conciliação), já que
 * o saldo bancário existe independente de já ter sido conciliado ou não.
 */
function calcularSaldoNaData(conta, dataAteISO) {{
  const ordenados = [...conta.lancamentos]
    .map(l => ({{...l, _iso: paraDataISO(l.data)}}))
    .filter(l => l._iso)
    .sort((a, b) => a._iso.localeCompare(b._iso));

  let saldo = conta.saldoAnterior;
  for (const l of ordenados) {{
    if (dataAteISO && l._iso > dataAteISO) break;
    saldo = l.saldo;
  }}
  return saldo;
}}

function contasSelecionadas() {{
  return Array.from(document.querySelectorAll('.chk-conta:checked')).map(el => el.value);
}}

function renderizar() {{
  const idsSelecionados = contasSelecionadas();
  const dataDe = document.getElementById('dataDe').value;
  const dataAte = document.getElementById('dataAte').value;
  const modoConciliacao = document.querySelector('input[name="conciliacao"]:checked').value;

  const cardsContainer = document.getElementById('cardsContainer');
  const saldosContainer = document.getElementById('saldosContainer');
  const secoesContainer = document.getElementById('secoesContainer');
  cardsContainer.innerHTML = '';
  saldosContainer.innerHTML = '';
  secoesContainer.innerHTML = '';

  const rotuloSaldo = (dataDe === dataAte)
    ? `Saldo em ${{fmtDataBR(dataAte)}}`
    : `Saldo ao final do período (${{fmtDataBR(dataAte)}})`;

  DADOS.forEach(conta => {{
    if (!idsSelecionados.includes(conta.id)) return;

    const saldoConta = calcularSaldoNaData(conta, dataAte);
    const boxSaldo = document.createElement('div');
    boxSaldo.className = 'box-saldo';
    boxSaldo.innerHTML = `
      <div class="label">${{conta.nome}}</div>
      <div class="valor-saldo">${{fmtMoeda(saldoConta)}}</div>
      <div class="ref">${{rotuloSaldo}}</div>
    `;
    saldosContainer.appendChild(boxSaldo);
  }});

  DADOS.forEach(conta => {{
    if (!idsSelecionados.includes(conta.id)) return;

    const filtrados = conta.lancamentos.filter(l => {{
      const dataISO = paraDataISO(l.data);
      if (dataDe && dataISO && dataISO < dataDe) return false;
      if (dataAte && dataISO && dataISO > dataAte) return false;

      if (modoConciliacao === 'conciliados' && !l.dataConciliacao) return false;
      if (modoConciliacao === 'nao_conciliados' && l.dataConciliacao) return false;

      return true;
    }});

    const entradas = filtrados.filter(l => l.valor > 0).reduce((s, l) => s + l.valor, 0);
    const saidas = filtrados.filter(l => l.valor < 0).reduce((s, l) => s + l.valor, 0);
    const saldo = entradas + saidas;

    const tipoLabel = conta.tipo === 'banco' ? 'Conta Bancária' : 'Cartão de Crédito';

    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="label">${{conta.nome}} — ${{tipoLabel}}</div>
      <div class="linha-valor"><span class="mini-label">Entradas</span><span class="valor entrada">${{fmtMoeda(entradas)}}</span></div>
      <div class="linha-valor"><span class="mini-label">Saídas</span><span class="valor saida">${{fmtMoeda(saidas)}}</span></div>
      <div class="linha-valor"><span class="mini-label">Saldo</span><span class="valor saldo">${{fmtMoeda(saldo)}}</span></div>
      <div class="qtd">${{filtrados.length}} lançamento(s)</div>
    `;
    cardsContainer.appendChild(card);

    const amostra = filtrados.slice(0, 30);
    const linhasHtml = amostra.map(l => `
      <tr>
        <td>${{l.data}}</td>
        <td>${{l.cliente}}</td>
        <td>${{l.tipoDoc}}</td>
        <td>${{fmtMoeda(l.valor)}}</td>
        <td>${{l.situacao}}</td>
        <td>${{l.dataConciliacao || '-'}}</td>
      </tr>
    `).join('');

    const secao = document.createElement('div');
    secao.innerHTML = `
      <h2>${{conta.nome}} — ${{tipoLabel}} (amostra de até 30 lançamentos)</h2>
      ${{amostra.length ? `
        <table>
          <tr><th>Data Pagamento/Recebimento</th><th>Cliente/Fornecedor</th><th>Tipo Doc.</th><th>Valor</th><th>Situação</th><th>Data Conciliação</th></tr>
          ${{linhasHtml}}
        </table>
      ` : '<div class="vazio">Nenhum lançamento encontrado com os filtros atuais.</div>'}}
    `;
    secoesContainer.appendChild(secao);
  }});
}}

// Data "até" default = hoje
document.getElementById('dataAte').value = new Date().toISOString().split('T')[0];

document.getElementById('btnAplicar').addEventListener('click', renderizar);
renderizar();
</script>

</body>
</html>
"""


def main():
    contas = coletar_dados()
    html = gerar_html(contas)

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Dashboard gerado em public/index.html")


if __name__ == "__main__":
    main()
