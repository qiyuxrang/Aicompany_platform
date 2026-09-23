"""Extract selected P6 blocks for P7/P8; no model, network, review or approval."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from contract import obj, validate

from strict_json import MAX_BYTES, read_bytes, unique_object, read_json
SKILL = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def select_blocks(content, selection, source_hash):
    obj(selection, ('version', 'content_sha256', 'block_ids'))
    if type(selection['version']) is not int or selection['version'] != 1:
        raise ValueError('INVALID_SELECTION_VERSION')
    if selection['content_sha256'] != source_hash:
        raise ValueError('CONTENT_HASH_MISMATCH')
    ids = selection['block_ids']
    available = {b['id'] for b in content['blocks'] if b['type'] not in ('heading', 'section')}
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
            or len(ids) != len(set(ids)) or set(ids) - available):
        raise ValueError('INVALID_BLOCK_SELECTION')
    paths, headings, selected = {}, [], set(ids)
    for block in content['blocks']:
        if block['type'] == 'heading':
            headings = [h for h in headings if h['level'] < block['level']] + [block]
        elif block['id'] in selected:
            paths[block['id']] = [h['id'] for h in headings]
    included = selected | {ident for path in paths.values() for ident in path}
    result = deepcopy(content)
    result['blocks'] = [b for b in result['blocks'] if b['id'] in included]
    for group, field in (('sources', 'source_ids'), ('requirements', 'requirement_ids')):
        referenced = {ident for b in result['blocks'] for ident in b[field]}
        result[group] = [item for item in result[group] if item['id'] in referenced]
    # A reviewed parent never grants review to a new selection or downstream plan.
    if result['metadata']['status'] == 'reviewed':
        result['metadata']['status'] = 'draft'
    return result, paths


def selected_assets(content, source_dir):
    assets = {}
    for block in content['blocks']:
        if block['type'] != 'figure':
            continue
        relative = Path(block['path'])
        if any(part in ('', '.', '..') or ':' in part for part in block['path'].split('/')):
            raise ValueError('UNSAFE_ASSET_PATH')
        current = source_dir
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('UNSAFE_ASSET_PATH')
        if not current.resolve().is_relative_to(source_dir) or not current.is_file():
            raise ValueError('ASSET_NOT_FOUND')
        data = read_bytes(current)
        name = 'assets/' + sha(data) + relative.suffix.lower()
        assets[name] = data
        block['path'] = name
    return assets


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def build_handoff(source, selection_path, output):
    source, selection_path, output = Path(source).resolve(), Path(selection_path).resolve(), Path(output)
    if output.is_symlink() or output.exists():
        raise ValueError('OUTPUT_EXISTS')
    output = output.resolve()
    if output.is_relative_to(SKILL):
        raise ValueError('OUTPUT_INSIDE_SKILL')
    if source.is_relative_to(output) or selection_path.is_relative_to(output):
        raise ValueError('OUTPUT_OVERLAPS_INPUT')
    raw, choice = read_bytes(source), read_bytes(selection_path)
    content = validate(read_json(raw))
    extracted, paths = select_blocks(content, read_json(choice), sha(raw))
    files = selected_assets(extracted, source.parent)
    validate(extracted)
    files['content.json'] = encoded(extracted)
    manifest = {
        'schema': 'BJ_CONTENT_HANDOFF_V1', 'hash_method': 'sha256-file-bytes',
        'source_content_sha256': sha(raw), 'selection_sha256': sha(choice),
        'source_status': content['metadata']['status'],
        'selected_block_ids': list(paths), 'section_paths': paths,
        'approval_inherited': False, 'review_verified': False,
        'pending_policy': 'preserve-all',
        'files': {name: sha(data) for name, data in files.items()},
    }
    files['manifest.json'] = encoded(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.handoff-', dir=output.parent))
    try:
        for name, data in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if output.exists() or output.is_symlink():
            raise ValueError('OUTPUT_EXISTS')
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('content', help='Current P6 content.json; never a DOCX')
    parser.add_argument('selection', help='JSON with version=1, content_sha256, block_ids')
    parser.add_argument('output', help='New directory, must not already exist')
    args = parser.parse_args()
    try:
        result = build_handoff(args.content, args.selection, args.output)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        print('HANDOFF_FAILED: ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps({'status': 'EXTRACTED_NOT_APPROVED', 'output': str(Path(args.output).resolve()),
                      'selected_block_ids': result['selected_block_ids']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
