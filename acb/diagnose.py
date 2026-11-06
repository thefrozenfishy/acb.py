"""Scan a folder of ACB/AWB files and report anything that would lose or mislabel audio.

    python -m acb.diagnose gamedata/Resources/Sound [--verbose]
"""

import argparse
import collections
import os

from .acb import ACBFile, find_awb


def check(path):
    problems = []
    awb = find_awb(path)
    with ACBFile(path, extern_awb=awb) as f:
        tl = f.track_list
        problems += tl.warnings
        streamed = [t for t in tl.tracks if t.is_stream]
        if streamed and not f.external_awb:
            problems.append(f"MISSING AWB: {len(streamed)} streamed track(s) but no {os.path.basename(path)[:-4]}.awb")
        for t in tl.tracks:
            ar = f.external_awb if t.is_stream else f.embedded_awb
            if ar is None:
                continue
            ids = {e.cue_id for e in ar.files}
            if t.cue_id not in ids:
                problems.append(f"MISSING DATA: {t.name} wants AWB id {t.cue_id}, not in archive")
                continue
            data = f.get_track_data(t, disarm=False)
            # HCA headers may be masked (high bit set), so compare the low 7 bits.
            if t.enc_type == 2 and bytes(b & 0x7F for b in data[:3]) != b"HCA":
                problems.append(f"BAD HEADER: {t.name} (id {t.cue_id}) does not start with HCA")
        for ar, label in ((f.embedded_awb, "embedded"), (f.external_awb, "external")):
            if ar is None:
                continue
            used = {t.cue_id for t in tl.tracks if (t.is_stream == (label == "external"))}
            spare = [e.cue_id for e in ar.files if e.cue_id not in used]
            if spare:
                problems.append(f"UNUSED {label} AWB entries: {spare[:10]}{'...' if len(spare) > 10 else ''}")
        return len(tl.tracks), problems


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root")
    p.add_argument("--verbose", "-v", action="store_true")
    a = p.parse_args()

    totals = collections.Counter()
    for root, _, files in os.walk(a.root):
        for name in sorted(files):
            if not name.endswith(".acb"):
                continue
            path = os.path.join(root, name)
            totals["acb"] += 1
            try:
                n, problems = check(path)
                totals["tracks"] += n
            except Exception as e:
                problems = [f"CRASH: {type(e).__name__}: {e}"]
            for prob in problems:
                totals[prob.split(":")[0].split(" ")[0]] += 1
            if problems and (a.verbose or any(x.startswith(("CRASH", "MISSING", "BAD")) for x in problems)):
                print(path)
                for prob in problems:
                    print("   ", prob)
    print("\nSummary:", dict(totals))


if __name__ == "__main__":
    main()
