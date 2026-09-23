"""Bounded, unambiguous JSON shared by content and handoff entry points."""
import json
import math
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024


def read_bytes(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES: raise ValueError('INPUT_TOO_LARGE')
    return data


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('NONFINITE_JSON_NUMBER')


def finite_float(value):
    result = float(value)
    if not math.isfinite(result): raise ValueError('NONFINITE_JSON_NUMBER')
    return result


def read_json(data):
    if len(data) > MAX_BYTES: raise ValueError('INPUT_TOO_LARGE')
    return json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique_object,
                      parse_constant=invalid_constant, parse_float=finite_float)
