"""Visible information-set contract and read-only tabular average-policy files.

No Torch, engine snapshots, future walls, action IDs or clocks enter a key.
The extra own-decision chain remembers earlier observations and legal menus,
including private PASS decisions, rather than assuming an event log suffices.
"""

from dataclasses import asdict, dataclass
from functools import lru_cache
import gzip
import hashlib
import io
import json
import math
from pathlib import Path

from bots.common_bot_helper.decision import ACTION_VERSION
from bots.common_bot_helper.observation import OBSERVATION_VERSION
from simulator.actions import ActionType

INFO_VERSION = 'cfr-visible-perfect-recall-v1'
FORMAT = 'cfr-tabular-average-v1'
STYLES = ('fast', 'big', 'defensive', 'blocking')
CONTRACT = dict(observation=OBSERVATION_VERSION, actions=ACTION_VERSION,
                information=INFO_VERSION, hu_policy='always-accept-v1')
ROOT_RECALL = '0' * 64


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def semantic(action):
    # required_values already has a canonical engine order; sort without mixing
    # numeric and string tile comparisons when accepting an external candidate.
    required = sorted(action.required_values, key=lambda v: (isinstance(v, str), str(v)))
    return _json((action.action_type.value, action.tile, required, action.meld_index))


def candidates(legal_actions):
    if not legal_actions:
        raise ValueError('CFR requires legal actions')
    hu = tuple(a for a in legal_actions if a.action_type == ActionType.HU)
    selected = hu or tuple(legal_actions)
    pairs = sorted(((semantic(a), a) for a in selected), key=lambda pair: pair[0])
    if len({token for token, _ in pairs}) != len(pairs):
        raise ValueError('Duplicate semantic legal actions')
    return tuple(token for token, _ in pairs), tuple(action for _, action in pairs)


def rule_contract(observation):
    return dict(ruleset=observation.ruleset_id, min_fan=observation.min_fan,
                scoring=observation.scoring_id, tile_supply=list(observation.tile_supply),
                allow_multiple_hu=observation.allow_multiple_hu)


@dataclass(frozen=True)
class EncodedInformation:
    key: str
    base: str
    recall: str
    tokens: tuple[str, ...]
    actions: tuple


class Recall:
    def __init__(self):
        self.reset()

    def reset(self):
        self.chain, self.initial, self.history_size = ROOT_RECALL, None, 0
        self.last, self.last_action = None, None

    def project(self, observation, legal_actions, *, style, seat):
        if style not in STYLES or seat not in range(4):
            raise ValueError('Invalid CFR style/seat')
        initial = _digest([asdict(e) for e in observation.history if e.kind == 'initial_hand' and e.private])
        if initial != self.initial or len(observation.history) < self.history_size:
            self.reset()
        self.initial, self.history_size = initial, len(observation.history)
        tokens, actions = candidates(legal_actions)
        visible = asdict(observation)
        visible.pop('decision_context')
        base = _digest(dict(visible=visible, actions=tokens, style=style, seat=seat))
        if self.last is not None and self.last.base == base:
            # Retrying act for the same visible decision must not add fictitious
            # own decisions; return bindings belonging to the current request.
            return EncodedInformation(self.last.key, base, self.last.recall, tokens, actions)
        key = _digest(dict(version=INFO_VERSION, base=base, recall=self.chain))
        return EncodedInformation(key, base, self.chain, tokens, actions)

    def commit(self, encoded, token):
        if token not in encoded.tokens:
            raise ValueError('Action outside encoded information set')
        if self.last is not None and self.last.base == encoded.base:
            if token != self.last_action:
                raise ValueError('Conflicting choices for the same visible decision')
            return
        if self.chain != encoded.recall:
            raise ValueError('Stale recalled decision')
        self.chain = _digest((self.chain, encoded.key, token))
        self.last, self.last_action = encoded, token


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


@dataclass(frozen=True)
class AverageNode:
    owner: int
    actions: tuple[str, ...]
    probabilities: tuple[float, ...]
    visits: int


class AveragePolicy:
    def __init__(self, value, digest):
        if value.get('format') != FORMAT or value.get('metadata', {}).get('contract') != CONTRACT:
            raise ValueError('CFR policy format/contract mismatch')
        metadata = value['metadata']
        rules = metadata.get('rules', {})
        if (metadata.get('game') != 'mahjong' or metadata.get('num_players') != 4
                or metadata.get('styles') != list(STYLES) or metadata.get('information') != INFO_VERSION
                or not isinstance(rules.get('tile_supply'), list) or len(rules['tile_supply']) != 24
                or any(type(n) is not int or n < 0 for n in rules['tile_supply'])
                or type(rules.get('min_fan')) is not int or not 0 <= rules['min_fan'] <= 6
                or type(rules.get('allow_multiple_hu')) is not bool
                or not isinstance(rules.get('ruleset'), str) or not isinstance(rules.get('scoring'), str)):
            raise ValueError('Invalid CFR rule/information contract')
        records = value.get('nodes')
        if not isinstance(records, dict) or len(records) > 200000:
            raise ValueError('Invalid CFR information-set table size')
        self._nodes = {}
        for key, row in records.items():
            if not isinstance(row, dict):
                raise ValueError('Invalid CFR average-policy row')
            owner, actions, probs, visits = (row.get(k) for k in ('owner', 'actions', 'probabilities', 'visits'))
            if (not isinstance(key, str) or len(key) != 64 or any(c not in '0123456789abcdef' for c in key)
                    or type(owner) is not int or owner not in range(4)
                    or not isinstance(actions, list) or not 2 <= len(actions) <= 4096
                    or any(not isinstance(a, str) or not a for a in actions) or len(set(actions)) != len(actions)
                    or not isinstance(probs, list) or len(probs) != len(actions)
                    or any(type(p) not in (int, float) or not math.isfinite(p) or p < 0 for p in probs)
                    or not math.isclose(math.fsum(probs), 1., abs_tol=1e-7)
                    or type(visits) is not int or visits < 1):
                raise ValueError('Invalid CFR average-policy row')
            self._nodes[key] = AverageNode(owner, tuple(actions), tuple(probs), visits)
        self.metadata, self.digest = metadata, digest

    def lookup(self, encoded, seat):
        node = self._nodes.get(encoded.key)
        if node is not None and (node.owner != seat or node.actions != encoded.tokens):
            raise ValueError('CFR information-set owner/actions changed')
        return node


@lru_cache(maxsize=4)
def _load(path, modified, size):
    if size > 128 * 1024 * 1024:
        raise ValueError('CFR compressed policy exceeds size limit')
    return AveragePolicy(read_archive(path), hashlib.sha256(Path(path).read_bytes()).hexdigest())


def load_policy(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _load(str(path), stat.st_mtime_ns, stat.st_size)
