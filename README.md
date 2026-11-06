# acb.py

Extract audio tracks from CRI ADX2 `.acb` / `.awb` files, the format many Unity and mobile games use for voice, music and sound effects. It can also decrypt HCA audio.

This is a maintained fork of [summertriangle-dev/acb.py](https://github.com/summertriangle-dev/acb.py), originally based on [VGMToolbox](https://sourceforge.net/projects/vgmtoolbox/). See [What this fork changes](#what-this-fork-changes) below.

## Install

Requires Python 3.10+ and a C compiler. The `_acb_speedup` extension is built during install: `build-essential` on Linux, the Xcode command line tools on macOS, or the "Desktop development with C++" workload of Visual Studio Build Tools on Windows.

```sh
pip install "acb-py @ git+https://github.com/thefrozenfishy/acb.py.git"
```

In a `requirements.txt`:

```
acb-py @ git+https://github.com/thefrozenfishy/acb.py.git
```

> `pip install acb-py` from PyPI installs the **original** package, not this fork.

## Usage

### Command line

```sh
acbextract somefile.acb output_dir
# same thing
python -m acb somefile.acb output_dir
```

- If `somefile.awb` sits next to the `.acb`, it is picked up automatically for streamed tracks. Use `--awb path/to/file.awb` if it is named or placed differently.
- `output_dir` is optional and defaults to the folder containing the `.acb`.
- Track names are read as UTF-8 first, then Shift-JIS. Use `--encoding` to force one.

### Python

```py
from acb import acb

# One-shot: write every track to a folder (the folder must exist).
acb.extract_acb("somefile.acb", "output_dir")

# More control:
with acb.ACBFile("somefile.acb", extern_awb="somefile.awb") as f:
    for track in f.track_list.tracks:
        data = f.get_track_data(track)  # bytes of the encoded .hca/.adx/...
        print(track.name, track.enc_type, track.is_stream, len(data))

    print(f.track_list.warnings)  # anything odd found while resolving cues
```

Each `track_t` has `name`, `enc_type` (2 = HCA, 0 = ADX, ...), `is_stream` (stored in the external `.awb` instead of inside the `.acb`), and `cue_id`, the entry id in that AWB.

### Output file names

- A cue with a single waveform is written as `<CueName>.<ext>`.
- A cue with several waveforms (random variations, layered sounds) is written as `<CueName>_01.<ext>`, `<CueName>_02.<ext>`, and so on.
- AWB audio that no cue refers to is still exported, as `UNREFERENCED_<n>.<ext>`, so no audio in the archive is dropped.
- If two tracks would get the same file name, the second gets a `__<n>` suffix instead of overwriting the first.

### Checking a folder of files

```sh
python -m acb.diagnose path/to/sounds        # only real problems
python -m acb.diagnose path/to/sounds -v     # also informational warnings
```

The script walks the folder and reports, for each ACB:
- crashes
- streamed tracks with no `.awb` next to them
- tracks whose AWB entry is missing
- data that doesn't start with an HCA header
- AWB entries that no cue uses

### Decryption

Pass keys to get decrypted (but still encoded) HCA files:

```sh
acbextract --disarm-with=0x<low32>,0x<high32> somefile.acb out
acbextract --disarm-with=0x<64-bit key> somefile.acb out
```

`--disarm-with=k1,k2` is equivalent to `hca_decoder -a k1 -b k2`. AWB embedded subkeys are supported ([background](https://github.com/hozuki/libcgss/issues/4)). In Python, pass `hca_keys="..."` to `ACBFile` or `extract_acb`. Add `--no-unmask` to keep the HCA header tags masked.

To turn `.hca` into `.wav`, use a decoder such as [vgmstream](https://github.com/vgmstream/vgmstream) (`vgmstream-cli -o out.wav in.hca`).

## What this fork changes

- **Correct cue resolution.** Tracks are found by following the same links the CRI runtime uses: Cue → Waveform / Synth / Sequence / BlockSequence → Track → TrackEvent `noteOn` → Synth → Waveform. Upstream assumed synth rows were in the same order as cues. That breaks as soon as a cue has several variations or two cues share a sound: every later cue ends up with its neighbour's audio.
- **Correct names.** Cue names are looked up by `CueNameTable.CueIndex` (the cue's row), not by `ReferenceIndex`.
- **Multi-waveform cues** (random or layered synths) are supported instead of crashing.
- **Nothing is silently dropped.** Unreferenced AWB entries are exported, duplicate names don't overwrite each other, and problems go to `track_list.warnings` instead of failing the whole file.
- **UTF table fixes.** Name-only (`0x10`) columns and default-plus-row (`0x70`) columns are now read correctly. Before, they shifted every later column in the row.
- **UTF-8 names by default**, because Shift-JIS decodes UTF-8 bytes into mojibake without raising an error.
- Builds with current GCC (14+) and Python versions.

## Tests

```sh
pip install -e .
python tests/test_tracklist.py
python tests/test_disarm.py
```

`tests/acbgen.py` is a small writer for synthetic `@UTF`/ACB/AFS2 files, so the tests need no game data.

## Credits

- Original library by The Holy Constituency of the Summer Triangle.
- Based on VGMToolbox.
- HCA decryption is based on the 2ch HCA decoder. Thanks to Headcrabbed for documenting the [newer extra key](https://blog.mottomo.moe/categories/Tech/RE/en/2018-10-12-New-HCA-Encryption/).

MIT licensed. See [LICENSE](LICENSE).
