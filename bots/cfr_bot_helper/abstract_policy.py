"""Read-only abstract CFR archive validation and cached loading."""

from functools import lru_cache
import hashlib
import math
from pathlib import Path

from bots.cfr_bot_helper.cfr_abstraction import Abstraction, PLANS
from bots.cfr_bot_helper.cfr_policy import read_archive

FORMAT = 'abstract-mccfr-average-v1'
ALGORITHM = 'shared-imperfect-recall-outcome-sampling-v1'


class Policy:
    def __init__(self, value, sha):
        if (value.get('format') != FORMAT or value.get('algorithm') != ALGORITHM
                or value.get('plans') != list(PLANS) or not isinstance(value.get('metadata'), dict)):
            raise ValueError('Abstract policy format/algorithm mismatch')
        self.abstraction = Abstraction(value['abstraction'])
        if value.get('codebook_sha256') != self.abstraction.digest:
            raise ValueError('Abstract codebook digest mismatch')
        records = value.get('nodes')
        if not isinstance(records, dict) or len(records) > self.abstraction.bucket_limit:
            raise ValueError('Invalid abstract policy table size')
        self.nodes = {}
        for key, row in records.items():
            if (not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key
                    or not 0 <= int(key) < self.abstraction.bucket_limit or not isinstance(row, dict)):
                raise ValueError('Invalid abstract model bucket')
            probs, visits = row.get('probabilities'), row.get('visits')
            if (not isinstance(probs, list) or len(probs) != len(PLANS)
                    or any(type(p) not in (int, float) or not math.isfinite(p) or p < 0 for p in probs)
                    or not math.isclose(math.fsum(probs), 1., abs_tol=1e-8)
                    or type(visits) is not int or visits < 1):
                raise ValueError('Invalid abstract model probability/visits')
            self.nodes[int(key)] = (tuple(probs), visits)
        self.metadata, self.digest = value['metadata'], sha


@lru_cache(maxsize=4)
def _load(path, modified, size):
    if size > 128 * 1024 * 1024:
        raise ValueError('Abstract policy exceeds compressed size limit')
    return Policy(read_archive(path), hashlib.sha256(Path(path).read_bytes()).hexdigest())


def load_policy(path):
    p = Path(path).resolve()
    info = p.stat()
    return _load(str(p), info.st_mtime_ns, info.st_size)
