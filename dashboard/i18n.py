"""Server-side Finnish translation of alert titles / messages / review lines (regex templates over the English text).
translate_alert(alert, lang) returns a copy; unknown text is left in English (never raises). Driver labels stay English."""
import re

T = r"(\{t:[^}]+\})"
TITLES = {
    "Price spike risk high": "Hintapiikin riski ylös", "Price spike risk low": "Hintapiikin riski alas",
    "Up-regulation expected": "Nousevan säädön tarve", "Down-regulation expected": "Laskevan säädön tarve",
    "Forecast revised upward": "Ennustetta nostettu", "Forecast revised downward": "Ennustetta laskettu",
    "Forecast interval widened": "Ennustealue levennyt", "High uncertainty": "Suuri epävarmuus",
    "Conflicting signals": "Ristiriitaiset signaalit", "Worth an expert review": "Asiantuntijan tarkistus suositeltu",
    "Rule error": "Sääntövirhe"}
REVIEWS = {
    "Check position; consider offering flexible capacity (up-regulation).": "Tarkista positio; harkitse joustavan kapasiteetin tarjoamista (ylössäätö).",
    "Check position; consider cutting production (down-regulation).": "Tarkista positio; harkitse tuotannon vähentämistä (alassäätö).",
    "Check whether flexible production or load can be offered.": "Tarkista, voiko joustavaa tuotantoa tai kulutusta tarjota.",
    "Check whether production can be cut or load raised.": "Tarkista, voiko tuotantoa vähentää tai kulutusta lisätä.",
    "Check what changed (wind, load, flows) and your positions.": "Tarkista mikä muuttui (tuuli, kulutus, siirrot) ja positiosi.",
    "Less certain than an hour ago; treat point forecasts with care.": "Epävarmempi kuin tunti sitten; suhtaudu pisteennusteisiin varoen.",
    "Avoid over-relying on the point forecast.": "Älä nojaa liikaa pisteennusteeseen.",
    "Worth an expert look.": "Asiantuntijan syytä vilkaista.",
    "Review plan, position and flexible capacity together.": "Käy läpi suunnitelma, positio ja joustava kapasiteetti yhdessä."}
CONFLICT = {
    "up-regulation likely but P50 at/below spot": "ylössäätö todennäköinen mutta P50 spotin tasolla tai alapuolella",
    "down-regulation likely but P50 at/above spot": "alassäätö todennäköinen mutta P50 spotin tasolla tai yläpuolella",
    "high spike likely but direction points down": "korkea piikki todennäköinen mutta suunta osoittaa alas",
    "low spike likely but direction points up": "matala piikki todennäköinen mutta suunta osoittaa ylös"}
FAM = {"price-spike risk": "hintapiikin riski", "regulation need": "säätötarve", "forecast revision": "ennusteen muutos",
       "high uncertainty": "suuri epävarmuus", "conflicting signals": "ristiriitaiset signaalit"}


PATTERNS = [
    (re.compile(r"(High|Low)-price spike risk peaks at (\d+)% \(" + T + "-" + T + r"\)(, up (\d+) pp since last update)?\."),
     lambda m: "%s piikin riski huipussaan %s %% (%s-%s)%s." % ("Korkean hinnan" if m.group(1) == "High" else "Matalan hinnan", m.group(2), m.group(3), m.group(4),
                                                              (", nousua %s %%-yks. edellisestä päivityksestä" % m.group(6)) if m.group(5) else "")),
    (re.compile(r"(Up|Down)-regulation likely " + T + "-" + T + r" \(avg probability (\d+)%\)\."),
     lambda m: "%s todennäköinen %s-%s (keskimääräinen todennäköisyys %s %%)." % ("Nouseva säätö" if m.group(1) == "Up" else "Laskeva säätö", m.group(2), m.group(3), m.group(4))),
    (re.compile(r"P50 for " + T + "-" + T + r" moved (up|down) by up to (\d+) EUR/MWh since last update\."),
     lambda m: "P50 %s-%s %s enintään %s EUR/MWh edellisestä päivityksestä." % (m.group(1), m.group(2), "nousi" if m.group(3) == "up" else "laski", m.group(4))),
    (re.compile(r"P10-P90 band widened from (\d+) to (\d+) EUR/MWh since last update\."),
     lambda m: "P10-P90-alue levisi %s:stä %s:een EUR/MWh edellisestä päivityksestä." % (m.group(1), m.group(2))),
    (re.compile(r"High uncertainty " + T + "-" + T + r": band averages (\d+) EUR/MWh\."),
     lambda m: "Suuri epävarmuus %s-%s: alue keskimäärin %s EUR/MWh." % (m.group(1), m.group(2), m.group(3))),
    (re.compile(r"High uncertainty: band (\d+) vs usual (\d+) EUR/MWh\."),
     lambda m: "Suuri epävarmuus: alue %s, tavallisesti %s EUR/MWh." % (m.group(1), m.group(2))),
    (re.compile(r"High uncertainty\."), lambda m: "Suuri epävarmuus."),
    (re.compile(T + "-" + T + ": (" + "|".join(re.escape(k) for k in CONFLICT) + r")\."),
     lambda m: "%s-%s: %s." % (m.group(1), m.group(2), CONFLICT[m.group(3)])),
    (re.compile(r"Several signals overlap " + T + "-" + T + r": ([^.]+)\."),
     lambda m: "Useita signaaleja samaan aikaan %s-%s: %s." % (m.group(1), m.group(2), ", ".join(FAM.get(x.strip(), x.strip()) for x in m.group(3).split(",")))),
    (re.compile(r"Drivers: "), lambda m: "Taustatekijät: "),
]
DRV = "Taustatekijät: "


def translate_text(s):
    for rx, fn in PATTERNS:
        s = rx.sub(fn, s)
    return s


def translate_alert(a, lang):
    if lang != "fi":
        return a
    b = dict(a)
    b["title"] = TITLES.get(a.get("title"), a.get("title"))
    b["message"] = translate_text(a.get("message", ""))
    b["review"] = REVIEWS.get(a.get("review"), a.get("review"))
    return b
