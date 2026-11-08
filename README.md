# Residencial Aurora — assistente virtual com Google ADK

API em Python (FastAPI) que expõe o assistente dos moradores do Residencial Aurora, construído com **Google ADK 2.9.1** e modelos Gemini. O modelo conduz a conversa; as regras críticas do condomínio estão no código. Enunciado original: [devfullcycle/mba-ia-desafio-criacao-agente](https://github.com/devfullcycle/mba-ia-desafio-criacao-agente).

```
aurora/
├── agentes.py       # agente principal, especialistas e App (resumível)
├── ferramentas.py   # tools: reservas, visitantes, regulamento
├── banco.py         # SQLite do condomínio (reservas e visitantes)
├── regulamento.py   # leitura do regulamento por capítulo
├── api.py           # rotas HTTP, sessões persistidas, confirmações
├── restaurar.py     # comando de restauração
└── __main__.py      # sobe a API em :8000
tests/
├── modelo_roteirizado.py    # modelo determinístico para testar sem chave do Gemini
├── servidor_roteirizado.py  # sobe a API com esse modelo
└── fluxo_avaliador.py       # reproduz o fluxo do avaliador por HTTP
```

## Arquitetura

O morador fala com um **agente principal**, que distribui o trabalho entre **três especialistas**:

| Agente | Responsabilidade | Como é acionado | Por quê |
|--------|------------------|-----------------|---------|
| `assistente_aurora` (principal) | Conversa com o morador e decide para quem vai cada pedido. Não tem tools de dados e **não recebe o regulamento** nas instruções. | Raiz do `App` | Mantém o contexto enxuto e o roteamento num lugar só. |
| `especialista_reservas` | Áreas, disponibilidade, reservar, listar e cancelar reservas do próprio apartamento. Tools: `listar_areas`, `consultar_disponibilidade`, `listar_minhas_reservas`, `reservar_area`, `cancelar_minha_reserva`. | Sub-agente, por **transferência** (`transfer_to_agent`) | Reservar é uma conversa de vários turnos (data ocupada, outra data, confirmação); o especialista assume a conversa enquanto ela durar e pede a confirmação da cobrança. |
| `especialista_visitantes` | Liberar entrada e listar visitantes do próprio apartamento. Tools: `listar_meus_visitantes`, `autorizar_visitante`. | Sub-agente, por **transferência** | Mesmo motivo: pode haver troca de mensagens e sempre há confirmação. |
| `especialista_regulamento` | Responde dúvidas lendo **um capítulo por vez** com `ler_capitulo`; só ele conhece o sumário do regulamento. | **`AgentTool`** chamado pelo principal | Roda num Runner próprio, com sessão em memória: o texto lido nunca entra nos eventos da sessão do morador, só a resposta curta. |

Os especialistas de reservas e visitantes podem transferir entre si e de volta ao principal. O `App` é **resumível** (`ResumabilityConfig(is_resumable=True)`): no ADK 2.9.1 é isso que faz o Runner entregar a resposta de uma confirmação ao agente que a pediu (`_agent_router.find_agent_to_run` só roteia pela chamada de função quando o app é resumível); sem isso a resposta cairia no principal, que não tem a tool, e a ação não executaria.

Armazenamento:
- `estado/condominio.db` (SQLite): reservas e visitantes. `dados/` é só o estado inicial e nunca é alterado.
- `estado/sessoes.db` (SQLite via `DatabaseSessionService`): sessões, state e eventos.

Modelos (configuráveis no `.env`): `gemini-3.8-flash` para o principal e os especialistas de reservas/visitantes, `gemini-3.5-flash-lite` para o regulamento — os modelos estáveis atuais na [lista de modelos da Gemini API](https://ai.google.dev/gemini-api/docs/models) (a família 2.5 está restrita a quem já a usava).

## Garantias

### 1. Cobrança ou acesso só com confirmação

`aurora/ferramentas.py`, em `reservar_area` (só quando a área tem taxa) e em `autorizar_visitante` (sempre):

```python
confirmacao = tool_context.tool_confirmation
if confirmacao is None:
    tool_context.request_confirmation(hint=..., payload={"area": ..., "data": ..., "taxa": ...})
    return {"status": "aguardando_confirmacao", ...}
if not confirmacao.confirmed:
    return {"status": "nao_confirmada", ...}
codigo = banco.criar_reserva(apartamento, dados_area["id"], data)
```

A gravação só acontece com `tool_confirmation.confirmed`, que o ADK só preenche quando chega uma `FunctionResponse` de `adk_request_confirmation`. Essa resposta só é criada pela rota de confirmações, em `aurora/api.py` (`responder_confirmacao`):

```python
if corpo.id not in {p["id"] for p in confirmacoes_pendentes(sessao.events)}:
    raise HTTPException(status_code=409, ...)
resposta = types.Part(function_response=types.FunctionResponse(
    id=corpo.id, name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME, response={"confirmed": corpo.confirmado}))
```

`confirmacoes_pendentes` lista as chamadas `adk_request_confirmation` da sessão que ainda não têm resposta; um id inexistente, de outra sessão ou já respondido não está na lista e recebe 409 sem executar nada. Não depende do modelo: escrever "já estou confirmando" é só texto e nunca vira `FunctionResponse`; e `quadra` (taxa 0) e `cancelar_minha_reserva` simplesmente não pedem confirmação.

### 2. Cada sessão pertence a um apartamento

`aurora/api.py`, `criar_sessao`, grava o apartamento uma única vez no state:

```python
sessao = await sessoes.create_session(app_name=APP_NAME, user_id=USUARIO,
                                      state={CHAVE_APARTAMENTO: corpo.apartamento})
```

`aurora/ferramentas.py`, `apartamento_da_sessao`, é a única origem do apartamento nas tools:

```python
def apartamento_da_sessao(tool_context: ToolContext) -> str:
    apartamento = tool_context.state.get(CHAVE_APARTAMENTO)
```

**Nenhuma tool tem parâmetro de apartamento**, então o modelo não tem como escolher outro. As consultas em `aurora/banco.py` filtram por `apartamento = ?` (`reservas_do_apartamento`, `visitantes_do_apartamento`, `cancelar_reserva`). `consultar_disponibilidade` usa `area_livre`, que devolve só livre/ocupada, nunca o dono; `reservar_area` em data ocupada responde "já está reservado" sem código nem apartamento.

### 3. Nada se perde no reinício

`aurora/api.py`, `criar_api`:

```python
sessoes = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{BANCO_SESSOES.as_posix()}", connect_args={"timeout": 30})
runner = Runner(app=app_adk or criar_app(), session_service=sessoes)
```

Sessões, state e eventos ficam em `estado/sessoes.db`; reservas e visitantes em `estado/condominio.db` (`aurora/banco.py`). Ao subir de novo, a mesma sessão devolve os mesmos eventos e continua. Uma confirmação pendente antes do reinício pode ser aprovada depois dele: o roteamento lê os eventos persistidos.

### 4. O regulamento é consultado, não carregado

`aurora/agentes.py`: o principal recebe `INSTRUCAO_PRINCIPAL`, que não tem nenhum trecho do regulamento, e chama o especialista como ferramenta:

```python
principal = LlmAgent(name="assistente_aurora", instruction=INSTRUCAO_PRINCIPAL,
                     sub_agents=[especialista_reservas, especialista_visitantes],
                     tools=[AgentTool(especialista_regulamento)])
```

`AgentTool` executa o especialista num Runner próprio com `InMemorySessionService`, então a chamada a `ler_capitulo` e o capítulo lido ficam fora da sessão do morador; na sessão entram só a chamada do `AgentTool` e a resposta curta. `aurora/regulamento.py` corta o arquivo por `## Capítulo` e `ferramentas.ler_capitulo` devolve **um** capítulo — nunca o arquivo inteiro.

### 5. Dois moradores, uma reserva

`aurora/banco.py`:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_reserva_ativa ON reservas (area, data) WHERE ativa = 1;
```

```python
try:
    con.execute("INSERT INTO reservas (codigo, apartamento, area, data, ativa, atualizada_em) VALUES (?, ?, ?, ?, 1, ?)", ...)
    return codigo
except sqlite3.IntegrityError as erro:
    if "reservas.codigo" in str(erro):
        continue  # código já usado: sorteia outro
    return None   # uq_reserva_ativa: alguém reservou a área nessa data
```

A exclusividade é verificada **pelo banco, no INSERT**. A consulta prévia em `reservar_area` só evita pedir confirmação para data ocupada; se duas aprovações correrem ao mesmo tempo, o SQLite serializa as escritas e a segunda bate no índice único, e a tool responde `{"status": "indisponivel", ...}` — resposta normal, HTTP 200. O mesmo arquivo garante que códigos nunca se repetem: `codigo` é chave primária e cancelar só marca `ativa = 0`, sem apagar a linha.

## Como rodar

Pré-requisitos: Python 3.12+ e [uv](https://docs.astral.sh/uv/). Não há serviço externo: o armazenamento é SQLite em arquivo.

```bash
cp .env.example .env        # preencha GOOGLE_API_KEY com a chave do Google AI Studio
uv sync
uv run python -m aurora.restaurar   # restaura reservas e visitantes de dados/ e apaga as sessões
uv run python -m aurora             # sobe a API em http://localhost:8000
```

Variáveis do `.env`:

| Variável | Obrigatória | Uso |
|----------|-------------|-----|
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio |
| `GOOGLE_GENAI_USE_VERTEXAI` | não | Deixe vazio (ou `FALSE`) para usar a Gemini API do AI Studio |
| `AURORA_MODELO_PRINCIPAL` | não | Padrão `gemini-3.8-flash` |
| `AURORA_MODELO_ESPECIALISTAS` | não | Padrão `gemini-3.8-flash` (reservas e visitantes) |
| `AURORA_MODELO_REGULAMENTO` | não | Padrão `gemini-3.5-flash-lite` |

O comando de restauração deve rodar com a API parada. Para reiniciar a API sem perder nada, basta `Ctrl+C` e `uv run python -m aurora` de novo (sem restaurar).

### Conferindo sem gastar cota

`tests/servidor_roteirizado.py` sobe a mesma API trocando o Gemini por um modelo determinístico (`tests/modelo_roteirizado.py`), útil para validar o que não depende do modelo — confirmação e retomada, sessão persistida, gravação e concorrência. `tests/fluxo_avaliador.py` reproduz os passos 1–14 do avaliador por HTTP, com o Gemini real ou com o roteirizado:

```bash
uv run python -m aurora.restaurar
uv run python -m tests.servidor_roteirizado      # ou: uv run python -m aurora
uv run python -m tests.fluxo_avaliador antes     # passos 1 a 12
# Ctrl+C na API e suba de novo, sem restaurar
uv run python -m tests.fluxo_avaliador depois    # passos 13 e 14
```

Com o modelo roteirizado, as duas fases passam sem falhas (o texto das respostas, nesse modo, é um resumo mecânico do resultado da tool).
