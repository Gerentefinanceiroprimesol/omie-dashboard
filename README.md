# Dashboard Omie — Prime Sol (Financeiro & Estoque)

Atualiza automaticamente, 2x por dia, uma página HTML publicada no GitHub Pages
com dados de Estoque e Financeiro puxados direto da API da Omie.

## Passo a passo para colocar no ar

### 1. Criar o repositório
1. Crie um repositório novo no GitHub (pode ser privado).
2. Suba estes arquivos mantendo a estrutura de pastas:
   ```
   .github/workflows/atualizar.yml
   scripts/atualizar_dashboard.py
   README.md
   ```

### 2. Gerar a chave de acesso na Omie
1. No Omie, acesse **Configurações > API** (ou peça ao administrador da conta).
2. Gere/copie o **App Key** e **App Secret** do seu aplicativo integrador.

### 3. Guardar as credenciais como Secrets no GitHub
1. No repositório: **Settings > Secrets and variables > Actions > New repository secret**.
2. Crie dois secrets:
   - `OMIE_APP_KEY`
   - `OMIE_APP_SECRET`

Nunca coloque essas chaves diretamente no código ou no HTML — elas ficam
só nesses secrets, acessíveis apenas durante a execução do workflow.

### 4. Ativar o GitHub Pages
1. **Settings > Pages**.
2. Em "Build and deployment", selecione **Source: GitHub Actions**.

### 5. Testar
1. Vá em **Actions > Atualizar dashboard Omie > Run workflow** para rodar manualmente
   a primeira vez.
2. Se der certo, a URL da página aparecerá em **Settings > Pages**
   (algo como `https://seu-usuario.github.io/nome-do-repo/`).
3. A partir daí, ele roda sozinho 2x por dia nos horários configurados no
   arquivo `.github/workflows/atualizar.yml`.

### 6. Ajustar os endpoints da Omie (importante)
Os nomes de `call` usados no script (`ListarContasPagar`, `ListarContasReceber`,
`ListarPosicaoEstoque`) são os mais comuns, mas podem variar conforme o plano
contratado. Antes de confiar 100% nos números:

1. Entre no [Portal do Desenvolvedor da Omie](https://developer.omie.com.br).
2. Teste cada chamada com sua própria App Key/Secret.
3. Confirme os nomes exatos dos campos retornados (ex: `valor_documento`,
   `status_titulo`, `fisico`, `estoqueMinimo`) e ajuste o script
   `scripts/atualizar_dashboard.py` se algum campo vier com nome diferente.

## Como mudar os horários
Edite os dois `cron:` em `.github/workflows/atualizar.yml`. Os horários são
em UTC — Campos dos Goytacazes está em UTC-3 (UTC-2 durante horário de verão,
se algum dia voltar a existir).

## Como mudar o visual / quais dados aparecem
Toda a montagem do HTML está na função `gerar_html()` em
`scripts/atualizar_dashboard.py`. Dá pra adicionar gráficos, mais filtros,
ou separar por CNPJ (Prime Sol Lagos vs Prime Sol Soluções) alterando os
parâmetros das chamadas à API.
