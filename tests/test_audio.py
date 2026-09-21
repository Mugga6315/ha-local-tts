"""Tests for audio.py: sentence splitting, prefetching, Markdown cleanup, WAV helpers.

Run: uv run --with pytest pytest tests"""

import asyncio
import io
import wave

import pytest

from audio import prefetched, sentences, speakable, stream_header, wav_parts


async def _chunks(*parts: str):
    for part in parts:
        yield part


async def _collect(gen) -> list:
    return [item async for item in gen]


# --- sentences ---

def test_sentences_reassemble_text_split_mid_sentence():
    text = _chunks("Hallo Welt. Wie ", "geht es dir?", " Gut!\nNeue Zeile", " hier")
    out = asyncio.run(_collect(sentences(text)))
    assert out == ["Hallo Welt.", "Wie geht es dir?", "Gut!", "Neue Zeile hier"]


def test_sentences_yield_first_sentence_before_rest_arrives_despite_leading_blank_lines():
    # LLM replies open with "\n\n". The first sentence must still come out as soon
    # as it is complete, or TTS only starts once the whole answer exists.
    seen = []

    async def reply():
        yield "\n\nErster Satz."
        seen.append("second chunk requested")
        yield " Zweiter Satz."

    async def consume():
        async for sentence in sentences(reply()):
            seen.append(sentence)

    asyncio.run(consume())
    assert seen == ["Erster Satz.", "second chunk requested", "Zweiter Satz."]


# --- prefetched ---

class SlowFetch:
    """Fake synth: three parts per item, records overlap and cancellation."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.started: list[str] = []
        self.cancelled: list[str] = []

    async def __call__(self, item: str):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.append(item)
        try:
            for part in range(3):
                await asyncio.sleep(0.01)
                yield f"{item}.{part}"
        except asyncio.CancelledError:
            self.cancelled.append(item)
            raise
        finally:
            self.active -= 1


async def _items(count: int):
    for i in range(count):
        yield f"s{i}"


def test_prefetched_keeps_item_order():
    out = asyncio.run(_collect(prefetched(_items(4), SlowFetch(), depth=2)))
    assert out == [f"s{i}.{part}" for i in range(4) for part in range(3)]


def test_prefetched_overlaps_fetches_up_to_depth():
    fetch = SlowFetch()
    asyncio.run(_collect(prefetched(_items(4), fetch, depth=2)))
    assert fetch.peak == 2


def test_prefetched_close_cancels_in_flight_fetches():
    fetch = SlowFetch()

    async def read_one_then_close():
        stream = prefetched(_items(4), fetch, depth=2)
        assert await stream.__anext__() == "s0.0"
        await asyncio.sleep(0.005)  # let the prefetch of s1 start
        await stream.aclose()

    asyncio.run(read_one_then_close())
    assert "s1" in fetch.started and "s1" in fetch.cancelled
    assert fetch.active == 0


# --- speakable ---

@pytest.mark.parametrize(("text", "expected"), [
    ("Es sind **20 Grad**.", "Es sind 20 Grad."),
    ("- **Lampe** (Flur)", "Lampe (Flur)"),
    ("## Wetter", "Wetter"),
    ("*kursiv* und `code` und __fett__", "kursiv und code und fett"),
    ("switch.test_heater ist an", "switch.test_heater ist an"),  # _ inside a word is text
    ("Temperatur: -3 Grad", "Temperatur: -3 Grad"),  # minus sign is not a bullet
    ("**", ""),
])
def test_speakable_removes_markdown_markers_only(text, expected):
    assert speakable(text) == expected


# --- WAV helpers ---

def test_wav_parts_round_trips_a_wav():
    pcm = b"\x01\x00" * 2400  # 0.1 s at 24 kHz, 16-bit mono
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)
    assert wav_parts(buf.getvalue()) == (24000, 2, 1, pcm)


def test_wav_parts_returns_none_for_non_wav():
    assert wav_parts(b"not a wav") is None


def test_stream_header_is_a_44_byte_riff_wave_header():
    header = stream_header(24000, 2, 1)
    assert header[:4] == b"RIFF" and header[8:12] == b"WAVE" and len(header) == 44
