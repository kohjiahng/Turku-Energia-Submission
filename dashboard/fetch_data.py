"""Download the dashboard's data bundle (a zip) into RADAR_DATA_DIR when the files are not already there.

Used on hosts without SSH access: put the zip at a private HTTPS link and set RADAR_DATA_URL. Only the files listed in
WANTED are extracted (by base name, so a malicious archive cannot write elsewhere). The link is never printed, because it may
carry a token. Files that already exist are kept unless RADAR_DATA_REFRESH=1.
"""
import io, os, sys, urllib.request, zipfile
from urllib.parse import urlparse

WANTED = {
    "inputs.parquet", "spot_nordpool.csv", "actuals_validation.csv",
    "forecast_validation.csv", "forecast_test.csv",
    "driver_shares_validation.parquet", "driver_shares_test.parquet",
    "baseline_validation.csv",
}
REQUIRED = WANTED - {"baseline_validation.csv"}
MAX_BYTES = 200 * 1024 * 1024


def missing(data_dir):
    return sorted(f for f in REQUIRED if not os.path.isfile(os.path.join(data_dir, f)))


def fetch(url, data_dir, refresh=False, log=print):
    u = urlparse(url)
    if u.scheme != "https" and u.hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("RADAR_DATA_URL must be an https link")
    os.makedirs(data_dir, exist_ok=True)
    if not refresh and not missing(data_dir):
        log("data bundle: all required files already in %s, not downloading" % data_dir)
        return
    log("data bundle: downloading (%s missing) ..." % (", ".join(missing(data_dir)) or "refresh requested"))
    req = urllib.request.Request(url, headers={"User-Agent": "radar-dashboard"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            blob = r.read(MAX_BYTES + 1)
    except Exception as e:   # do not echo the URL
        raise SystemExit("data bundle download failed: %s" % type(e).__name__)
    if len(blob) > MAX_BYTES:
        raise SystemExit("data bundle is larger than %d MB" % (MAX_BYTES // 2**20))
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise SystemExit("data bundle is not a zip file (check that the link is a direct download)")
    got = []
    for info in zf.infolist():
        name = os.path.basename(info.filename)
        if info.is_dir() or name not in WANTED:
            continue
        dest = os.path.join(data_dir, name)
        with zf.open(info) as src, open(dest + ".tmp", "wb") as out:
            out.write(src.read())
        os.replace(dest + ".tmp", dest)
        got.append(name)
    log("data bundle: extracted %d files (%s)" % (len(got), ", ".join(sorted(got))))
    left = missing(data_dir)
    if left:
        raise SystemExit("data bundle is missing required files: %s" % ", ".join(left))
