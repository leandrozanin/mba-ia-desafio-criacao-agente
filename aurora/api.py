"""API HTTP do assistente (contrato do enunciado).

Sessoes e eventos ficam no DatabaseSessionService sobre SQLite
(`estado/sessoes.db`), entao reiniciar a API nao perde conversas (Garantia 3).

Garantia 1 na API: as confirmacoes pendentes sao as chamadas
`adk_request_confirmation` gravadas na sessao que ainda nao tem resposta. A
rota de confirmacoes so aceita um id que esteja nessa lista; qualquer outro
(inexistente, de outra sessao ou ja respondido) recebe 409 e nada executa.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, HTTPException
from google.adk.apps import App
from google.adk.events import Event
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import BaseModel

from . import banco
from .agentes import criar_app
from .config import APP_NAME, BANCO_SESSOES, ESTADO
from .ferramentas import CHAVE_APARTAMENTO

USUARIO = "morador"


class NovaSessao(BaseModel):
    apartamento: str


class NovaMensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


def confirmacoes_pendentes(eventos: list[Event]) -> list[dict]:
    respondidas = {
        fr.id for ev in eventos for fr in ev.get_function_responses() if fr.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
    }
    pendentes: list[dict] = []
    for ev in eventos:
        for fc in ev.get_function_calls():
            if fc.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME or fc.id in respondidas:
                continue
            args = fc.args or {}
            original = args.get("originalFunctionCall") or {}
            confirmacao = args.get("toolConfirmation") or {}
            pendentes.append(
                {
                    "id": fc.id,
                    "acao": original.get("name", ""),
                    "detalhes": confirmacao.get("payload") or original.get("args") or {},
                    "mensagem": confirmacao.get("hint", ""),
                }
            )
    return pendentes


def texto_da_resposta(eventos: list[Event]) -> str:
    partes = []
    for ev in eventos:
        if ev.author == "user" or ev.partial or not ev.content or not ev.content.parts:
            continue
        for p in ev.content.parts:
            if p.text and not p.thought:
                partes.append(p.text.strip())
    return "\n\n".join(t for t in partes if t)


def criar_api(app_adk: App | None = None) -> FastAPI:
    ESTADO.mkdir(parents=True, exist_ok=True)
    sessoes = DatabaseSessionService(
        db_url=f"sqlite+aiosqlite:///{BANCO_SESSOES.as_posix()}",
        connect_args={"timeout": 30},
    )
    runner = Runner(app=app_adk or criar_app(), session_service=sessoes)
    travas: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    api = FastAPI(title="Assistente do Residencial Aurora")

    async def sessao_ou_404(session_id: str):
        sessao = await sessoes.get_session(app_name=APP_NAME, user_id=USUARIO, session_id=session_id)
        if sessao is None:
            raise HTTPException(status_code=404, detail="sessão não encontrada")
        return sessao

    async def executar(session_id: str, mensagem: types.Content) -> dict:
        novos: list[Event] = []
        async for evento in runner.run_async(user_id=USUARIO, session_id=session_id, new_message=mensagem):
            novos.append(evento)
        sessao = await sessao_ou_404(session_id)
        return {"resposta": texto_da_resposta(novos), "confirmacoes_pendentes": confirmacoes_pendentes(sessao.events)}

    @api.post("/sessoes", status_code=201)
    async def criar_sessao(corpo: NovaSessao) -> dict:
        if corpo.apartamento not in banco.APARTAMENTOS:
            raise HTTPException(status_code=422, detail="apartamento inexistente")
        # Garantia 2: o apartamento entra no state uma unica vez, aqui, e as tools so leem dele.
        sessao = await sessoes.create_session(
            app_name=APP_NAME, user_id=USUARIO, state={CHAVE_APARTAMENTO: corpo.apartamento}
        )
        return {"session_id": sessao.id}

    @api.post("/sessoes/{session_id}/mensagens")
    async def enviar_mensagem(session_id: str, corpo: NovaMensagem) -> dict:
        await sessao_ou_404(session_id)
        async with travas[session_id]:
            return await executar(session_id, types.Content(role="user", parts=[types.Part(text=corpo.texto)]))

    @api.post("/sessoes/{session_id}/confirmacoes")
    async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao) -> dict:
        await sessao_ou_404(session_id)
        async with travas[session_id]:
            sessao = await sessao_ou_404(session_id)
            if corpo.id not in {p["id"] for p in confirmacoes_pendentes(sessao.events)}:
                raise HTTPException(status_code=409, detail="não existe confirmação pendente com esse id nesta sessão")
            resposta = types.Part(
                function_response=types.FunctionResponse(
                    id=corpo.id,
                    name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                    response={"confirmed": corpo.confirmado},
                )
            )
            return await executar(session_id, types.Content(role="user", parts=[resposta]))

    @api.get("/sessoes/{session_id}/eventos")
    async def eventos(session_id: str) -> list[dict[str, Any]]:
        sessao = await sessao_ou_404(session_id)
        return [ev.model_dump(mode="json", exclude_none=True, by_alias=True) for ev in sessao.events]

    @api.get("/apartamentos/{numero}/reservas")
    async def reservas(numero: str) -> list[dict]:
        return banco.reservas_do_apartamento(numero)

    @api.get("/apartamentos/{numero}/visitantes")
    async def visitantes(numero: str) -> list[dict]:
        return banco.visitantes_do_apartamento(numero)

    return api
