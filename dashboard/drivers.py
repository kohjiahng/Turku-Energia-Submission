"""Driver-name glossary: feature name -> human label. Never raises on unknown names."""
import json
import re


class Glossary:
    def __init__(self, path=None):
        self.exact, self.prefix = {}, []
        try:
            with open(path, encoding="utf-8") as fh:
                entries = json.load(fh).get("entries", {})
        except Exception:
            entries = {}
        for k, v in entries.items():
            if k.endswith("*"):
                self.prefix.append((k[:-1], v))
            else:
                self.exact[k] = v
        self.prefix.sort(key=lambda kv: -len(kv[0]))

    @staticmethod
    def prettify(name):
        s = re.sub(r"[_\-]+", " ", str(name)).strip()
        return (s[:1].upper() + s[1:]) if s else "Unknown driver"

    def describe(self, name):
        name = str(name).strip()
        m = re.search(r"(\d+)$", name)
        n = m.group(1) if m else ""
        hit = self.exact.get(name)
        if hit is None:
            for p, v in self.prefix:
                if name.startswith(p):
                    hit = v
                    break
        if hit is None:
            return {"name": name, "label": self.prettify(name).lower(), "explain": "Model feature (no glossary entry yet).", "known": False}
        try:
            label, expl = hit[0].replace("{N}", n), hit[1].replace("{N}", n)
        except Exception:
            label, expl = self.prettify(name).lower(), ""
        return {"name": name, "label": label, "explain": expl, "known": True}

    def describe_many(self, names, limit=5):
        out, seen = [], set()
        for n in names:
            n = str(n).strip()
            if n and n.lower() != "nan" and n not in seen:
                seen.add(n)
                out.append(self.describe(n))
        return out[:limit]
