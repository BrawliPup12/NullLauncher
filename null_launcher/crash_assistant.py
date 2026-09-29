from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CrashDiagnosis:
    """A small, deterministic hint derived from the Minecraft log."""

    code: str
    message_key: str


_SIGNATURES: tuple[tuple[str, str, tuple[re.Pattern[str], ...]], ...] = (
    (
        "memory",
        "crash_hint_memory",
        (
            re.compile(r"OutOfMemoryError", re.I),
            re.compile(r"Java heap space", re.I),
            re.compile(r"GC overhead limit exceeded", re.I),
            re.compile(r"Could not reserve enough space for .* object heap", re.I),
        ),
    ),
    (
        "java",
        "crash_hint_java",
        (
            re.compile(r"UnsupportedClassVersionError", re.I),
            re.compile(r"class file version \d+\.\d+.*only recognizes", re.I),
            re.compile(r"has been compiled by a more recent version of the Java Runtime", re.I),
        ),
    ),
    (
        "mods",
        "crash_hint_mods",
        (
            re.compile(r"Mixin(?:Apply|Transformer|Initialisation)?Error", re.I),
            re.compile(r"ModResolutionException", re.I),
            re.compile(r"ModLoadingException", re.I),
            re.compile(r"Failed to load mods", re.I),
            re.compile(r"Could not execute entrypoint", re.I),
            re.compile(r"Incompatible mod set", re.I),
        ),
    ),
    (
        "native",
        "crash_hint_native",
        (
            re.compile(r"UnsatisfiedLinkError", re.I),
            re.compile(r"Failed to (?:load|locate).*\b(?:native|library)\b", re.I),
            re.compile(r"Couldn['’]?t load .*\b(?:dll|so|dylib)\b", re.I),
            re.compile(r"no lwjgl.* in java\.library\.path", re.I),
        ),
    ),
    (
        "graphics",
        "crash_hint_graphics",
        (
            re.compile(r"GLFW error 65542", re.I),
            re.compile(r"OpenGL.*(?:not supported|failed)", re.I),
            re.compile(r"Pixel format not accelerated", re.I),
        ),
    ),
)


def diagnose_minecraft_crash(log_text: str, *, max_hints: int = 2) -> list[CrashDiagnosis]:
    """Return at most a few high-confidence hints without guessing beyond the log."""
    text = str(log_text or "")
    if not text:
        return []
    result: list[CrashDiagnosis] = []
    for code, message_key, patterns in _SIGNATURES:
        if any(pattern.search(text) for pattern in patterns):
            result.append(CrashDiagnosis(code=code, message_key=message_key))
            if len(result) >= max(1, int(max_hints)):
                break
    return result
