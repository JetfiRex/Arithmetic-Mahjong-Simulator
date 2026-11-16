"""Shared coarse-policy action semantics and safe gzip JSON I/O."""

import gzip
import hashlib
import io
import json
from pathlib import Path

from bots.common_bot_helper.decision import ACTION_VERSION
from bots.common_bot_helper.observation import OBSERVATION_VERSION

STYLES = ('fast', 'big', 'defensive', 'blocking')
CONTRACT = dict(observation=OBSERVATION_VERSION, actions=ACTION_VERSION, hu_policy='always-accept-v1')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def semantic(action):
    # required_values already has a canonical engine order; sort without mixing
    # numeric and string tile comparisons when accepting an external candidate.
    required = sorted(action.required_values, key=lambda v: (isinstance(v, str), str(v)))
    return _json((action.action_type.value, action.tile, required, action.meld_index))


def rule_contract(observation):
    return dict(ruleset=observation.ruleset_id, min_fan=observation.min_fan,
                scoring=observation.scoring_id, tile_supply=list(observation.tile_supply),
                allow_multiple_hu=observation.allow_multiple_hu)


def write_archive(path, value):
    """Atomic, deterministic gzip JSON; no pickle in policy or checkpoints."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.partial.gz')
    with temporary.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, filename='', mode='wb', mtime=0, compresslevel=3) as compressed:
            with io.TextIOWrapper(compressed, encoding='utf-8', newline='\n') as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    temporary.replace(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_archive(path, *, max_bytes=256 * 1024 * 1024):
    with gzip.open(path, 'rb') as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('CFR archive exceeds uncompressed size limit')
    return json.loads(data)
