#!/usr/bin/env python3
"""
Atualiza um dashboard HTML com dados Financeiros (extrato de contas
bancárias + cartões de crédito) puxados direto da API da Omie.

A página gerada tem filtros INTERATIVOS (em JavaScript, no navegador):
- Painel superior: quais contas/cartões exibir, período, conciliação,
  cliente/fornecedor, valor, situação
- Filtro estilo Excel em cada coluna das tabelas (clique na seta do
  cabeçalho para escolher valores específicos)

Para isso, o script busca TODOS os lançamentos brutos de 01/01/2026 até
a data de hoje (a cada execução, mês a mês para evitar o limite de 100
registros por chamada da Omie), e embute esses dados na própria página.
Os filtros só reorganizam o que já foi baixado — não fazem nova chamada
à Omie a cada clique.
"""

import os
import json as jsonlib
from datetime import datetime, timezone, timedelta
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


def gerar_meses(data_inicial: str, data_final: str) -> list:
    """Gera uma lista de (inicio, fim) por mês, no formato dd/mm/aaaa,
    para evitar que uma única chamada ultrapasse o limite de registros
    da Omie (a API não expõe paginação para o extrato)."""
    d_ini = datetime.strptime(data_inicial, "%d/%m/%Y")
    d_fim = datetime.strptime(data_final, "%d/%m/%Y")

    intervalos = []
    atual = d_ini.replace(day=1)
    while atual <= d_fim:
        if atual.month == 12:
            proximo_mes = atual.replace(year=atual.year + 1, month=1, day=1)
        else:
            proximo_mes = atual.replace(month=atual.month + 1, day=1)
        fim_mes = min(proximo_mes - timedelta(days=1), d_fim)
        inicio_intervalo = max(atual, d_ini)
        intervalos.append((
            inicio_intervalo.strftime("%d/%m/%Y"),
            fim_mes.strftime("%d/%m/%Y"),
        ))
        atual = proximo_mes
    return intervalos


def buscar_extrato(nCodCC: int) -> dict:
    """Busca o extrato completo de uma conta, mês a mês, para evitar
    truncamento pelo limite de 100 registros por chamada da Omie.

    Retorna também o saldo anterior ao período total (necessário para
    calcular o saldo em datas onde ainda não há lançamento no período).
    """
    meses = gerar_meses(PERIODO_INICIAL, PERIODO_FINAL)
    todos_movimentos = []
    saldo_anterior_total = None

    for i, (ini, fim) in enumerate(meses):
        data = chamar_omie(
            "financas/extrato",
            "ListarExtrato",
            {
                "nCodCC": nCodCC,
                "cCodIntCC": "",
                "dPeriodoInicial": ini,
                "dPeriodoFinal": fim,
            },
        )
        if i == 0:
            saldo_anterior_total = data.get("nSaldoAnterior", 0)
        todos_movimentos.extend(data.get("listaMovimentos", []) or [])

    return {
        "saldoAnterior": saldo_anterior_total or 0,
        "movimentos": todos_movimentos,
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
  :root {{
    --bg: #0b1220;
    --bg-panel: #131c2e;
    --bg-card: #17223a;
    --border: #263248;
    --text: #e7ecf5;
    --text-muted: #8b96ab;
    --text-faint: #5b6780;
    --accent: #3b82f6;
    --accent-hover: #2563eb;
    --green: #4ade80;
    --red: #f87171;
    --cyan: #7dd3fc;
    --radius: 12px;
    --radius-sm: 8px;
    --space: 8px;
  }}

  * {{ box-sizing: border-box; }}

  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.4; }}

  header {{ padding: 24px 32px; background: var(--bg-panel); border-bottom: 1px solid var(--border); }}
  h1 {{ margin: 0; font-size: 19px; font-weight: 700; letter-spacing: -0.01em; }}
  .atualizado {{ color: var(--text-muted); font-size: 12.5px; margin-top: 6px; }}

  .filtros {{
    padding: 20px 32px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, auto));
    gap: 24px;
    align-items: start;
  }}
  .filtro-bloco {{ display: flex; flex-direction: column; gap: 10px; min-height: 62px; }}
  .filtro-bloco h3 {{
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    color: var(--text-faint); margin: 0;
  }}
  .grupo-check {{ display: flex; flex-direction: column; gap: 5px; margin-bottom: 8px; font-size: 13px; }}
  .grupo-check:last-child {{ margin-bottom: 0; }}
  .grupo-check strong {{ font-size: 10.5px; color: var(--text-faint); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 2px; }}
  .grupo-check label {{ display: flex; align-items: center; gap: 8px; cursor: pointer; color: var(--text); }}
  .grupo-check input[type="checkbox"] {{ accent-color: var(--accent); width: 14px; height: 14px; }}

  .datas {{ display: flex; gap: 12px; align-items: center; font-size: 12.5px; flex-wrap: wrap; }}
  .datas label {{ display: flex; align-items: center; gap: 6px; color: var(--text-muted); }}
  .datas input, .input-texto, .input-valor {{
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: var(--radius-sm); padding: 7px 9px; font-size: 12.5px;
  }}
  .datas input:focus, .input-texto:focus, .input-valor:focus {{ outline: none; border-color: var(--accent); }}
  .input-texto {{ width: 180px; }}
  .input-valor {{ width: 88px; }}

  .radios {{ display: flex; flex-direction: column; gap: 7px; font-size: 13px; }}
  .radios label {{ display: flex; align-items: center; gap: 8px; cursor: pointer; }}
  .radios input[type="radio"] {{ accent-color: var(--accent); width: 14px; height: 14px; }}

  #btnAplicar {{
    background: var(--accent); color: white; border: none; border-radius: var(--radius-sm);
    padding: 0 22px; height: 40px; font-size: 13px; font-weight: 600; cursor: pointer;
    align-self: end; transition: background .15s ease;
  }}
  #btnAplicar:hover {{ background: var(--accent-hover); }}

  .saldos, .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 14px;
    padding: 20px 32px 0;
  }}

  .box-saldo {{
    background: linear-gradient(180deg, #0f2942 0%, #0d2338 100%);
    border: 1px solid #1d4d7a;
    border-radius: var(--radius);
    padding: 16px 18px;
  }}
  .box-saldo .label {{ font-size: 11px; font-weight: 700; color: var(--cyan); text-transform: uppercase; letter-spacing: .05em; }}
  .box-saldo .valor-saldo {{ font-size: 22px; font-weight: 800; margin-top: 8px; color: #f0f9ff; letter-spacing: -0.01em; }}
  .box-saldo .ref {{ font-size: 11px; color: var(--text-faint); margin-top: 6px; }}

  .card {{
    background: var(--bg-card); border-radius: var(--radius); padding: 16px 18px;
    border: 1px solid var(--border); display: flex; flex-direction: column;
  }}
  .card .label {{
    font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;
    letter-spacing: .05em; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }}
  .linha-valor {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 2px 0; }}
  .mini-label {{ font-size: 11.5px; color: var(--text-faint); }}
  .valor {{ font-size: 14px; font-weight: 700; }}
  .valor.entrada {{ color: var(--green); }}
  .valor.saida {{ color: var(--red); }}
  .valor.saldo {{ color: var(--text); font-size: 16px; }}
  .card .qtd {{ font-size: 11px; color: var(--text-faint); margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border); }}

  section {{ padding: 12px 32px 40px; }}
  h2 {{
    font-size: 14.5px; font-weight: 700; color: var(--text); border-bottom: 1px solid var(--border);
    padding-bottom: 10px; margin: 32px 0 14px;
  }}
  section > div:first-child h2 {{ margin-top: 8px; }}

  table {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; font-size: 12.5px; background: var(--bg-panel); border-radius: var(--radius-sm); overflow: hidden; }}
  th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--text-muted); font-weight: 600; white-space: nowrap; font-size: 11.5px; text-transform: uppercase; letter-spacing: .03em; }}
  tr:last-child td {{ border-bottom: none; }}
  .vazio {{ color: var(--text-faint); font-size: 13px; padding: 16px 0; }}

  .btn-filtro-col {{ background: none; border: none; color: var(--text-faint); cursor: pointer; font-size: 11px; padding: 2px 5px; border-radius: 4px; margin-left: 3px; vertical-align: middle; }}
  .btn-filtro-col:hover {{ background: var(--border); color: var(--text); }}
  .btn-filtro-col.ativo {{ color: var(--green); font-weight: 700; }}

  .dropdown-filtro {{ position: fixed; background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px; z-index: 1000; width: 240px; box-shadow: 0 12px 32px rgba(0,0,0,0.5); }}
  .dropdown-busca {{ width: 100%; box-sizing: border-box; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 6px 8px; font-size: 12px; margin-bottom: 8px; }}
  .dropdown-busca:focus {{ outline: none; border-color: var(--accent); }}
  .dropdown-acoes {{ display: flex; justify-content: space-between; margin-bottom: 6px; }}
  .dropdown-acoes button {{ background: none; border: none; color: var(--cyan); font-size: 11px; cursor: pointer; padding: 2px 4px; }}
  .dropdown-acoes button:hover {{ text-decoration: underline; }}
  .dropdown-lista {{ max-height: 220px; overflow-y: auto; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 6px 0; margin-bottom: 8px; }}
  .dropdown-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 4px 3px; cursor: pointer; border-radius: 4px; }}
  .dropdown-item:hover {{ background: var(--bg); }}
  .dropdown-item input {{ accent-color: var(--accent); }}
  .dropdown-ok {{ width: 100%; background: var(--accent); color: white; border: none; border-radius: 6px; padding: 7px; font-size: 12px; font-weight: 600; cursor: pointer; }}
  .dropdown-ok:hover {{ background: var(--accent-hover); }}
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

  <div class="filtro-bloco">
    <h3>Data de Conciliação</h3>
    <div class="datas">
      <label>De: <input type="date" id="conciliDe"></label>
      <label>Até: <input type="date" id="conciliAte"></label>
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Cliente / Fornecedor</h3>
    <input type="text" id="filtroCliente" placeholder="Buscar por nome..." class="input-texto">
  </div>

  <div class="filtro-bloco">
    <h3>Valor</h3>
    <div class="datas">
      <label>Mín: <input type="number" id="valorMin" step="0.01" placeholder="-9999" class="input-valor"></label>
      <label>Máx: <input type="number" id="valorMax" step="0.01" placeholder="9999" class="input-valor"></label>
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Situação</h3>
    <input type="text" id="filtroSituacao" placeholder="Ex: Conciliado, Pago..." class="input-texto">
  </div>

  <button id="btnAplicar">Aplicar filtros</button>
</div>

<div class="saldos" id="saldosContainer"></div>

<div class="cards" id="cardsContainer"></div>

<section id="secoesContainer"></section>

<script>
const DADOS = {dados_json};

// Definição das colunas da tabela, usadas tanto para montar o cabeçalho
// quanto para o filtro estilo Excel de cada coluna.
const COLUNAS = [
  {{ key: 'data', label: 'Data Pagamento/Recebimento', get: l => l.data }},
  {{ key: 'cliente', label: 'Cliente/Fornecedor', get: l => l.cliente }},
  {{ key: 'tipoDoc', label: 'Tipo Doc.', get: l => l.tipoDoc }},
  {{ key: 'valor', label: 'Valor', get: l => fmtMoeda(l.valor) }},
  {{ key: 'situacao', label: 'Situação', get: l => l.situacao }},
  {{ key: 'dataConciliacao', label: 'Data Conciliação', get: l => l.dataConciliacao || '-' }},
];

// Estado dos filtros de coluna (estilo Excel): chave "contaId::coluna" -> Set de valores permitidos.
// Ausência da chave = nenhum filtro ativo nessa coluna (mostra tudo).
let colFiltros = {{}};
// Guarda, a cada renderização, a lista já filtrada pelo painel superior (antes do filtro de coluna),
// usada para montar as opções disponíveis em cada dropdown.
let baseFiltradosPorConta = {{}};

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

function aplicarFiltrosColuna(contaId, lista) {{
  return lista.filter(l => {{
    return COLUNAS.every(col => {{
      const chave = `${{contaId}}::${{col.key}}`;
      const permitidos = colFiltros[chave];
      if (!permitidos) return true;
      return permitidos.has(col.get(l));
    }});
  }});
}}

function fecharDropdowns() {{
  document.querySelectorAll('.dropdown-filtro').forEach(d => d.remove());
}}

document.addEventListener('click', (e) => {{
  if (!e.target.closest('.dropdown-filtro') && !e.target.closest('.btn-filtro-col')) {{
    fecharDropdowns();
  }}
}});

function abrirDropdownColuna(contaId, col, btnEl) {{
  fecharDropdowns();
  const chave = `${{contaId}}::${{col.key}}`;
  const selecionadosAtuais = colFiltros[chave];
  const baseLista = baseFiltradosPorConta[contaId] || [];
  const valoresUnicos = [...new Set(baseLista.map(col.get))].sort();

  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown-filtro';
  const rect = btnEl.getBoundingClientRect();
  dropdown.style.top = (rect.bottom + 4) + 'px';
  dropdown.style.left = Math.min(rect.left, window.innerWidth - 260) + 'px';

  dropdown.innerHTML = `
    <input type="text" class="dropdown-busca" placeholder="Buscar...">
    <div class="dropdown-acoes">
      <button type="button" class="dropdown-todos">Selecionar tudo</button>
      <button type="button" class="dropdown-nenhum">Limpar</button>
    </div>
    <div class="dropdown-lista"></div>
    <button type="button" class="dropdown-ok">Aplicar</button>
  `;

  const lista = dropdown.querySelector('.dropdown-lista');

  function renderLista(filtroTexto) {{
    lista.innerHTML = '';
    valoresUnicos
      .filter(v => !filtroTexto || (v || '').toLowerCase().includes(filtroTexto.toLowerCase()))
      .forEach(v => {{
        const marcado = !selecionadosAtuais || selecionadosAtuais.has(v);
        const item = document.createElement('label');
        item.className = 'dropdown-item';
        const valorEscapado = (v || '').replace(/"/g, '&quot;');
        item.innerHTML = `<input type="checkbox" value="${{valorEscapado}}" ${{marcado ? 'checked' : ''}}> <span>${{v || '(vazio)'}}</span>`;
        lista.appendChild(item);
      }});
  }}
  renderLista('');

  dropdown.querySelector('.dropdown-busca').addEventListener('input', (ev) => renderLista(ev.target.value));
  dropdown.querySelector('.dropdown-todos').addEventListener('click', () => {{
    dropdown.querySelectorAll('.dropdown-item input').forEach(chk => chk.checked = true);
  }});
  dropdown.querySelector('.dropdown-nenhum').addEventListener('click', () => {{
    dropdown.querySelectorAll('.dropdown-item input').forEach(chk => chk.checked = false);
  }});
  dropdown.querySelector('.dropdown-ok').addEventListener('click', () => {{
    const marcados = Array.from(dropdown.querySelectorAll('.dropdown-item input:checked')).map(c => c.value);
    if (marcados.length === valoresUnicos.length) {{
      delete colFiltros[chave];
    }} else {{
      colFiltros[chave] = new Set(marcados);
    }}
    fecharDropdowns();
    renderizar();
  }});

  document.body.appendChild(dropdown);
}}

function renderizar() {{
  const idsSelecionados = contasSelecionadas();
  const dataDe = document.getElementById('dataDe').value;
  const dataAte = document.getElementById('dataAte').value;
  const modoConciliacao = document.querySelector('input[name="conciliacao"]:checked').value;
  const conciliDe = document.getElementById('conciliDe').value;
  const conciliAte = document.getElementById('conciliAte').value;
  const textoCliente = document.getElementById('filtroCliente').value.trim().toLowerCase();
  const textoSituacao = document.getElementById('filtroSituacao').value.trim().toLowerCase();
  const valorMinStr = document.getElementById('valorMin').value;
  const valorMaxStr = document.getElementById('valorMax').value;
  const valorMin = valorMinStr === '' ? null : parseFloat(valorMinStr);
  const valorMax = valorMaxStr === '' ? null : parseFloat(valorMaxStr);

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

    const baseFiltrados = conta.lancamentos.filter(l => {{
      const dataISO = paraDataISO(l.data);
      if (dataDe && dataISO && dataISO < dataDe) return false;
      if (dataAte && dataISO && dataISO > dataAte) return false;

      if (modoConciliacao === 'conciliados' && !l.dataConciliacao) return false;
      if (modoConciliacao === 'nao_conciliados' && l.dataConciliacao) return false;

      if (conciliDe || conciliAte) {{
        const conciliISO = paraDataISO(l.dataConciliacao);
        if (!conciliISO) return false;
        if (conciliDe && conciliISO < conciliDe) return false;
        if (conciliAte && conciliISO > conciliAte) return false;
      }}

      if (textoCliente && !(l.cliente || '').toLowerCase().includes(textoCliente)) return false;
      if (textoSituacao && !(l.situacao || '').toLowerCase().includes(textoSituacao)) return false;

      if (valorMin !== null && l.valor < valorMin) return false;
      if (valorMax !== null && l.valor > valorMax) return false;

      return true;
    }});

    baseFiltradosPorConta[conta.id] = baseFiltrados;
    const filtrados = aplicarFiltrosColuna(conta.id, baseFiltrados);

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

    const headerHtml = COLUNAS.map(col => {{
      const chave = `${{conta.id}}::${{col.key}}`;
      const ativo = colFiltros[chave] ? 'ativo' : '';
      return `<th>${{col.label}} <button type="button" class="btn-filtro-col ${{ativo}}" data-conta="${{conta.id}}" data-col="${{col.key}}">▾</button></th>`;
    }}).join('');

    const secao = document.createElement('div');
    secao.innerHTML = `
      <h2>${{conta.nome}} — ${{tipoLabel}} (amostra de até 30 lançamentos, de ${{filtrados.length}} filtrados)</h2>
      ${{amostra.length ? `
        <table>
          <tr>${{headerHtml}}</tr>
          ${{linhasHtml}}
        </table>
      ` : '<div class="vazio">Nenhum lançamento encontrado com os filtros atuais.</div>'}}
    `;
    secoesContainer.appendChild(secao);
  }});
}}

// Delegação de clique para os botões de filtro de coluna (eles são recriados a cada renderização).
document.getElementById('secoesContainer').addEventListener('click', (e) => {{
  const btn = e.target.closest('.btn-filtro-col');
  if (!btn) return;
  const contaId = btn.dataset.conta;
  const colKey = btn.dataset.col;
  const col = COLUNAS.find(c => c.key === colKey);
  if (col) abrirDropdownColuna(contaId, col, btn);
}});

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
