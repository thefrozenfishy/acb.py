"""Tiny @UTF / ACB / AFS2 writer used to build synthetic test ACBs."""

import struct

T_U8, T_U16, T_U32, T_STR, T_DATA = 0x00, 0x02, 0x04, 0x0A, 0x0B
_FMT = {T_U8: "B", T_U16: "H", T_U32: "I"}


def utf_table(name, columns, rows, zero_columns=()):
    """columns: list of (name, type). rows: list of dicts. zero_columns: (name, type) stored as 0x10."""
    strings = bytearray(b"<NULL>\0")
    str_off = {}

    def s(v):
        if v not in str_off:
            str_off[v] = len(strings)
            strings.extend(v.encode() + b"\0")
        return str_off[v]

    data = bytearray()
    name_off = s(name)
    schema = bytearray()
    for cname, ctype in zero_columns:
        schema += struct.pack(">BI", 0x10 | ctype, s(cname))
    for cname, ctype in columns:
        schema += struct.pack(">BI", 0x50 | ctype, s(cname))

    rowbytes = bytearray()
    for r in rows:
        for cname, ctype in columns:
            v = r[cname]
            if ctype in _FMT:
                rowbytes += struct.pack(">" + _FMT[ctype], v)
            elif ctype == T_STR:
                rowbytes += struct.pack(">I", s(v))
            elif ctype == T_DATA:
                rowbytes += struct.pack(">II", len(data), len(v))
                data.extend(v)
    row_size = len(rowbytes) // max(len(rows), 1)

    header_len = 0x18  # from offset 8 to the schema at 0x20
    row_offset = header_len + len(schema)
    string_offset = row_offset + len(rowbytes)
    data_offset = string_offset + len(strings)
    body = (
        struct.pack(
            ">HHIIIHHI",
            1,
            row_offset,
            string_offset,
            data_offset,
            name_off,
            len(columns) + len(zero_columns),
            row_size,
            len(rows),
        )
        + schema
        + rowbytes
        + strings
        + data
    )
    return b"@UTF" + struct.pack(">I", len(body)) + body


def afs2(files, align=32):
    """files: list of (id, bytes). 2-byte ids, 4-byte offsets."""
    n = len(files)
    head = bytearray(b"AFS2" + bytes([2, 4, 2, 0]) + struct.pack("<IHH", n, align, 0))
    head += b"".join(struct.pack("<H", i) for i, _ in files)
    off_pos = len(head)
    head += b"\0" * (4 * (n + 1))
    blob = bytearray(head)
    offs = []
    for _, d in files:
        offs.append(len(blob))
        blob += b"\0" * ((-len(blob)) % align)
        blob += d
    offs.append(len(blob))
    struct.pack_into(f"<{n + 1}I", blob, off_pos, *offs)
    return bytes(blob)


def u16s(*v):
    return struct.pack(f">{len(v)}H", *v)


def note_on(ref_type, idx):
    return struct.pack(">HB", 2000, 4) + u16s(ref_type, idx)


END = struct.pack(">HB", 0, 0)


def build_acb(*, cues, names, waveforms, synths, sequences=(), tracks=(), events=(), awb=b""):
    cue_t = utf_table(
        "Cue",
        [("CueId", T_U32), ("ReferenceType", T_U8), ("ReferenceIndex", T_U16), ("NumRelatedWaveforms", T_U16)],
        cues,
    )
    nam_t = utf_table("CueName", [("CueName", T_STR), ("CueIndex", T_U16)], names)
    # "Id" is stored as a zero column, which is what newer ACBs look like.
    wav_t = utf_table(
        "Waveform",
        [("MemoryAwbId", T_U16), ("StreamAwbId", T_U16), ("EncodeType", T_U8), ("Streaming", T_U8)],
        waveforms,
        zero_columns=[("Id", T_U16)],
    )
    syn_t = utf_table("Synth", [("ReferenceItems", T_DATA)], synths)
    seq_t = utf_table("Sequence", [("NumTracks", T_U16), ("TrackIndex", T_DATA)], list(sequences))
    trk_t = utf_table("Track", [("EventIndex", T_U16)], list(tracks))
    ev_t = utf_table("TrackEvent", [("Command", T_DATA)], list(events))
    return utf_table(
        "Header",
        [
            ("CueTable", T_DATA),
            ("CueNameTable", T_DATA),
            ("WaveformTable", T_DATA),
            ("SynthTable", T_DATA),
            ("SequenceTable", T_DATA),
            ("TrackTable", T_DATA),
            ("TrackEventTable", T_DATA),
            ("AwbFile", T_DATA),
        ],
        [
            {
                "CueTable": cue_t,
                "CueNameTable": nam_t,
                "WaveformTable": wav_t,
                "SynthTable": syn_t,
                "SequenceTable": seq_t,
                "TrackTable": trk_t,
                "TrackEventTable": ev_t,
                "AwbFile": awb,
            }
        ],
    )
