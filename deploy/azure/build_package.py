"""Build the deployment package for the free Azure demo: dist/azure/hotelchain-demo.zip

The Free (F1) App Service tier gives 1 GB of storage and 1 GB of memory, so the package carries
only what the web app needs at run time:
  - the application code, web client, configuration and hero image
  - a slim copy of the lakehouse with the Gold, AI and operations tables the pages read
    (no Bronze, and only the three Silver tables the API queries)
  - the run-time Python libraries, pre-built for Linux (no machine-learning or test libraries)

The pipeline and the models are run on your machine; Azure only serves the results.

    python deploy/azure/build_package.py
"""
from __future__ import annotations

import importlib.metadata as md
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import duckdb
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist" / "azure"
APP = DIST / "app"
SILVER_NEEDED = ["menu_item", "product", "room_night"]
RUNTIME = ["fastapi", "uvicorn", "duckdb", "pandas", "numpy"]
SITE_PACKAGES = "python_packages"


def main() -> None:
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir(parents=True)
    skip = shutil.ignore_patterns("__pycache__", "*.pyc")
    for folder in ("hip", "web", "config"):
        shutil.copytree(ROOT / folder, APP / folder, ignore=skip)
    # The Free tier limits data sent per day (about 165 MB), so the 2.7 MB hero photograph is shipped as a compact JPEG.
    (APP / "img").mkdir()
    hero = Image.open(ROOT / "img" / "hero.png").convert("RGB")
    hero.thumbnail((2200, 2200))
    hero.save(APP / "img" / "hero.jpg", "JPEG", quality=80, optimize=True, progressive=True)
    (APP / "requirements.txt").write_text("\n".join(f"{p}=={md.version(p)}" for p in RUNTIME) + "\n", encoding="utf-8")

    # ---- Python libraries, pre-built for the Azure host (Linux, Python 3.12).
    # The Free tier cannot run the build on its side: it stops the app after a few minutes of CPU, so
    # the Linux wheels are downloaded here and shipped inside the package. PYTHONPATH points at this folder.
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", str(APP / SITE_PACKAGES),
                    "--python-version", "3.12", "--implementation", "cp", "--only-binary=:all:",
                    "--platform", "manylinux_2_28_x86_64", "--platform", "manylinux_2_17_x86_64", "--platform", "manylinux2014_x86_64",
                    "-r", str(APP / "requirements.txt")], check=True)
    for junk in list((APP / SITE_PACKAGES).rglob("__pycache__")) + list((APP / SITE_PACKAGES).rglob("tests")) + [APP / SITE_PACKAGES / "bin"]:
        shutil.rmtree(junk, ignore_errors=True)

    # ---- slim lakehouse
    (APP / "data").mkdir()
    out = APP / "data" / "lakehouse.duckdb"
    con = duckdb.connect(str(out))
    con.execute(f"ATTACH '{(ROOT / 'data' / 'lakehouse.duckdb').as_posix()}' AS src (READ_ONLY)")
    tables = con.execute("SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog = 'src' AND table_schema IN ('gold', 'ai', 'ops', 'silver') ORDER BY 1, 2").fetchall()
    rows = 0
    for schema, name in tables:
        if schema == "silver" and name not in SILVER_NEEDED:
            continue
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        con.execute(f'CREATE TABLE {schema}."{name}" AS SELECT * FROM src.{schema}."{name}"')
        rows += con.execute(f'SELECT count(*) FROM {schema}."{name}"').fetchone()[0]
    con.execute("DETACH src")
    con.execute("CHECKPOINT")
    con.close()

    # ---- zip
    zpath = DIST / "hotelchain-demo.zip"
    zpath.unlink(missing_ok=True)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(APP.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(APP).as_posix())
    size = sum(f.stat().st_size for f in APP.rglob("*") if f.is_file())
    print(f"slim lakehouse: {out.stat().st_size / 1e6:.0f} MB, {rows:,} rows")
    print(f"package folder: {size / 1e6:.0f} MB unpacked  ->  {zpath}  ({zpath.stat().st_size / 1e6:.0f} MB)")
    print("run-time packages:", (APP / "requirements.txt").read_text().split())


if __name__ == "__main__":
    main()
