# acb.py: For all your ACB extracting needs

# Copyright (c) 2016, The Holy Constituency of the Summer Triangle.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# This program is based on code from VGMToolbox.
# Copyright (c) 2009
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation
# files (the "Software"), to deal in the Software without
# restriction, including without limitation the rights to use,
# copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following
# conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
# OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
# OTHER DEALINGS IN THE SOFTWARE.

import io
import struct
import itertools
import os
import re
from collections import namedtuple as T
from collections.abc import Callable

from .utf import UTFTable, R
from .disarm import DisarmContext

# fmt: off
WAVEFORM_ENCODE_TYPE_ADX          = 0
WAVEFORM_ENCODE_TYPE_HCA          = 2
WAVEFORM_ENCODE_TYPE_VAG          = 7
WAVEFORM_ENCODE_TYPE_ATRAC3       = 8
WAVEFORM_ENCODE_TYPE_BCWAV        = 9
WAVEFORM_ENCODE_TYPE_NINTENDO_DSP = 13

wave_type_ftable = {
    WAVEFORM_ENCODE_TYPE_ADX          : ".adx",
    WAVEFORM_ENCODE_TYPE_HCA          : ".hca",
    WAVEFORM_ENCODE_TYPE_VAG          : ".vag",
    WAVEFORM_ENCODE_TYPE_ATRAC3       : ".at3",
    WAVEFORM_ENCODE_TYPE_BCWAV        : ".bcwav",
    WAVEFORM_ENCODE_TYPE_NINTENDO_DSP : ".dsp"
}
# fmt: on
track_t = T(
    "track_t",
    ("cue_id", "name", "enc_type", "is_stream"),
)


REFERENCE_WAVEFORM = 1
REFERENCE_SYNTH = 2
REFERENCE_SEQUENCE = 3
REFERENCE_BLOCK_SEQUENCE = 8

# Track event commands that start playback of a synth/sequence.
COMMAND_NOTE_ON = 2000
COMMAND_NOTE_ON_WITH_NO = 2003
COMMAND_END = 0


def _u16_list(data) -> list[int]:
    if not data:
        return []
    return list(struct.unpack(f">{len(data) // 2}H", data[: len(data) // 2 * 2]))


def _ref_items(data) -> list[tuple[int, int]]:
    """Synth ReferenceItems: a packed list of (u16 type, u16 index) pairs."""
    vals = _u16_list(data)
    return list(zip(vals[0::2], vals[1::2]))


def _iter_commands(data):
    """Track event command stream: repeated (u16 code, u8 size, payload)."""
    pos = 0
    while pos + 3 <= len(data):
        code, size = struct.unpack_from(">HB", data, pos)
        pos += 3
        yield code, data[pos : pos + size]
        pos += size
        if code == COMMAND_END:
            return


def _table(utf, name, encoding):
    raw = utf.rows[0].get(name)
    if not raw:
        return []
    return UTFTable(io.BytesIO(raw), encoding=encoding).rows


class TrackList(object):
    """Resolves every cue to the waveform(s) it can play.

    Walks Cue -> (Waveform | Synth | Sequence | BlockSequence) -> Track ->
    TrackEvent noteOn -> Synth -> Waveform, the same graph the CRI runtime uses.

    After construction:
    - tracks: one track_t per (cue, distinct waveform) pair.
    - unreferenced_waveforms: waveform indexes no cue reaches. These are still
      present in the AWB; they are exported as tracks named "UNREFERENCED_<id>".
    - warnings: human-readable notes about anything odd in the file.
    """

    def __init__(self, utf):
        enc = utf.encoding
        cues = _table(utf, "CueTable", enc)
        nams = _table(utf, "CueNameTable", enc)
        self.wavs = wavs = _table(utf, "WaveformTable", enc)
        self.syns = _table(utf, "SynthTable", enc)
        self.seqs = _table(utf, "SequenceTable", enc)
        self.trks = _table(utf, "TrackTable", enc)
        self.blk_seqs = _table(utf, "BlockSequenceTable", enc)
        self.blks = _table(utf, "BlockTable", enc)
        self.events = _table(utf, "TrackEventTable", enc) or _table(utf, "CommandTable", enc)

        self.tracks: list[track_t] = []
        self.warnings: list[str] = []

        # CueNameTable.CueIndex is a row index into CueTable (not ReferenceIndex).
        name_map = {row["CueIndex"]: row["CueName"] for row in nams}

        referenced: set[int] = set()
        used_names: set[str] = set()
        for cue_index, row in enumerate(cues):
            cue_name = name_map.get(cue_index) or f"UNKNOWN_{row.get('CueId', cue_index)}"
            wav_indexes = self._resolve(row["ReferenceType"], row["ReferenceIndex"], set())
            # The same audio can be reachable through several paths, and separate Waveform rows
            # can point at the same AWB entry; export each distinct AWB entry once per cue.
            wav_indexes = list(dict.fromkeys(wav_indexes))
            seen_audio = set()
            unique = []
            for w in wav_indexes:
                k = self._audio_key(w)
                if k not in seen_audio:
                    seen_audio.add(k)
                    unique.append(w)
            wav_indexes = unique

            expected = row.get("NumRelatedWaveforms")
            if expected is not None and expected != len(wav_indexes):
                self.warnings.append(
                    f"cue {cue_name}: NumRelatedWaveforms={expected} but resolved {len(wav_indexes)}"
                )
            if not wav_indexes:
                self.warnings.append(f"cue {cue_name}: references no waveforms")

            for i, w in enumerate(wav_indexes):
                referenced.add(w)
                name = cue_name if len(wav_indexes) == 1 else f"{cue_name}_{i + 1:02}"
                self._add_track(w, name, used_names)

        played = {self._audio_key(i) for i in referenced}
        self.unreferenced_waveforms = []
        for i in range(len(wavs)):
            if i not in referenced and self._audio_key(i) not in played:
                played.add(self._audio_key(i))
                self.unreferenced_waveforms.append(i)
        for w in self.unreferenced_waveforms:
            self.warnings.append(f"waveform {w} is not referenced by any cue")
            self._add_track(w, f"UNREFERENCED_{w}", used_names)

    def _audio_key(self, w):
        wav = self.wavs[w]
        if wav.get("Streaming"):
            return ("stream", wav.get("StreamAwbId"))
        mem_id = wav.get("Id")
        return ("memory", wav.get("MemoryAwbId") if mem_id is None else mem_id)

    def _add_track(self, w, name, used_names):
        wav = self.wavs[w]
        mem_id = wav.get("Id")
        if mem_id is None:
            mem_id = wav.get("MemoryAwbId")
        is_stream = wav.get("Streaming", 0)
        # Streaming=2 means a short memory preload plus the full stream; the stream is the real file.
        awb_id = wav.get("StreamAwbId") if is_stream else mem_id
        if name in used_names:
            name = f"{name}__{w}"
        used_names.add(name)
        self.tracks.append(track_t(awb_id, name, wav["EncodeType"], bool(is_stream)))

    def _resolve(self, ref_type, index, seen) -> list[int]:
        key = (ref_type, index)
        if key in seen:  # guard against reference loops
            return []
        seen = seen | {key}

        if ref_type == REFERENCE_WAVEFORM:
            if index >= len(self.wavs):
                self.warnings.append(f"waveform index {index} out of range")
                return []
            return [index]

        if ref_type == REFERENCE_SYNTH:
            if index >= len(self.syns):
                self.warnings.append(f"synth index {index} out of range")
                return []
            out = []
            for t, i in _ref_items(self.syns[index].get("ReferenceItems")):
                out += self._resolve(t, i, seen)
            return out

        if ref_type == REFERENCE_SEQUENCE:
            if index >= len(self.seqs):
                self.warnings.append(f"sequence index {index} out of range")
                return []
            return self._resolve_tracks(_u16_list(self.seqs[index].get("TrackIndex")), seen)

        if ref_type == REFERENCE_BLOCK_SEQUENCE:
            if index >= len(self.blk_seqs):
                self.warnings.append(f"block sequence index {index} out of range")
                return []
            bs = self.blk_seqs[index]
            out = self._resolve_tracks(_u16_list(bs.get("TrackIndex")), seen)
            for b in _u16_list(bs.get("BlockIndex")):
                if b < len(self.blks):
                    out += self._resolve_tracks(_u16_list(self.blks[b].get("TrackIndex")), seen)
            return out

        if ref_type == 0:  # empty reference
            return []
        self.warnings.append(f"ReferenceType {ref_type} not implemented")
        return []

    def _resolve_tracks(self, track_indexes, seen) -> list[int]:
        out = []
        for t in track_indexes:
            if t >= len(self.trks):
                self.warnings.append(f"track index {t} out of range")
                continue
            ev = self.trks[t].get("EventIndex", self.trks[t].get("CommandIndex"))
            if ev is None or ev == 0xFFFF or ev >= len(self.events):
                continue
            for code, payload in _iter_commands(self.events[ev].get("Command") or b""):
                if code in (COMMAND_NOTE_ON, COMMAND_NOTE_ON_WITH_NO) and len(payload) >= 4:
                    tlv_type, tlv_index = struct.unpack_from(">HH", payload)
                    out += self._resolve(tlv_type, tlv_index, seen)
        return out


def align(n):
    def _align(number):
        return (number + n - 1) & ~(n - 1)

    return _align


afs2_file_ent_t = T("afs2_file_ent_t", ("cue_id", "offset", "size"))


class AFSArchive(object):
    def __init__(self, file: io.BufferedIOBase, *, encoding: str | None = None):
        # Note: we don't do anything involving strings here so encoding is not actually required
        buf = R(file, encoding=encoding or "utf-8")

        magic = buf.uint32_t()
        if magic != 0x41465332:
            raise ValueError("bad magic")

        version = buf.bytes(4)
        file_count = buf.le_uint32_t()

        if version[0] >= 0x02:
            self.alignment: int = buf.le_uint16_t()
            self.mix_key: int | None = buf.le_uint16_t()
        else:
            self.alignment: int = buf.le_uint32_t()
            self.mix_key: int | None = None

        # print("afs2:", file_count, "files in ar")
        # print("afs2: aligned to", self.alignment, "bytes")

        self.offset_size: int = version[1]
        self.offset_mask: int = int("FF" * self.offset_size, 16)
        cue_id_size = version[2]
        # print("afs2: a file offset is", self.offset_size, "bytes")

        self.files: list[afs2_file_ent_t] = []
        self.create_file_entries(
            buf, file_count, cue_id_size, self.offset_size, self.offset_mask
        )
        self.src = buf

    def _struct_format(self, size):
        if size == 2:
            return "H"
        elif size == 4:
            return "I"
        raise ValueError(f"Cannot deal with size {size} at this time")

    def create_file_entries(
        self, buf, file_count, cue_id_size, offset_size, offset_mask
    ):
        buf.seek(0x10)
        read_cue_ids = struct.Struct(
            "<" + (self._struct_format(cue_id_size) * file_count)
        )
        read_raw_offs = struct.Struct(
            "<" + (self._struct_format(offset_size) * (file_count + 1))
        )
        # read all in one go
        cue_ids = buf.struct(read_cue_ids)
        raw_offs = buf.struct(read_raw_offs)
        # apply the mask
        unaligned_offs = tuple(map(lambda x: x & offset_mask, raw_offs))
        aligned_offs = tuple(map(align(self.alignment), unaligned_offs))
        offsets_for_length_calculating = unaligned_offs[1:]
        lengths = itertools.starmap(
            lambda my_offset, next_offset: next_offset - my_offset,
            zip(aligned_offs, offsets_for_length_calculating),
        )

        self.files = list(
            itertools.starmap(afs2_file_ent_t, zip(cue_ids, aligned_offs, lengths))
        )

    def file_data_for_cue_id(self, cue_id, rw=False):
        for f in self.files:
            if f.cue_id == cue_id:
                if rw:
                    buf = bytearray(f.size)
                    self.src.bytesinto(buf, at=f.offset)
                    return buf
                else:
                    return self.src.bytes(f.size, at=f.offset)
        raise ValueError(f"id {cue_id} not found in archive")


AnyFile = str | os.PathLike | io.BufferedIOBase
Uninitialized = object()


def _get_file_obj(name: AnyFile) -> tuple[io.BufferedIOBase, bool]:
    if isinstance(name, (str, os.PathLike)):
        return open(name, "rb"), True
    else:
        return name, False


class ACBFile(object):
    """Represents an ACB file.

    You should call the close() method to release the underlying file objects
    when finished, or use the object as a context manager.
    Tracks can be accessed using .track_list together with get_track_data().

    Constructor arguments:
    - acb_file: Path to the file, or an open file object in binary mode.
    - extern_awb: Path to the streaming AWB file, if needed.
    - hca_keys: HCA keys in string format. Can be one of:
        "0xLOWBYTES,0xHIGHBYTES", "0xHIGHBYTESLOWBYTES", or None.
        "0xLOWBYTES,0xHIGHBYTES" is equivalent to invoking hca_decoder
        with arguments "-a LOWBYTES -b HIGHBYTES".
        If None, HCA files will not be decrypted.
    - encoding: String encoding to use when reading the file. If this is
        not passed, we'll try reading it as UTF-8 first, and then Shift-JIS,
        before giving up. If an
        encoding is explicitly passed, only that encoding will be used.
    """

    def __init__(
        self,
        acb_file: AnyFile,
        extern_awb: AnyFile | None = None,
        hca_keys: str | None = None,
        encoding: str | None = None,
    ):
        self.acb_handle, self.acb_handle_owned = _get_file_obj(acb_file)

        if extern_awb is None:
            self.awb_handle = None
            self.awb_handle_owned = False
        else:
            self.awb_handle, self.awb_handle_owned = _get_file_obj(extern_awb)

        # UTF-8 first: Shift-JIS happily "decodes" UTF-8 bytes into mojibake, the reverse rarely succeeds.
        self.encoding = encoding or "utf-8"
        try:
            utf = UTFTable(self.acb_handle, encoding=self.encoding)
            self.track_list = TrackList(utf)
        except UnicodeDecodeError:
            if encoding is None:
                self.encoding = "sjis"
                self.acb_handle.seek(0)
                utf = UTFTable(self.acb_handle, encoding="sjis")
                self.track_list = TrackList(utf)
            else:
                raise

        if len(utf.rows[0]["AwbFile"]) > 0:
            self.embedded_awb = AFSArchive(
                io.BytesIO(utf.rows[0]["AwbFile"]), encoding=self.encoding
            )
        else:
            self.embedded_awb = None  # type: ignore

        if self.awb_handle:
            self.external_awb = AFSArchive(self.awb_handle, encoding=self.encoding)
        else:
            self.external_awb = None  # type: ignore

        self.hca_keys = hca_keys
        self.embedded_disarm: DisarmContext | None = Uninitialized  # type: ignore
        self.external_disarm: DisarmContext | None = Uninitialized  # type: ignore

        self.closed = False

    def get_embedded_disarm(self) -> DisarmContext | None:
        if self.embedded_disarm is Uninitialized:
            if self.hca_keys and self.embedded_awb:
                self.embedded_disarm = DisarmContext(
                    self.hca_keys, self.embedded_awb.mix_key
                )
            else:
                self.embedded_disarm = None

        return self.embedded_disarm

    def get_external_disarm(self) -> DisarmContext | None:
        if self.external_disarm is Uninitialized:
            if self.hca_keys and self.external_awb:
                self.external_disarm = DisarmContext(
                    self.hca_keys, self.external_awb.mix_key
                )
            else:
                self.external_disarm = None
        return self.external_disarm

    def get_track_data(
        self, track: track_t, disarm: bool | None = None, unmask: bool = True
    ) -> bytearray:
        """Gets encoded audio data as a bytearray.

        Arguments:
        - track: The track to get data for, from .track_list.
        - disarm: Whether to decrypt HCA data before returning it.
            The default action is to decrypt if hca_keys were passed when creating
            the ACBFile. You can pass False to skip decryption, or True to force
            decryption.
            ValueError is raised you force decryption and keys were not passed.
        - unmask: Whether to remove XOR masking from HCA header tags.
            This only has an effect if decryption is enabled, whether implicitly or
            explicitly.
        """
        if self.closed:
            raise ValueError("ACBFile is closed")

        if track.is_stream:
            if not self.external_awb:
                raise ValueError(
                    f"Track {track} is streamed, but there's no external AWB attached."
                )

            buf = self.external_awb.file_data_for_cue_id(track.cue_id, rw=True)
            disarmer = self.get_external_disarm()
        else:
            if not self.embedded_awb:
                raise ValueError(
                    f"Track {track} is internal, but this ACB file has no internal AWB."
                )

            buf = self.embedded_awb.file_data_for_cue_id(track.cue_id, rw=True)
            disarmer = self.get_embedded_disarm()

        if disarm is True and not disarmer:
            raise ValueError(
                "Disarm was explicitly requested, but no keys were provided. "
                "Either remove the disarm= argument from the call to get_track_data, "
                "or provide keys using the hca_keys= argument to ACBFile."
            )

        if disarm is None:
            disarm = disarmer is not None

        if disarm and disarmer:
            disarmer.disarm(buf, not unmask)

        return buf

    def __enter__(self):
        return self

    def __exit__(self, _1, _2, _3):
        self.close()

    def close(self):
        """Close any open files held by ACBFile. If you passed file objects
        instead of paths when creating the ACBFile instance, they will
        not be closed.

        Can be called multiple times; subsequent calls will have no effect.
        """
        if self.acb_handle_owned:
            self.acb_handle.close()
            self.acb_handle_owned = False
        if self.awb_handle_owned and self.awb_handle:
            self.awb_handle.close()
            self.awb_handle_owned = False
        self.closed = True

    def __del__(self):
        self.close()


def find_awb(path):
    if re.search(r"\.acb$", path):
        awb_path = re.sub(r"\.acb$", ".awb", path)
        if os.path.exists(awb_path):
            return awb_path


def name_gen_default(track):
    return f"{track.name}{wave_type_ftable.get(track.enc_type, track.enc_type)}"


def extract_acb(
    acb_file: AnyFile,
    target_dir: str,
    extern_awb: AnyFile | None = None,
    hca_keys: str | None = None,
    name_gen: Callable[[track_t], str] = name_gen_default,
    no_unmask: bool = False,
    encoding: str | None = None,
):
    """Oneshot file extraction API. Dumps all tracks from a file into the
    named output directory.

    Arguments:
    - acb_file: Path to the file, or an open file object in binary mode.
    - target_dir: Path to the destination directory. Must already exist.
    - extern_awb: Path to the streaming AWB file, if needed.
    - hca_keys: Same as ACBFile's hca_keys argument.
    - name_gen: A callable taking the track_t object and returning a
        destination filename. Should not return absolute paths, as
        they will be prefixed with the target_dir.
    - no_unmask: See ACBFile.get_track_data's unmask argument. For
        compatibility reasons, the meaning of this flag is reversed;
        i.e. True will result in unmasking being disabled.
    - encoding: Encoding used for track names. See ACBFile's docstring
        for behaviour when this argument is None/omitted.
    """
    if isinstance(acb_file, str) and extern_awb is None:
        extern_awb = find_awb(acb_file)

    with ACBFile(
        acb_file, extern_awb=extern_awb, hca_keys=hca_keys, encoding=encoding
    ) as acb:
        for track in acb.track_list.tracks:
            name = name_gen(track)

            with open(os.path.join(target_dir, name), "wb") as out_file:
                out_file.write(acb.get_track_data(track, unmask=not no_unmask))
