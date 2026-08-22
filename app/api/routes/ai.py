from __future__ import annotations

import json
import re
from typing import List, Optional
from fastapi import APIRouter, Depends, Request, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.core.rate_limit import enforce_rate_limit
from app.services.ai_gateway_client import chat_completion_via_gateway

router = APIRouter(prefix="/ai")


class AICategoryVerifyRequest(BaseModel):
    description: str
    amount: float
    selected_category: str
    available_categories: List[str]


class AICategoryVerifyResponse(BaseModel):
    is_coherent: bool
    explanation: Optional[str] = None
    suggested_category: Optional[str] = None


def extract_json_payload(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise ValueError("AI response is not valid JSON")


@router.post("/verify-category", response_model=AICategoryVerifyResponse)
async def verify_category(
    request: AICategoryVerifyRequest,
    fastapi_req: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AICategoryVerifyResponse:
    # 1. Enforce rate limiting: 10 requests per minute per user
    await enforce_rate_limit(
        db,
        fastapi_req,
        key_prefix=f"verify_category:{current_user.id}",
        limit=10,
        window_seconds=60,
    )

    # 2. Build system and user prompts
    system_prompt = (
        "Tu es un copilote financier pour l'application de budget 7sabek.\n"
        "Ton rôle est de vérifier si la catégorie choisie pour une dépense est cohérente avec sa description.\n"
        "Sois tolérant et ne bloque pas pour des synonymes ou des détails mineurs (ex: Spotify sous 'Abonnements' ou 'Loisirs' est cohérent).\n"
        "Bloque uniquement les incohérences flagrantes (ex: 'Dîner resto' sous 'Loyer' ou 'Santé').\n\n"
        "Tu dois impérativement renvoyer un objet JSON brut, sans formatage markdown (pas de ```json), contenant exactement ces clés :\n"
        "- is_coherent: boolean (true si cohérent, false si incohérent)\n"
        "- explanation: une phrase courte en français expliquant pourquoi c'est cohérent ou pourquoi c'est incohérent.\n"
        "- suggested_category: le nom d'une catégorie appropriée choisie uniquement parmi la liste des catégories disponibles fournie, ou null si c'est cohérent.\n\n"
        "Exemple de réponse attendue :\n"
        "{\"is_coherent\": false, \"explanation\": \"Un restaurant ne correspond pas à la catégorie Loyer.\", \"suggested_category\": \"Alimentation\"}"
    )

    user_message = (
        f"Description: {request.description}\n"
        f"Montant: {request.amount}\n"
        f"Catégorie sélectionnée: {request.selected_category}\n"
        f"Catégories disponibles: {', '.join(request.available_categories)}"
    )

    try:
        ai_response = await chat_completion_via_gateway(
            db,
            messages=[{"role": "user", "content": user_message}],
            system_prompt=system_prompt,
        )

        payload = extract_json_payload(ai_response)

        is_coherent = bool(payload.get("is_coherent", True))
        explanation = payload.get("explanation", "")
        suggested_category = payload.get("suggested_category")

        if suggested_category and suggested_category not in request.available_categories:
            suggested_category = None

        return AICategoryVerifyResponse(
            is_coherent=is_coherent,
            explanation=explanation,
            suggested_category=suggested_category,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur de communication avec l'IA : {str(exc)}",
        )
