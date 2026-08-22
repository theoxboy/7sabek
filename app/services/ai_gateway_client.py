from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.platform_settings import get_platform_settings

AI_NOT_CONFIGURED_MESSAGE = (
    "AI Gateway is not configured. Please configure AI Gateway Hub in Superadmin Settings."
)


class AIGatewayConfigurationError(ValueError):
    pass


class AIGatewayUnsupportedProviderError(ValueError):
    pass


def _safe_string(value: Any) -> str:
    return str(value or "").strip()


def _gateway_endpoint(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _extract_json_payload(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty AI response")

    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\\n", "", raw)
        raw = re.sub(r"\\n```$", "", raw).strip()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(raw[start : end + 1])
        else:
            raise ValueError("AI response is not valid JSON")

    if not isinstance(payload, dict):
        raise ValueError("AI response JSON must be an object")
    return payload


def _normalize_suggestion_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    subject = _safe_string(payload.get("subject"))
    preview_text = _safe_string(payload.get("preview_text"))
    body = _safe_string(payload.get("body"))
    cta_label = _safe_string(payload.get("cta_label"))

    if not subject:
        raise ValueError("AI response missing subject")
    if not body:
        raise ValueError("AI response missing body")
    if not cta_label:
        raise ValueError("AI response missing cta_label")

    return {
        "subject": subject,
        "preview_text": preview_text,
        "body": body,
        "cta_label": cta_label,
    }


def _resolve_gateway(settings: Any) -> Tuple[Dict[str, Any], str]:
    ai_gateways = settings.ai_gateways if isinstance(settings.ai_gateways, list) else []
    ai_routing = settings.ai_routing if isinstance(settings.ai_routing, dict) else {}
    default_gateway_id = _safe_string(ai_routing.get("default_gateway_id"))

    enabled_gateways: List[Dict[str, Any]] = []
    for item in ai_gateways:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue
        if not _safe_string(item.get("base_url")):
            continue
        if not _safe_string(item.get("api_key")):
            continue
        enabled_gateways.append(item)

    selected: Optional[Dict[str, Any]] = None
    if default_gateway_id:
        for gateway in enabled_gateways:
            if _safe_string(gateway.get("id")) == default_gateway_id:
                selected = gateway
                break

    if selected is None and enabled_gateways:
        selected = enabled_gateways[0]

    if selected is None:
        raise AIGatewayConfigurationError(AI_NOT_CONFIGURED_MESSAGE)

    gateway_model = _safe_string(selected.get("model"))
    routing_default_model = _safe_string(ai_routing.get("default_model"))
    model = gateway_model or routing_default_model

    base_url = _safe_string(selected.get("base_url")).lower()
    provider = _safe_string(selected.get("provider")).lower()

    # Auto-heal placeholder, blank or invalid model string
    if not model or "..." in model or ("gpt" in model and "claude" in model and "gemini" in model):
        if "openrouter.ai" in base_url or provider == "openrouter":
            model = "google/gemini-2.0-flash-001"
        elif "generativelanguage.googleapis.com" in base_url or provider == "gemini":
            model = "gemini-2.0-flash"
        elif "groq.com" in base_url or provider == "groq":
            model = "llama-3.3-70b-versatile"
        elif "mistral.ai" in base_url or provider == "mistral":
            model = "mistral-small-latest"
        elif "anthropic.com" in base_url or provider == "anthropic":
            model = "claude-3-5-haiku-20241022"
        else:
            model = "gpt-4o-mini"

    return selected, model


def get_ai_gateway_status(settings: Any) -> Dict[str, Any]:
    ai_gateways = settings.ai_gateways if isinstance(settings.ai_gateways, list) else []
    ai_routing = settings.ai_routing if isinstance(settings.ai_routing, dict) else {}

    has_enabled_gateway = False
    has_model = False

    for item in ai_gateways:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue
        if _safe_string(item.get("base_url")) and _safe_string(item.get("api_key")):
            has_enabled_gateway = True
        if _safe_string(item.get("model")):
            has_model = True

    if _safe_string(ai_routing.get("default_model")):
        has_model = True

    return {
        "ai_gateway_configured": has_enabled_gateway,
        "ai_default_model_configured": has_model,
    }


def _build_system_prompt() -> str:
    return (
        "You are writing short product emails for 7sabek. "
        "Respect selected language. Darija should be natural Moroccan Arabic, not formal Arabic. "
        "French should be simple and clear. English should be simple and clear. "
        "Avoid spammy language. Avoid exaggerated financial promises. "
        "Do not mention exact financial amounts. Keep the body short. Include one clear CTA. "
        "Return JSON only with keys: subject, preview_text, body, cta_label."
    )


def _build_user_prompt(context: Dict[str, Any]) -> str:
    return json.dumps(context, ensure_ascii=False)


async def suggest_email_draft_via_gateway(
    db: AsyncSession,
    *,
    language: str,
    tone: str,
    goal: str,
    audience_type: str,
    cta_url: str,
    cta_label_hint: str,
    safe_user_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    settings = await get_platform_settings(db, create_if_missing=False)
    gateway, model = _resolve_gateway(settings)

    provider = _safe_string(gateway.get("provider")).lower()
    protocol = _safe_string(gateway.get("protocol")).lower()
    base_url = _safe_string(gateway.get("base_url"))
    api_key = _safe_string(gateway.get("api_key"))
    auth_header = _safe_string(gateway.get("auth_header")) or "Authorization"
    auth_scheme = _safe_string(gateway.get("auth_scheme")) or "Bearer"

    openai_compatible = protocol in {"openai_compatible", "azure_openai"} or provider in {
        "openai",
        "openrouter",
        "groq",
        "mistral",
        "perplexity",
        "custom",
        "azure_openai",
    }

    if not openai_compatible:
        raise AIGatewayUnsupportedProviderError(
            "Unsupported AI provider/protocol for Email Center suggestions. "
            "Please use an OpenAI-compatible gateway."
        )

    paths = gateway.get("paths") if isinstance(gateway.get("paths"), dict) else {}
    completions_path = _safe_string(paths.get("chat_completions") or paths.get("chat") or paths.get("completions"))
    endpoint = _gateway_endpoint(base_url, completions_path or "/chat/completions")

    extra_headers = gateway.get("extra_headers") if isinstance(gateway.get("extra_headers"), dict) else {}
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if auth_scheme:
        headers[auth_header] = "{0} {1}".format(auth_scheme, api_key)
    else:
        headers[auth_header] = api_key
    for key, value in extra_headers.items():
        clean_key = _safe_string(key)
        clean_value = _safe_string(value)
        if clean_key:
            headers[clean_key] = clean_value

    prompt_context: Dict[str, Any] = {
        "language": _safe_string(language).lower() or "fr",
        "tone": _safe_string(tone).lower() or "friendly",
        "goal": _safe_string(goal),
        "audience_type": _safe_string(audience_type).lower() or "test",
        "cta_url": _safe_string(cta_url),
        "cta_label_hint": _safe_string(cta_label_hint),
        "constraints": {
            "no_financial_amounts": True,
            "no_transactions_details": True,
            "short_body": True,
            "single_clear_cta": True,
        },
    }
    if safe_user_context:
        prompt_context["user_context"] = safe_user_context

    payload = {
        "model": model,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": _build_system_prompt()},
            {"role": "user", "content": _build_user_prompt(prompt_context)},
        ],
        "response_format": {"type": "json_object"},
    }

    timeout_ms_raw = 60000
    ai_routing = settings.ai_routing if isinstance(settings.ai_routing, dict) else {}
    try:
        timeout_ms_raw = int(ai_routing.get("request_timeout_ms") or 60000)
    except Exception:
        timeout_ms_raw = 60000
    timeout_s = max(1.0, min(float(timeout_ms_raw) / 1000.0, 120.0))

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("AI provider request failed.") from exc

    try:
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        raise ValueError("AI provider returned invalid JSON response.") from exc

    content = ""
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else {}
        if isinstance(message, dict):
            content = _safe_string(message.get("content"))

    if not content:
        raise ValueError("AI provider returned an empty response.")

    suggestion_raw = _extract_json_payload(content)
    return _normalize_suggestion_payload(suggestion_raw)


# ---------------------------------------------------------------------------
# Chat completion (conversational AI advisor) via gateway
# ---------------------------------------------------------------------------


async def chat_completion_via_gateway(
    db: AsyncSession,
    *,
    messages: list[dict[str, str]],
    system_prompt: Optional[str] = None,
) -> str:
    """Send a conversation to the configured AI gateway and return the text response.

    This function supports both ``native_gemini`` and OpenAI-compatible protocols.

    Parameters
    ----------
    db : AsyncSession
        Database session for reading platform settings.
    messages : list[dict[str, str]]
        Conversation history. Each dict must have ``role`` (``user``/``model``
        for Gemini, or ``user``/``assistant``/``system`` for OpenAI) and
        ``content`` (the message text).
    system_prompt : str | None
        Optional system-level instruction. Will be injected as a system message
        for OpenAI-compatible endpoints, or as ``system_instruction`` for
        native Gemini.

    Returns
    -------
    str
        The text content of the AI's response.

    Raises
    ------
    AIGatewayConfigurationError
        If no active gateway is configured.
    AIGatewayUnsupportedProviderError
        If the provider/protocol is not supported for chat.
    ValueError
        If the API request fails or returns an empty/invalid response.
    """
    settings = await get_platform_settings(db, create_if_missing=False)
    gateway, model = _resolve_gateway(settings)

    provider = _safe_string(gateway.get("provider")).lower()
    protocol = _safe_string(gateway.get("protocol")).lower()
    base_url = _safe_string(gateway.get("base_url"))
    api_key = _safe_string(gateway.get("api_key"))
    auth_header = _safe_string(gateway.get("auth_header")) or "Authorization"
    auth_scheme = _safe_string(gateway.get("auth_scheme")) or "Bearer"
    paths = gateway.get("paths") if isinstance(gateway.get("paths"), dict) else {}

    is_native_gemini = protocol == "native_gemini" or provider == "gemini"

    if is_native_gemini:
        return await _chat_completion_gemini(
            base_url=base_url,
            api_key=api_key,
            model=model,
            auth_header=auth_header,
            auth_scheme=auth_scheme,
            paths=paths,
            messages=messages,
            system_prompt=system_prompt,
            settings=settings,
        )

    # Default: OpenAI-compatible / general protocol
    return await _chat_completion_openai(
        base_url=base_url,
        api_key=api_key,
        model=model,
        auth_header=auth_header,
        auth_scheme=auth_scheme,
        paths=paths,
        messages=messages,
        system_prompt=system_prompt,
        settings=settings,
    )


async def _chat_completion_gemini(
    base_url: str,
    api_key: str,
    model: str,
    auth_header: str,
    auth_scheme: str,
    paths: dict[str, Any],
    messages: list[dict[str, str]],
    system_prompt: Optional[str],
    settings: Any,
) -> str:
    """Invoke a native Gemini generation endpoint."""
    # Map message roles: user -> user, model/assistant -> model
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = _safe_string(msg.get("content", ""))
        if not content:
            continue
        gemini_role = "model" if role in ("model", "assistant") else "user"
        contents.append({
            "role": gemini_role,
            "parts": [{"text": content}],
        })

    # Build the request payload
    payload: dict[str, Any] = {
        "contents": contents,
    }

    if system_prompt:
        payload["system_instruction"] = {
            "parts": [{"text": system_prompt}],
        }

    # Resolve the endpoint URL
    gemini_path = _safe_string(paths.get("generate_content") or paths.get("chat") or "")
    if not gemini_path:
        # Default Gemini API path for generateContent
        gemini_path = f"/v1beta/models/{model}:generateContent"
    endpoint = _gateway_endpoint(base_url, gemini_path)

    # Build headers
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_scheme and auth_scheme.lower() == "bearer":
        headers[auth_header] = f"Bearer {api_key}"
    else:
        headers[auth_header] = api_key

    extra_headers = settings.ai_routing.get("extra_headers") if isinstance(settings.ai_routing, dict) else {}
    if isinstance(extra_headers, dict):
        for key, value in extra_headers.items():
            clean_key = _safe_string(key)
            clean_value = _safe_string(value)
            if clean_key:
                headers[clean_key] = clean_value

    timeout_s = _resolve_timeout(settings)

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body_preview = ""
        try:
            body_preview = exc.response.text[:500]
        except Exception:
            pass
        raise ValueError(
            f"Gemini API returned {exc.response.status_code}: "
            f"{body_preview or str(exc)}"
        ) from exc
    except Exception as exc:
        raise ValueError(f"Gemini API request failed: {exc}") from exc

    try:
        data = response.json()
    except Exception as exc:
        raise ValueError("Gemini provider returned invalid JSON response.") from exc

    # Extract text from Gemini response structure
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list) and candidates:
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        content_block = first.get("content") if isinstance(first, dict) else {}
        if isinstance(content_block, dict):
            parts = content_block.get("parts") if isinstance(content_block, dict) else []
            if isinstance(parts, list):
                texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
                combined = "".join(texts).strip()
                if combined:
                    return combined

    raise ValueError("Gemini provider returned an empty response.")


async def _chat_completion_openai(
    base_url: str,
    api_key: str,
    model: str,
    auth_header: str,
    auth_scheme: str,
    paths: dict[str, Any],
    messages: list[dict[str, str]],
    system_prompt: Optional[str],
    settings: Any,
) -> str:
    """Invoke an OpenAI-compatible chat completions endpoint."""
    # Build OpenAI-style messages array
    openai_messages: list[dict[str, str]] = []

    if system_prompt:
        openai_messages.append({"role": "system", "content": system_prompt})

    for msg in messages:
        role = msg.get("role", "user")
        content = _safe_string(msg.get("content", ""))
        if not content:
            continue
        # Map Gemini-style model role to assistant
        if role in ("model", "assistant"):
            openai_role = "assistant"
        else:
            openai_role = "user"
        openai_messages.append({"role": openai_role, "content": content})

    completions_path = _safe_string(
        paths.get("chat_completions") or paths.get("chat") or paths.get("completions") or "/chat/completions"
    )
    endpoint = _gateway_endpoint(base_url, completions_path)

    # Build headers
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if auth_scheme:
        headers[auth_header] = f"{auth_scheme} {api_key}" if auth_scheme != "None" else api_key
    else:
        headers[auth_header] = api_key

    # Merge extra headers from gateway paths first, then fall back to ai_routing
    extra_headers = (
        paths.get("extra_headers")
        if isinstance(paths.get("extra_headers"), dict)
        else None
    )
    if not extra_headers:
        ai_routing = settings.ai_routing if isinstance(settings.ai_routing, dict) else {}
        extra_headers = (
            ai_routing.get("extra_headers")
            if isinstance(ai_routing.get("extra_headers"), dict)
            else {}
        )
    for key, value in extra_headers.items():
        clean_key = _safe_string(key)
        clean_value = _safe_string(value)
        if clean_key:
            headers[clean_key] = clean_value

    # Add OpenRouter specific headers if targeting openrouter.ai
    if "openrouter.ai" in base_url.lower():
        if "HTTP-Referer" not in headers and "http-referer" not in headers:
            headers["HTTP-Referer"] = "https://7sabek.ma"
        if "X-Title" not in headers and "x-title" not in headers:
            headers["X-Title"] = "7sabek Floussy"

    payload: dict[str, Any] = {
        "model": model,
        "messages": openai_messages,
    }

    timeout_s = _resolve_timeout(settings)

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Try to extract the response body for debugging
        body_preview = ""
        try:
            body_preview = exc.response.text[:500]
        except Exception:
            pass
        raise ValueError(
            f"AI provider returned {exc.response.status_code}: "
            f"{body_preview or str(exc)}"
        ) from exc
    except Exception as exc:
        raise ValueError(f"AI provider request failed: {exc}") from exc

    try:
        data = response.json()
    except Exception as exc:
        raise ValueError("AI provider returned invalid JSON response.") from exc

    # Extract from OpenAI-style response
    content = ""
    choices = data.get("choices") if isinstance(data, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else {}
        if isinstance(message, dict):
            content = _safe_string(message.get("content"))

    if not content:
        raise ValueError("AI provider returned an empty response.")

    return content


def _resolve_timeout(settings: Any) -> float:
    """Extract the request timeout from settings, capped at 18s to avoid App Platform 504 timeouts."""
    timeout_ms_raw = 18000
    ai_routing = settings.ai_routing if isinstance(settings.ai_routing, dict) else {}
    try:
        timeout_ms_raw = int(ai_routing.get("request_timeout_ms") or 18000)
    except Exception:
        timeout_ms_raw = 18000
    return max(1.0, min(float(timeout_ms_raw) / 1000.0, 20.0))
