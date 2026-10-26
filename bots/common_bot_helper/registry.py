"""Lazy policy factories for experiments; math is separate from weak baselines."""

from bots.baseline_bot import RandomBot, TsumogiriBot, WinThenRandomBot

BASELINES = {"tsumogiri": TsumogiriBot, "random": RandomBot, "win-random": WinThenRandomBot}


def make_baseline(name, seed=0):
    cls = BASELINES[name]
    return cls() if cls is TsumogiriBot else cls(seed)


BOT_NAMES = (*BASELINES, 'math', 'search', 'fast', 'big', 'defensive', 'blocking',
             'nn-fast', 'nn-big', 'nn-defensive', 'nn-blocking')
BOT_NAMES += ('rl-fast', 'rl-big', 'rl-defensive', 'rl-blocking')


def make_bot(name, seed=0, *, model_path=None):
    if name.startswith(('nn-', 'rl-')) and name in BOT_NAMES:
        from bots.neural_bot import NeuralBot
        if name.startswith('rl-') and model_path is None:
            from bots.neural_bot_helper.portable_policy import DEFAULT_RL_MODEL
            model_path = DEFAULT_RL_MODEL
        return NeuralBot(name[3:], seed=seed, **({'model_path': str(model_path)} if model_path else {}))
    if model_path is not None:
        raise ValueError('Model paths apply only to nn-* or rl-* policies')
    if name in ('fast', 'big', 'defensive', 'blocking'):
        from bots.style_bot import StyleBot
        return StyleBot('defensive' if name == 'blocking' else name, seed=seed,
                        containment=1.0 if name == 'blocking' else None)
    if name == 'search':
        from bots.search_bot import SearchBot
        return SearchBot(seed=seed)
    if name == 'math':
        from bots.math_bot import MathBot
        return MathBot(seed=seed)
    return make_baseline(name, seed)
