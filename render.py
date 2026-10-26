#!/usr/bin/env python3
"""Fill the __TOKENS__ in deploy/, .env-sample and the two MCP servers' main.py from config.env; write the result to rendered/.

Usage: python3 render.py [config.env]
Nothing is changed in place. Re-run after editing config.env.
"""
import re
import shutil
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
conf_path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "config.env"
if not conf_path.exists():
    sys.exit(f"{conf_path} not found. Copy config.env.example to config.env and fill it in.")

values = {}
for line in conf_path.read_text().splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

out = root / "rendered"
if out.exists():
    shutil.rmtree(out)
shutil.copytree(root / "deploy", out / "deploy")
shutil.copy(root / ".env-sample", out / ".env")
# Source files that hold your instance id as a constant: render them too, and package the MCP servers from rendered/.
for rel in ("mcp_read/main.py", "foundation/action_mcp/main.py"):
    (out / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(root / rel, out / rel)

missing = set()
for path in out.rglob("*"):
    if not path.is_file():
        continue
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        continue

    def sub(match):
        name = match.group(1)
        if values.get(name):
            return values[name]
        missing.add(name)
        return match.group(0)

    new = re.sub(r"__([A-Z0-9_]+)__", sub, text)
    if new != text:
        path.write_text(new)

print(f"Rendered into {out}")
if missing:
    print("Still unset (fill in config.env and re-run when these exist):", ", ".join(sorted(missing)))
