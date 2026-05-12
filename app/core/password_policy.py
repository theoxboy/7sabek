from __future__ import annotations

import re


_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_HAS_DIGIT_RE = re.compile(r"\d")

# Lightweight denylist of very common breached passwords.
_COMPROMISED_PASSWORDS = {
    "123456",
    "12345678",
    "123456789",
    "1234567890",
    "111111",
    "000000",
    "qwerty",
    "qwertyuiop",
    "password",
    "password1",
    "password123",
    "admin",
    "admin123",
    "welcome",
    "letmein",
    "iloveyou",
    "abc123",
    "123123",
}


def _normalize_password(value: str) -> str:
    return value.strip().lower()


def validate_password_easy(
    password: str,
    minimum_length: int,
) -> str | None:
    if len(password) < minimum_length:
        return f"Le mot de passe doit contenir au moins {minimum_length} caractères."
    if not _HAS_LETTER_RE.search(password):
        return "Le mot de passe doit contenir au moins une lettre."
    if not _HAS_DIGIT_RE.search(password):
        return "Le mot de passe doit contenir au moins un chiffre."
    if _normalize_password(password) in _COMPROMISED_PASSWORDS:
        return "Ce mot de passe est compromis. Merci d'en choisir un autre."
    return None
