"""Create review contact sheets and structural image inventory for a large-output run."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat


def page_record(path):
    with Image.open(path) as source:
        image = source.convert("RGB")
        white = Image.new("RGB", image.size, "white")
        difference = ImageChops.difference(image, white).convert("L")
        bounds = difference.point(lambda value: 255 if value > 8 else 0).getbbox()
        statistics = ImageStat.Stat(image.convert("L"))
        return {
            "file": path.name,
            "width": image.width,
            "height": image.height,
            "ink_bounds": list(bounds) if bounds else None,
            "grayscale_stddev": round(statistics.stddev[0], 3),
            "blank": bounds is None or statistics.stddev[0] < 0.5,
        }


def contact_sheets(source_dir, output_dir, columns, rows, thumb_width):
    paths = sorted(source_dir.glob("*.png"))
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    per_sheet = columns * rows
    for sheet_index, offset in enumerate(range(0, len(paths), per_sheet), start=1):
        selected = paths[offset:offset + per_sheet]
        thumbs = []
        for path in selected:
            with Image.open(path) as source:
                image = source.convert("RGB")
                height = round(image.height * thumb_width / image.width)
                image.thumbnail((thumb_width, height), Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", (thumb_width + 24, height + 56), "white")
                canvas.paste(image, (12, 36))
                ImageDraw.Draw(canvas).text((12, 10), path.stem, fill="black")
                thumbs.append(canvas)
        cell_width = max(item.width for item in thumbs)
        cell_height = max(item.height for item in thumbs)
        sheet = Image.new("RGB", (cell_width * columns, cell_height * rows), "#D9D9D9")
        for index, image in enumerate(thumbs):
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            sheet.paste(image, (x, y))
        target = output_dir / f"contact-{sheet_index:03d}.jpg"
        sheet.save(target, quality=90, optimize=True)
        records.append({"file": target.name, "pages": [path.name for path in selected]})
    return paths, records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit("run root does not exist")
    specifications = {
        "word-render-technical-solution": (2, 3, 850),
        "word-render-feasibility": (2, 3, 850),
        "ppt-render": (2, 2, 950),
    }
    inventory = {"root": str(root), "sets": {}, "issues": []}
    for name, (columns, rows, width) in specifications.items():
        source = root / name
        pages, contacts = contact_sheets(source, root / "visual-review" / name, columns, rows, width)
        page_records = [page_record(path) for path in pages]
        inventory["sets"][name] = {"pages": page_records, "contacts": contacts}
        for record in page_records:
            if record["blank"]:
                inventory["issues"].append({"set": name, "file": record["file"], "code": "blank_page"})
            bounds = record["ink_bounds"]
            if name.startswith("word") and bounds:
                if bounds[0] <= 2 or bounds[1] <= 2 or bounds[2] >= record["width"] - 2 or bounds[3] >= record["height"] - 2:
                    inventory["issues"].append({"set": name, "file": record["file"], "code": "ink_touches_page_edge"})
    target = root / "visual-review" / "inventory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "inventory": str(target),
        "page_counts": {name: len(value["pages"]) for name, value in inventory["sets"].items()},
        "contact_counts": {name: len(value["contacts"]) for name, value in inventory["sets"].items()},
        "issues": inventory["issues"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
