"""Pure audio/text helpers for the streaming path — no Home Assistant imports,
so they are unit-testable on their own (see demo() at the bottom)."""

from __future__ import annotations

import io
import re
import wave
from typing import AsyncGenerator

# End of a sentence: run up to .!?… (plus trailing quotes/brackets) before a
# space or end, OR a newline. Keeps the delimiter with the sentence.
_SENTENCE_END = re.compile(r"[^.!?…\n]*(?:[.!?…]+[\"')\]]*(?=\s|$)|\n)", re.DOTALL)


async def sentences(message_gen: AsyncGenerator[str]) -> AsyncGenerator[str]:
    """Yield complete sentences as text arrives; flush the remainder at the end."""
    buf = ""
    async for chunk in message_gen:
        buf += chunk
        while True:
            m = _SENTENCE_END.match(buf)
            if not m or not m.group().strip():
                break
            sentence = m.group()
            buf = buf[m.end():]
            if sentence.strip():
                yield sentence.strip()
    if buf.strip():
        yield buf.strip()


def wav_parts(data: bytes) -> tuple[int, int, int, bytes] | None:
    """(sample_rate, sampwidth, channels, pcm_frames) from a WAV blob, or None."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            return (wf.getframerate(), wf.getsampwidth(), wf.getnchannels(),
                    wf.readframes(wf.getnframes()))
    except Exception:
        return None


def stream_header(sr: int, sampwidth: int, channels: int) -> bytes:
    """A WAV header with max-size placeholders for a stream of unknown length.
    ffmpeg `-f wav` (HA's converter) reads this from a pipe fine."""
    byte_rate = sr * channels * sampwidth
    block_align = channels * sampwidth
    return b"".join([
        b"RIFF", (0xFFFFFFFF).to_bytes(4, "little"), b"WAVE",
        b"fmt ", (16).to_bytes(4, "little"),
        (1).to_bytes(2, "little"), channels.to_bytes(2, "little"),
        sr.to_bytes(4, "little"), byte_rate.to_bytes(4, "little"),
        block_align.to_bytes(2, "little"), (sampwidth * 8).to_bytes(2, "little"),
        b"data", (0xFFFFFFFF).to_bytes(4, "little"),
    ])


def demo() -> None:
    """Self-check: sentence splitting + WAV header/parse round-trip."""
    import asyncio

    async def _gen(parts):
        for p in parts:
            yield p

    async def _collect():
        # Text split across chunks mid-sentence must reassemble into whole sentences.
        out = [s async for s in sentences(_gen(
            ["Hallo Welt. Wie ", "geht es dir?", " Gut!\nNeue Zeile", " hier"]))]
        assert out == ["Hallo Welt.", "Wie geht es dir?", "Gut!", "Neue Zeile hier"], out

    asyncio.run(_collect())

    # A real 24kHz mono 16-bit WAV parses back to its parameters and PCM length.
    pcm = (b"\x01\x00" * 2400)  # 0.1s
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    parts = wav_parts(buf.getvalue())
    assert parts is not None, "wav_parts returned None on a valid WAV"
    sr, sw, ch, frames = parts
    assert (sr, sw, ch) == (24000, 2, 1), (sr, sw, ch)
    assert frames == pcm, "PCM round-trip mismatch"

    hdr = stream_header(24000, 2, 1)
    assert hdr[:4] == b"RIFF" and hdr[8:12] == b"WAVE" and len(hdr) == 44, hdr
    assert wav_parts(b"not a wav") is None

    print("audio.py demo OK")


if __name__ == "__main__":
    demo()
