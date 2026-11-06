import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from acbgen import END, build_acb, afs2, note_on, u16s
from acb import acb


def sample_acb():
    # Five waveforms in the embedded AWB; waveform 4 is not used by any cue.
    awb = afs2([(i, f"HCA-{i}".encode()) for i in range(5)])
    waveforms = [{"MemoryAwbId": i, "StreamAwbId": 0xFFFF, "EncodeType": 2, "Streaming": 0} for i in range(5)]
    synths = [
        {"ReferenceItems": u16s(1, 0)},  # synth0 -> wav0 (shared by two cues)
        {"ReferenceItems": u16s(1, 1)},  # synth1 -> wav1
        {"ReferenceItems": u16s(1, 2, 1, 3)},  # synth2 -> random(wav2, wav3)
    ]
    # Sequence order deliberately differs from cue order, so ReferenceIndex != CueIndex.
    sequences = [
        {"NumTracks": 1, "TrackIndex": u16s(3)},  # seq0: used by cue "voice_c"
        {"NumTracks": 1, "TrackIndex": u16s(0)},  # seq1: used by cue "voice_a"
        {"NumTracks": 2, "TrackIndex": u16s(1, 2)},  # seq2: used by cue "voice_b"
    ]
    tracks = [{"EventIndex": i} for i in range(4)]
    events = [
        {"Command": note_on(2, 0) + END},
        {"Command": note_on(2, 1) + END},
        {"Command": note_on(2, 2) + END},
        {"Command": note_on(2, 0) + END},
    ]
    cues = [
        {"CueId": 10, "ReferenceType": 3, "ReferenceIndex": 1, "NumRelatedWaveforms": 1},
        {"CueId": 11, "ReferenceType": 3, "ReferenceIndex": 2, "NumRelatedWaveforms": 3},
        {"CueId": 12, "ReferenceType": 3, "ReferenceIndex": 0, "NumRelatedWaveforms": 1},
    ]
    names = [
        {"CueName": "voice_a", "CueIndex": 0},
        {"CueName": "voice_b", "CueIndex": 1},
        {"CueName": "voice_c", "CueIndex": 2},
    ]
    return build_acb(
        cues=cues, names=names, waveforms=waveforms, synths=synths,
        sequences=sequences, tracks=tracks, events=events, awb=awb,
    )


def test_resolves_graph():
    with acb.ACBFile(io.BytesIO(sample_acb())) as f:
        got = {t.name: bytes(f.get_track_data(t)) for t in f.track_list.tracks}
        assert got == {
            "voice_a": b"HCA-0",
            "voice_b_01": b"HCA-1",
            "voice_b_02": b"HCA-2",
            "voice_b_03": b"HCA-3",
            "voice_c": b"HCA-0",
            "UNREFERENCED_4": b"HCA-4",
        }, got
        assert f.track_list.unreferenced_waveforms == [4]


def test_extract_writes_every_track():
    with tempfile.TemporaryDirectory() as d:
        acb.extract_acb(io.BytesIO(sample_acb()), d)
        assert sorted(os.listdir(d)) == sorted(
            ["voice_a.hca", "voice_b_01.hca", "voice_b_02.hca", "voice_b_03.hca", "voice_c.hca", "UNREFERENCED_4.hca"]
        )


if __name__ == "__main__":
    test_resolves_graph()
    test_extract_writes_every_track()
    print("ok")
