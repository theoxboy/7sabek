from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.rate_limit import check_rate_limit
from app.db.session import get_db
from app.models.advisor_chat_message import AdvisorChatMessage
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
    AdvisorChatMessageOut,
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
    AIGatewayQuotaError,
    _safe_string,
    chat_completion_via_gateway,
)

logger = logging.getLogger(__name__)

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


ADVISOR_QUOTA_WINDOW_HOURS = 24

# The market's timezone: a user in Casablanca must read the hour they will
# actually see on their phone, not UTC.
ADVISOR_DISPLAY_TIMEZONE = "Africa/Casablanca"

_MONTHS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
_MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_MONTHS_AR = [
    "يناير", "فبراير", "مارس", "أبريل", "ماي", "يونيو",
    "يوليوز", "غشت", "شتنبر", "أكتوبر", "نونبر", "دجنبر",
]


def _advisor_retry_after_seconds(error: Exception) -> Optional[int]:
    """Seconds the provider asked us to wait, when it says so.

    A rate limit usually carries its own window; only when it does not do we
    fall back to a flat 24 hours.
    """
    text = str(error)
    for pattern in (
        r'"retry[_-]?after"\s*:\s*"?(\d+)',
        r"retry[- ]after[\"'\s:]+(\d+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                seconds = int(match.group(1))
            except ValueError:
                continue
            # Anything beyond a week is not a wait, it is an outage.
            if 0 < seconds <= 7 * 24 * 3600:
                return seconds
    return None


def _advisor_quota_reset_at(seconds_until_reset: Optional[int]) -> datetime:
    """When the user should come back.

    The delay is anchored on the first refusal rather than recomputed on every
    attempt: "come back in 24 hours" that moves forward each time the user taps
    send is not a limit, it is a mirage.
    """
    delay = timedelta(seconds=seconds_until_reset) if seconds_until_reset and seconds_until_reset > 0 \
        else timedelta(hours=ADVISOR_QUOTA_WINDOW_HOURS)
    return datetime.now(timezone.utc) + delay


def _format_reset_moment(moment: datetime, language: str) -> str:
    """A date a human reads, without depending on server locales."""
    try:
        local = moment.astimezone(ZoneInfo(ADVISOR_DISPLAY_TIMEZONE))
    except Exception:
        # No tz database on the host: Morocco is UTC+1 year round.
        local = moment.astimezone(timezone(timedelta(hours=1)))

    day = local.day
    month_index = local.month - 1
    hour = local.strftime("%H:%M")

    if language == "ar":
        return f"{day} {_MONTHS_AR[month_index]} {local.year} على {hour}"
    if language == "en":
        return f"{_MONTHS_EN[month_index]} {day}, {local.year} at {hour}"
    return f"{day} {_MONTHS_FR[month_index]} {local.year} à {hour}"


def _advisor_outage_notice(
    prompt: str,
    error: Exception,
    seconds_until_reset: Optional[int] = None,
) -> str:
    """One sentence telling the user what happened, in the language they used."""
    prompt_lower = (prompt or "").lower()
    is_darija = any(
        w in prompt_lower
        for w in ["chkoun", "fin", "flous", "kifach", "3afak", "bghit", "dyal", "salam", "chokran"]
    ) or any("\u0600" <= ch <= "\u06ff" for ch in (prompt or ""))
    is_english = any(
        w in prompt_lower
        for w in ["hello", "how", "what", "budget", "save", "money", "help", "security"]
    )

    if isinstance(error, AIGatewayQuotaError):
        # Same shape as any assistant that meters usage: say the limit was
        # reached, and say exactly when it lifts. Never why, and never with the
        # provider's billing page attached.
        reset_at = _advisor_quota_reset_at(seconds_until_reset)
        if is_darija:
            moment = _format_reset_moment(reset_at, "ar")
            return (
                f"⏳ وصلتي للحد ديال المحادثة مع المساعد الذكي دابا.\n"
                f"عاود جرب من بعد **{moment}**. حتى ذاك الوقت، ها تحليل مباشر ديال أرقامك :"
            )
        if is_english:
            moment = _format_reset_moment(reset_at, "en")
            return (
                f"⏳ You have reached the AI chat limit for now.\n"
                f"Please come back after **{moment}**. In the meantime, here is a read of your figures:"
            )
        moment = _format_reset_moment(reset_at, "fr")
        return (
            f"⏳ Vous avez atteint la limite du chat avec l'assistant IA.\n"
            f"Revenez après **{moment}**. En attendant, voici une lecture de vos chiffres :"
        )

    if isinstance(error, AIGatewayConfigurationError):
        if is_darija:
            return "⚙️ المساعد الذكي مامفعلش حالياً. ها تحليل مباشر ديال حسابك :"
        if is_english:
            return "⚙️ The AI assistant is not switched on yet. Here is a direct read of your account:"
        return "⚙️ L'assistant IA n'est pas encore activé. Voici une lecture directe de votre compte :"

    if is_darija:
        return "⚠️ ما قدرناش نوصلو للمساعد الذكي. ها تحليل ديال أرقامك :"
    if is_english:
        return "⚠️ The AI assistant could not be reached. Here is a read of your figures:"
    return "⚠️ Impossible de joindre l'assistant IA. Voici une lecture de vos chiffres :"


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
    if current_user.is_guest:
        from app.core.guest import GUEST_ADVISOR_MESSAGES_PER_DAY
        from sqlalchemy import func as _func

        day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        used_today = await db.scalar(
            select(_func.count())
            .select_from(AdvisorChatMessage)
            .where(
                AdvisorChatMessage.user_id == current_user.id,
                AdvisorChatMessage.role == "user",
                AdvisorChatMessage.created_at >= day_start,
            )
        )
        if (used_today or 0) >= GUEST_ADVISOR_MESSAGES_PER_DAY:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "guest_advisor_daily_limit",
                    "limit": GUEST_ADVISOR_MESSAGES_PER_DAY,
                },
            )

    # Gather user financial context
    user_context = await _collect_user_context(db, current_user)

    # Build a structured context block to inject into the system prompt
    context_json = json.dumps(user_context, ensure_ascii=False, indent=2)

    user_language_context = (
        f"The user's name is {current_user.first_name or ''} {current_user.last_name or ''}."
        f" Their currency is {current_user.currency}."
    )

    base_system = (
        "You are 7sabek AI (Floussy AI), the elite smart financial advisor integrated directly into 7sabek (حسابك / فلوسي).\n"
        "You have complete and authorized access to the user's real account data provided below.\n\n"
        "CORE IDENTITY & EXPERTISE:\n"
        "- You are a top-tier personal CFO: warm, encouraging, mathematically precise, proactive, and respectful.\n"
        "- You master the 7sabek financial method: Cash Splitting (تقسيم المداخيل), Envelopes (الأظرفة المالية), Sweeps (ترحيل الفائض للادخار), and ShieldKey Security (الأمان والتشفير).\n\n"
        "LANGUAGE & LOCALIZATION RULES (MANDATORY):\n"
        "1. MATCH USER LANGUAGE NATURALLY:\n"
        "   - If the user speaks Moroccan Darija (in Arabic script or Arabizi/Latin e.g., 'salam', 'wa fin', 'labas', 'bghit', 'kifach', 'dyal'):\n"
        "     Respond in natural, fluent, elegant Moroccan Darija with Arabic script (e.g., 'وا فيين سي عمر ! كلشي بيخير ؟', 'الأظرفة المالية', 'الكاش المتوفر').\n"
        "   - NEVER mix English terms inside Arabic sentences (NEVER say 'envelopes الخاصة بك' -> ALWAYS use 'الأظرفة ديالك' or 'الصناديق ديالك').\n"
        "   - If the user speaks French, respond in flawless, modern French.\n"
        "   - If the user speaks English, respond in professional, friendly English.\n\n"
        "2. CONVERSATIONAL INTELLIGENCE & GREETINGS:\n"
        "   - If the user just says a simple greeting ('salam', 'wa fin', 'bonjour', 'hello', 'labas'):\n"
        "     Respond with a warm, lively greeting in the same language (1-2 sentences) and briefly ask how you can help them optimize their money or simulate a budget today.\n"
        "     DO NOT dump the entire budget unless they asked a question about it.\n\n"
        "3. DEEP RELEVANCE WITH USER'S REAL ENVELOPES & DATA:\n"
        "   - Always check and reference the user's actual named envelopes (e.g. from the list in USER FINANCIAL DATA) and goals.\n"
        "   - If their envelopes are at 0 DH, guide them on how to allocate their available cash (e.g. their 13,000 DH or current balance) into their specific envelopes.\n"
        "   - Never invent transactions or balances not present in the data.\n\n"
        "4. INTERACTIVE ACTION BUTTONS (MANDATORY):\n"
        "   - At the VERY END of every single response, you MUST ALWAYS generate 2 to 3 contextual interactive button suggestions on separate lines in the exact format:\n"
        "     [bouton: Label en français]\n"
        "     [button: Label in English]\n"
        "     [bouton: نص الزر بالعربية]\n"
        "     Examples:\n"
        "     [bouton: 📈 Simuler un budget]\n"
        "     [bouton: 💡 Conseils d'épargne]\n"
        "     [bouton: 🔍 Analyser mes alertes]\n"
        "     [bouton: 💸 توزيع الفلوس على الأظرفة]\n\n"
        f"USER CONTEXT:\n{user_language_context}\n\n"
        f"USER FINANCIAL DATA (LIVE SNAPSHOT):\n{context_json}"
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
        # The raw provider error used to be printed straight into the chat
        # bubble - billing URLs, token accounting, internal messages and all.
        # Users get a sentence they can act on; the details go to the log, and
        # to superadmins, who are the only ones who can do anything with them.
        logger.warning("Advisor gateway failed: %s", exc, exc_info=True)

        seconds_until_reset: Optional[int] = None
        if isinstance(exc, AIGatewayQuotaError):
            seconds_until_reset = _advisor_retry_after_seconds(exc)
            if not seconds_until_reset:
                # A one-slot bucket per user: the first refusal opens the
                # window, every later attempt reads the same end time.
                quota_window = await check_rate_limit(
                    db,
                    key=f"advisor-quota:{current_user.id}",
                    limit=1,
                    window_seconds=ADVISOR_QUOTA_WINDOW_HOURS * 3600,
                )
                seconds_until_reset = quota_window.retry_after

        notice = _advisor_outage_notice(last_user_prompt, exc, seconds_until_reset)
        fallback_text = _generate_advisor_fallback_response(user_context, last_user_prompt)
        reply = f"{notice}\n\n---\n\n{fallback_text}"
        if current_user.role == "superadmin":
            reply = f"{reply}\n\n`[diagnostic superadmin] {str(exc)[:400]}`"

    # Store the exchange so every client of this account continues the same
    # conversation. Only the trailing user message is written: clients still
    # send their whole transcript, and persisting all of it would duplicate the
    # history on every turn.
    try:
        if last_user_prompt:
            db.add(
                AdvisorChatMessage(
                    user_id=current_user.id,
                    role="user",
                    text=last_user_prompt,
                )
            )
        db.add(
            AdvisorChatMessage(
                user_id=current_user.id,
                role="assistant",
                text=reply,
            )
        )
        await db.commit()
    except Exception:
        # A history that cannot be written must not cost the user the answer
        # they just waited for.
        await db.rollback()

    return AdvisorChatResponseOut(text=reply)


@router.get("/chat/history", response_model=list[AdvisorChatMessageOut])
async def advisor_chat_history(
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AdvisorChatMessageOut]:
    """The account's conversation, oldest first, shared by web and mobile."""
    result = await db.execute(
        select(AdvisorChatMessage)
        .where(AdvisorChatMessage.user_id == current_user.id)
        .order_by(AdvisorChatMessage.seq.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()
    return [AdvisorChatMessageOut.model_validate(m, from_attributes=True) for m in messages]


@router.delete("/chat/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_advisor_chat_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Clears the conversation for this account, on every device at once."""
    await db.execute(
        delete(AdvisorChatMessage).where(AdvisorChatMessage.user_id == current_user.id)
    )
    await db.commit()
