"""Validated NumPy-only inference export. Torch/CUDA are training dependencies."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path

from bots.neural_bot_helper.encoding import CONTRACT, HEADS
from bots.math_bot_helper.analysis import SUPPORTED_RULESET

FORMAT = 'style-candidate-mlp-v1'
DEFAULT_MODEL = Path(__file__).resolve().parents[2] / '.data/models/p4-v1/policy.npz'
DEFAULT_RL_MODEL = Path(__file__).resolve().parents[2] / '.data/models/rl-v1/policy.npz'


class PortablePolicy:
    def __init__(self, arrays, metadata, digest):
        import numpy as np
        if metadata.get('format') != FORMAT or metadata.get('contract') != CONTRACT or metadata.get('heads') != list(HEADS):
            raise ValueError('Model contract/version mismatch')
        if (metadata.get('ruleset') != SUPPORTED_RULESET or type(metadata.get('min_fan')) is not int
                or not 0 <= metadata['min_fan'] <= 6
                or not isinstance(metadata.get('tile_supply'), list) or len(metadata['tile_supply']) != 24
                or any(type(n) is not int or n < 0 for n in metadata['tile_supply'])):
            raise ValueError('Invalid model rule/supply contract')
        ds, da, width = (metadata.get(k) for k in ('state_dim', 'action_dim', 'width'))
        if any(type(n) is not int or not 1 <= n <= 2048 for n in (ds, da, width)):
            raise ValueError('Invalid model dimensions')
        expected = {'obs.weight': (width, ds), 'obs.bias': (width,),
                    'act.weight': (width, da), 'act.bias': (width,),
                    'joint.weight': (width, width * 3), 'joint.bias': (width,),
                    'heads.weight': (4, width), 'heads.bias': (4,),
                    'skip.weight': (4, da), 'skip.bias': (4,)}
        if set(arrays) != set(expected):
            raise ValueError('Unexpected model parameters')
        for key, shape in expected.items():
            a = arrays[key]
            if a.shape != shape or a.dtype != np.float32 or not np.isfinite(a).all():
                raise ValueError('Invalid model parameter: ' + key)
            a.flags.writeable = False
        self.arrays, self.metadata, self.digest = arrays, metadata, digest

    def logits(self, state, actions, weights):
        import numpy as np
        s, a, w = np.asarray(state, np.float32), np.asarray(actions, np.float32), np.asarray(weights, np.float32)
        if s.shape != (self.metadata['state_dim'],) or a.ndim != 2 or a.shape[1] != self.metadata['action_dim'] or w.shape != (4,):
            raise ValueError('Input dimensions mismatch')
        if not np.isfinite(s).all() or not np.isfinite(a).all() or not np.isfinite(w).all():
            raise ValueError('Non-finite input')
        if (w < 0).any() or not np.isclose(w.sum(), 1):
            raise ValueError('Style weights must be a convex mixture')
        p = self.arrays
        linear = lambda x, name: x @ p[name + '.weight'].T + p[name + '.bias']
        obs = np.maximum(linear(s, 'obs'), 0)
        act = np.maximum(linear(a, 'act'), 0)
        repeated = np.broadcast_to(obs, act.shape)
        joint = np.concatenate((repeated, act, repeated * act), axis=-1)
        hidden = np.maximum(linear(joint, 'joint'), 0)
        result = (linear(hidden, 'heads') + linear(a, 'skip')) @ w
        if not np.isfinite(result).all():
            raise ValueError('Non-finite model predictions')
        return result


@lru_cache(maxsize=8)
def _load(path, modified, size):
    import numpy as np
    if size > 16 * 1024 * 1024:
        raise ValueError('Portable model exceeds size limit')
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    with np.load(path, allow_pickle=False) as data:
        metadata = json.loads(str(data['metadata'].item()))
        arrays = {k: data[k] for k in data.files if k != 'metadata'}
    return PortablePolicy(arrays, metadata, digest)


def load_policy(path=DEFAULT_MODEL):
    path = Path(path).resolve()
    info = path.stat()
    return _load(str(path), info.st_mtime_ns, info.st_size)
