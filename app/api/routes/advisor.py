from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.models.envelope import Envelope
from app.models.envelope_period import EnvelopePeriod
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.repositories.advisor import (
    AdvisorDecisionRepository,
    AdvisorPreviewRepository,
    AdvisorValidationRepository,
)
from app.schemas.advisor.api import (
    AdvisorAcceptOut,
    AdvisorAcceptRequestIn,
    AdvisorChatRequestIn,
    AdvisorChatResponseOut,
    AdvisorPreviewEnvelopeOut,
    AdvisorPreviewRequestIn,
    AdvisorValidatePreApplyOut,
    AdvisorValidatePreApplyRequestIn,
)
from app.services.advisor import (
    AcceptService,
    AdvisorPreviewService,
    FallbackExplainService,
    GatingService,
    NormalizerService,
    ProposalEngineService,
    ValidatePreApplyService,
)
from app.services.ai_gateway_client import (
    AIGatewayConfigurationError,
    _safe_string,
    chat_completion_via_gateway,
)

router = APIRouter(prefix="/advisor")

_PREVIEW_ERROR_HTTP_STATUS: dict[str, int] = {
    "ADVISOR_USER_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ADVISOR_SOURCE_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ADVISOR_NORMALIZER_FAILED": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ADVISOR_GATING_FAILED": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ADVISOR_ENGINE_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "ADVISOR_EXPLAIN_FALLBACK_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
    "ADVISOR_PREVIEW_PERSIST_FAILED": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


@router.post("/preview", response_model=AdvisorPreviewEnvelopeOut)
async def advisor_preview(
    payload: AdvisorPreviewRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorPreviewEnvelopeOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADVISOR_USER_MISMATCH")

    service = AdvisorPreviewService(
        normalizer=NormalizerService(),
        gating=GatingService(),
        engine=ProposalEngineService(),
        fallback_explain=FallbackExplainService(),
        previews=AdvisorPreviewRepository(),
    )

    try:
        out = await service.generate(
            db=db,
            user=current_user,
            source=payload.source,
            force_regenerate=payload.force_regenerate,
        )
    except RuntimeError as exc:
        await db.rollback()
        code = str(exc)
        raise HTTPException(
            status_code=_PREVIEW_ERROR_HTTP_STATUS.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=code,
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="INTERNAL_ERROR") from exc

    await db.commit()
    return out


@router.post("/validate-pre-apply", response_model=AdvisorValidatePreApplyOut)
async def advisor_validate_pre_apply(
    payload: AdvisorValidatePreApplyRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorValidatePreApplyOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADVISOR_USER_MISMATCH")

    service = ValidatePreApplyService(
        previews=AdvisorPreviewRepository(),
        validations=AdvisorValidationRepository(),
    )
    out = await service.validate(
        db=db,
        user=current_user,
        preview_id=payload.preview_id,
        proposal_id=payload.proposal_id,
    )
    await db.commit()
    return out


@router.post("/accept", response_model=AdvisorAcceptOut)
async def advisor_accept(
    payload: AdvisorAcceptRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorAcceptOut:
    if payload.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ADVISOR_USER_MISMATCH")

    service = AcceptService(
        decisions=AdvisorDecisionRepository(),
        validations=AdvisorValidationRepository(),
    )
    try:
        out = await service.accept(
            db=db,
            user=current_user,
            preview_id=payload.preview_id,
            proposal_id=payload.proposal_id,
            validation_id=payload.validation_id,
            confirm=payload.confirm,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    return out


# ---------------------------------------------------------------------------
# Conversational chat endpoint (AI advisor with real user context)
# ---------------------------------------------------------------------------


async def _collect_user_context(db: AsyncSession, user: User) -> dict:
    """Collect user profile, envelopes, goals and recent transactions for AI context."""
    # Profile
    profile = {
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "email": user.email,
        "currency": user.currency,
        "sweep_interval_days": user.sweep_interval_days,
    }

    # Envelopes with current period balances
    envelopes_query = await db.execute(
        select(Envelope)
        .where(Envelope.user_id == user.id)
        .order_by(Envelope.name)
    )
    envelopes = list(envelopes_query.scalars().all())

    envelope_list = []
    for env in envelopes:
        period = await db.execute(
            select(EnvelopePeriod)
            .where(
                EnvelopePeriod.envelope_id == env.id,
                EnvelopePeriod.user_id == user.id,
            )
            .order_by(EnvelopePeriod.period_start.desc())
            .limit(1)
        )
        period_obj = period.scalar_one_or_none()
        balance = float(period_obj.opening_balance) if period_obj else 0
        env_info = {
            "id": str(env.id),
            "name": env.name,
            "is_default_savings": env.is_default_savings,
            "is_cash": env.is_cash,
            "is_goal": env.is_goal,
            "rollover_enabled": env.rollover_enabled,
            "current_balance": balance,
        }
        envelope_list.append(env_info)

    # Goals
    goals_query = await db.execute(
        select(Goal)
        .where(Goal.user_id == user.id)
        .order_by(Goal.priority, Goal.name)
    )
    goals = list(goals_query.scalars().all())

    goal_list = []
    for g in goals:
        current_balance = 0
        if g.envelope_id:
            period = await db.execute(
                select(EnvelopePeriod)
                .where(
                    EnvelopePeriod.envelope_id == g.envelope_id,
                    EnvelopePeriod.user_id == user.id,
                )
                .order_by(EnvelopePeriod.period_start.desc())
                .limit(1)
            )
            period_obj = period.scalar_one_or_none()
            if period_obj:
                current_balance = float(period_obj.opening_balance)

        goal_list.append({
            "id": str(g.id),
            "name": g.name,
            "goal_type": g.goal_type,
            "target_amount": float(g.target_amount),
            "current_balance": current_balance,
            "target_date": str(g.target_date) if g.target_date else None,
            "contribution_amount": float(g.contribution_amount),
            "auto_contribute": g.auto_contribute,
            "priority": g.priority,
        })

    # Recent transactions (last 10)
    txn_query = await db.execute(
        select(Transaction)
        .where(Transaction.user_id == user.id)
        .order_by(Transaction.occurred_on.desc())
        .limit(10)
    )
    transactions = list(txn_query.scalars().all())

    recent_txns = []
    for t in transactions:
        recent_txns.append({
            "id": str(t.id),
            "type": t.type.value if hasattr(t.type, "value") else str(t.type),
            "amount": float(t.amount),
            "occurred_on": str(t.occurred_on),
            "description": t.description or "",
            "source": t.source,
        })

    return {
        "profile": profile,
        "envelopes": envelope_list,
        "goals": goal_list,
        "recent_transactions": recent_txns,
    }


def _generate_advisor_fallback_response(user_context: dict, prompt: str) -> str:
    """Generate a high-quality contextual advisory response when external LLM is unreachable."""
    profile = user_context.get("profile", {})
    first_name = profile.get("first_name") or "Cher utilisateur"
    currency = profile.get("currency") or "MAD"
    envelopes = user_context.get("envelopes", [])
    goals = user_context.get("goals", [])
    
    total_env_balance = sum(e.get("current_balance", 0) for e in envelopes)
    prompt_lower = (prompt or "").lower()
    
    is_darija = any(w in prompt_lower for w in ["chkoun", "fin", "flous", "kifach", "3afak", "bghit", "dyal", "salam", "chokran"])
    is_english = any(w in prompt_lower for w in ["hello", "how", "what", "budget", "save", "money", "help", "security"])
    
    # 1. Topic: Security / ShieldKey / Alerts
    if any(k in prompt_lower for k in ["securite", "sécurité", "alerte", "alertes", "shieldkey", "pin", "ip", "connexion", "security"]):
        if is_darija:
            return (
                f"أهلاً {first_name} ! 🛡️ نظام الحماية ShieldKey خدام بشكل ممتاز والحساب ديالك فـ أمان كامل.\n\n"
                "• التشفير المحلي مفعّل على كل العمليات والجلسات.\n"
                "• كنصحك تبدل كود PIN ديالك بشكل دوري وتأكد من الإشعارات الجديدة.\n\n"
                "[bouton: 🔍 Analyser mes alertes]\n"
                "[bouton: 📈 Simuler un budget]"
            )
        elif is_english:
            return (
                f"Hello {first_name}! 🛡️ Your ShieldKey security system is active and your account is secure.\n\n"
                "• All transactions and sensitive data are encrypted locally.\n"
                "• Recommended: Regularly review active alerts and maintain strong PIN protection.\n\n"
                "[button: 🔍 Analyze my alerts]\n"
                "[button: 📈 Simulate a budget]"
            )
        else:
            return (
                f"Bonjour {first_name} ! 🛡️ Le système de sécurité ShieldKey est actif et votre compte est parfaitement protégé.\n\n"
                "• Vos transactions et communications bénéficient du chiffrement de bout en bout.\n"
                "• Recommandation : Surveillez régulièrement vos notifications de connexion et ne partagez jamais votre code PIN.\n\n"
                "[bouton: 🔍 Analyser mes alertes]\n"
                "[bouton: 📈 Simuler un budget]"
            )
            
    # 2. Topic: Savings / Goals / Epargne
    if any(k in prompt_lower for k in ["epargne", "épargne", "cagnotte", "objectif", "objectifs", "economiser", "économiser", "savings", "goal"]):
        goal_summary = ""
        if goals:
            g = goals[0]
            curr = g.get("current_balance", 0)
            tgt = g.get("target_amount", 0)
            pct = int((curr / tgt * 100)) if tgt > 0 else 0
            goal_summary = f"Votre objectif **{g.get('name')}** est actuellement financé à **{pct}%** ({curr:.2f} / {tgt:.2f} {currency}).\n\n"
        
        if is_darija:
            return (
                f"أهلاً {first_name} ! 💎 بخصوص الادخار ديالك :\n\n"
                f"{goal_summary}"
                f"الرصيد الإجمالي الموزع فـ الأظرفة هو **{total_env_balance:.2f} {currency}**.\n"
                "كنصحك تفعل ميزة Cash Split التلقائية باش تحول الفائض مباشرة للادخار مع نهاية كل دورة.\n\n"
                "[bouton: 💡 Conseils d'épargne]\n"
                "[bouton: 📈 Simuler un budget]"
            )
        elif is_english:
            return (
                f"Hello {first_name}! 💎 Regarding your savings:\n\n"
                f"{goal_summary}"
                f"Your total envelope balance is **{total_env_balance:.2f} {currency}**.\n"
                "Tip: Activate automatic Cash Split to route unspent funds directly to your savings goals.\n\n"
                "[button: 💡 Savings tips]\n"
                "[button: 📈 Simulate a budget]"
            )
        else:
            return (
                f"Bonjour {first_name} ! 💎 Concernant votre épargne :\n\n"
                f"{goal_summary}"
                f"Le total actuellement réparti dans vos enveloppes est de **{total_env_balance:.2f} {currency}**.\n"
                "Astuce : Activez la discipline Floussy Cash Split pour basculer automatiquement les reliquats non dépensés vers vos cagnottes prioritaires.\n\n"
                "[bouton: 💡 Conseils d'épargne]\n"
                "[bouton: 📈 Simuler un budget]\n"
                "[bouton: 💸 Expliquer Cash Split]"
            )

    # 3. Default: Budget & Financial overview
    env_details = ""
    if envelopes:
        top_envs = envelopes[:3]
        env_details = "• " + "\n• ".join([f"**{e.get('name')}** : {e.get('current_balance', 0):.2f} {currency}" for e in top_envs]) + "\n\n"

    if is_darija:
        return (
            f"أهلاً {first_name} ! 🪙 ها هي النظرة العامة على الميزانية ديالك :\n\n"
            f"• الرصيد الإجمالي للأظرفة : **{total_env_balance:.2f} {currency}**\n"
            f"{env_details}"
            "كيفاش نقدر نعاونك اليوم فـ إدارة الفلوس ديالك ؟\n\n"
            "[bouton: 📈 Simuler un budget]\n"
            "[bouton: 💡 Conseils d'épargne]\n"
            "[bouton: 🔍 Analyser mes alertes]"
        )
    elif is_english:
        return (
            f"Hello {first_name}! 🪙 Here is an overview of your current budget:\n\n"
            f"• Total envelopes balance: **{total_env_balance:.2f} {currency}**\n"
            f"{env_details}"
            "How can I help you manage your funds today?\n\n"
            "[button: 📈 Simulate a budget]\n"
            "[button: 💡 Savings tips]\n"
            "[button: 🔍 Analyze my alerts]"
        )
    else:
        return (
            f"Bonjour {first_name} ! 🪙 Voici le point sur votre budget 7sabek :\n\n"
            f"• Solde global des enveloppes : **{total_env_balance:.2f} {currency}**\n"
            f"{env_details}"
            "Que souhaitez-vous analyser ou simuler aujourd'hui ?\n\n"
            "[bouton: 📈 Simuler un budget]\n"
            "[bouton: 💡 Conseils d'épargne]\n"
            "[bouton: 🔍 Analyser mes alertes]"
        )


@router.post("/chat", response_model=AdvisorChatResponseOut)
async def advisor_chat(
    payload: AdvisorChatRequestIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AdvisorChatResponseOut:
    """Send a conversation to the AI advisor enriched with the user's real financial data."""
    # Gather user financial context
    user_context = await _collect_user_context(db, current_user)

    # Build a structured context block to inject into the system prompt
    context_json = json.dumps(user_context, ensure_ascii=False, indent=2)

    user_language_context = (
        f"The user's name is {current_user.first_name or ''} {current_user.last_name or ''}."
        f" Their currency is {current_user.currency}."
    )

    base_system = (
        "You are Floussy AI, the intelligent financial advisor integrated into the 7sabek/Floussy application. "
        "You have access to the user's real financial data below. Use this information to answer "
        "questions about their envelopes, goals, transactions, and budget.\n\n"
        "IMPORTANT PRIVACY RULES:\n"
        "- You are permitted to use the financial data below because the user has explicitly requested advice.\n"
        "- Do not share this data externally; only use it to answer the user's question.\n"
        "- Never ask the user to provide information you already have.\n\n"
        "RESPONSE GUIDELINES:\n"
        "- Be friendly, professional, and concise (2-3 short paragraphs max).\n"
        "- Use the user's language: French, English, or Moroccan Darija naturally.\n"
        "- Do not invent balances or transactions not present in the data.\n"
        "- At the end of your response, suggest 1-3 relevant follow-up actions using the format:\n"
        "  [bouton: Action label] (for French)\n"
        "  [button: Action label] (for English)\n"
        "  [bouton: تسمية الإجراء] (for Arabic)\n\n"
        f"USER CONTEXT:\n{user_language_context}\n\n"
        f"USER FINANCIAL DATA:\n{context_json}"
    )

    # Inject the superadmin's global advisor instructions if set
    from app.core.platform_settings import get_platform_settings as _get_ps
    try:
        _platform_settings = await _get_ps(db, create_if_missing=False)
        _global_instructions = (
            _safe_string(getattr(_platform_settings, "advisor_global_instructions", None))
            if _platform_settings is not None
            else ""
        )
        if _global_instructions:
            base_system = (
                f"{base_system}\n\n"
                "GLOBAL PLATFORM INSTRUCTIONS (mandatory):\n"
                f"{_global_instructions}"
            )
    except Exception:
        pass

    # If the client provided a custom system prompt, append it to our base context
    system_prompt = base_system
    if payload.system_prompt:
        system_prompt = f"{base_system}\n\nAdditional instructions from the client:\n{payload.system_prompt}"

    # Convert incoming messages to the format expected by chat_completion_via_gateway
    messages_for_ai = [
        {"role": msg.role, "content": msg.text}
        for msg in payload.messages
    ]

    last_user_prompt = ""
    for msg in reversed(payload.messages):
        if msg.role == "user" and msg.text:
            last_user_prompt = msg.text
            break

    try:
        reply = await chat_completion_via_gateway(
            db,
            messages=messages_for_ai,
            system_prompt=system_prompt,
        )
    except Exception as exc:
        err_msg = str(exc)
        # Display clear diagnostic if gateway fails so superadmin knows immediately why
        fallback_text = _generate_advisor_fallback_response(user_context, last_user_prompt)
        reply = f"⚠️ **[Diagnostic IA]** : `{err_msg}`\n\n---\n\n{fallback_text}"

    return AdvisorChatResponseOut(text=reply)
