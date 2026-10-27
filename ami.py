"""Read questions with word-level timings from the AMI Meeting Corpus manual annotations (CC BY 4.0).

Words come from words/<meeting>.<speaker>.words.xml (forced-aligned start and end times). Questions are
dialogue acts whose type is Elicit-Inform (el.inf), from dialogueActs/<meeting>.<speaker>.dialog-act.xml.
"""
import collections, io, re, urllib.request, zipfile, pathlib
import xml.etree.ElementTree as ET

URL = "http://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip"
NITE = "{http://nite.sourceforge.net/}"
ROOT = pathlib.Path(__file__).parent

def zip_path():
    p = ROOT / "data" / "ami_public_manual_1.6.2.zip"
    if not p.exists():
        p.parent.mkdir(exist_ok=True)
        print("downloading AMI manual annotations (one time)...", flush=True)
        p.write_bytes(urllib.request.urlopen(URL, timeout=300).read())
    return p

def question_types(xml_bytes):
    """nite:ids of dialogue-act types meaning Elicit-Inform, matched by gloss or name."""
    ids = set()
    for el in ET.fromstring(xml_bytes).iter():
        nid = el.get(NITE + "id")
        label = " ".join(filter(None, [el.get("gloss"), el.get("name")])).lower()
        if nid and ("elicit-inform" in label or re.search(r"\bel\.inf\b", label)):
            ids.add(nid)
    return ids

def read_tokens(xml_bytes):
    """Every <w> token in document order, punctuation included, as (id, start, end, text, keep).
    keep is False for punctuation and untimed tokens, which never reach the timeline."""
    out = []
    for el in ET.fromstring(xml_bytes):
        if el.tag != "w":
            continue
        text = (el.text or "").strip()
        try:
            start, end = float(el.get("starttime")), float(el.get("endtime"))
            timed = True
        except (TypeError, ValueError):
            start = end = None; timed = False
        keep = timed and bool(text) and el.get("punc") != "true"
        out.append((el.get(NITE + "id"), start, end, text, keep))
    return out

def read_words(xml_bytes):
    """Ordered list of (id, start, end, text) for timed, non-punctuation words."""
    return [(i, s, e, t) for i, s, e, t, k in read_tokens(xml_bytes) if k]

HREF = re.compile(r"#id\(([^)]+)\)(?:\.\.id\(([^)]+)\))?")

def read_questions(dact_bytes, tokens, qtypes):
    """Word spans of every Elicit-Inform dialogue act. Ranges often end on a punctuation token, so the
    range is resolved over all tokens and then filtered to timed words."""
    pos = {t[0]: i for i, t in enumerate(tokens)}
    out = []
    for dact in ET.fromstring(dact_bytes):
        ptr = dact.find(NITE + "pointer"); child = dact.find(NITE + "child")
        if ptr is None or child is None:
            continue
        m = re.search(r"#id\(([^)]+)\)", ptr.get("href", ""))
        if not m or m.group(1) not in qtypes:
            continue
        r = HREF.search(child.get("href", ""))
        if not r:
            continue
        lo, hi = pos.get(r.group(1)), pos.get(r.group(2) or r.group(1))
        if lo is None or hi is None:
            continue
        span = [(i, s, e, t) for i, s, e, t, k in tokens[lo:hi + 1] if k]
        if len(span) >= 4:
            out.append(span)
    return out

def load(max_meetings=None, context_s=90.0):
    """Questions as dicts: meeting, speaker, words [(start, end, text)], end, context (prior transcript)."""
    z = zipfile.ZipFile(zip_path())
    names = z.namelist()
    qtypes = question_types(z.read(next(n for n in names if n.endswith("da-types.xml"))))
    by_meeting = collections.defaultdict(dict)
    for n in names:
        m = re.search(r"words/([A-Z]{2}\d{4}[a-d]?)\.([A-Z])\.words\.xml$", n)
        if m:
            by_meeting[m.group(1)][m.group(2)] = n
    questions = []
    for meeting in sorted(by_meeting)[:max_meetings]:
        timeline, spk_words = [], {}
        for spk, wn in by_meeting[meeting].items():
            toks = read_tokens(z.read(wn)); spk_words[spk] = toks
            timeline += [(s, e, spk, t) for _, s, e, t, k in toks if k]
        timeline.sort()
        for spk, ws in spk_words.items():
            dn = f"dialogueActs/{meeting}.{spk}.dialog-act.xml"
            dn = next((x for x in names if x.endswith(dn)), None)
            if not dn:
                continue
            for span in read_questions(z.read(dn), spk_words[spk], qtypes):
                start, end = span[0][1], span[-1][2]
                ctx = [w for w in timeline if start - context_s <= w[0] < start]
                lines, last = [], None
                for s, e, who, t in ctx:
                    if who != last:
                        lines.append(f"Speaker {who}:"); last = who
                    lines[-1] += " " + t
                questions.append({"meeting": meeting, "speaker": spk, "words": [(s, e, t) for _, s, e, t in span],
                                  "end": end, "context": "\n".join(lines)})
    return questions
