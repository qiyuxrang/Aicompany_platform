import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
theme = (ROOT / "frontend/src/tech-theme.css").read_text(encoding="utf-8")
light_block = re.search(r":root\s*\{([^}]+)\}", theme).group(1)
dark_block = re.search(r'html\[data-theme="dark"\]\s*\{([^}]+)\}', theme).group(1)
light_tokens = dict(re.findall(r"--([\w-]+):\s*(#[\da-fA-F]{6});", light_block))
dark_tokens = light_tokens | dict(re.findall(r"--([\w-]+):\s*(#[\da-fA-F]{6});", dark_block))


def luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast(foreground, background):
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


checks = []
surfaces = (
    "page", "surface", "raised", "soft", "row-hover", "preview-banner", "preview-bg",
    "feature-end", "feature-hover", "manager-start", "manager-end", "auth-glow",
    "auth-start", "auth-end", "password-glow",
)
pairs = [(foreground, background, 4.5) for foreground in ("ink", "muted", "accent") for background in surfaces]
pairs += [
    ("on-action", "action", 4.5), ("on-action", "accent-dark", 4.5),
    ("good", "good-bg", 4.5), ("warning", "warning-bg", 4.5),
    ("danger", "danger-bg", 4.5), ("teal", "surface", 4.5),
    ("placeholder", "surface", 4.5), ("input-border", "surface", 3.0),
    ("accent", "surface", 3.0), ("accent", "raised", 3.0),
]
for mode, tokens in (("light", light_tokens), ("dark", dark_tokens)):
    for foreground, background, minimum in pairs:
        ratio = contrast(tokens[foreground], tokens[background])
        checks.append({"theme": mode, "foreground": foreground, "background": background, "ratio": round(ratio, 2), "minimum": minimum, "passed": ratio >= minimum})
assert "color-scheme: light" in light_block
assert "color-scheme: dark" in dark_block
assert dark_tokens["page"] == "#101927"
assert "prefers-reduced-motion" in theme
report = {"scope": "Static semantic color pairs only; not a full accessibility certification", "checks": checks}
parser = argparse.ArgumentParser(description="Check light and dark business-theme contrast.")
parser.add_argument("--output", type=Path, default=ROOT / "docs/evidence/bridge-20260921-theme-toggle/contrast.json")
destination = parser.parse_args().output
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
if not all(check["passed"] for check in checks):
    raise SystemExit("Theme contrast check failed")
