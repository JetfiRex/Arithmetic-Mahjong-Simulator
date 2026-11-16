"""Practice-only policy catalog; leave the trainer's registry and source hash alone."""

STYLE_NAMES = {"fast": "速胡", "big": "做大", "defensive": "稳守", "blocking": "牵制"}
GROUPS = {
    "baseline": "基础", "math": "数学", "search": "搜索", "style": "启发式风格",
    "nn": "模仿学习 NN", "rl": "强化学习 RL", "acfr": "CFR",
}
PROFILES = {"tsumogiri": "摸切", "random": "随机", "win-random": "先胡随机", "math": "数学", "search": "搜索"}
for prefix, label in (("", "风格"), ("nn-", "NN"), ("rl-", "RL"), ("acfr-", "CFR")):
    PROFILES.update({prefix + style: f"{label} {name}" for style, name in STYLE_NAMES.items()})


def family(profile):
    if profile not in PROFILES:
        raise ValueError("Unknown practice policy")
    for prefix in ("nn", "rl", "acfr"):
        if profile.startswith(prefix + "-"):
            return prefix
    return profile if profile in ("math", "search") else "style" if profile in STYLE_NAMES else "baseline"


def profile_states(model_info):
    """Only static availability, never a bot's current candidates or intention."""
    result = {}
    for profile in PROFILES:
        kind = family(profile)
        required = kind in {"nn", "rl", "acfr"}
        info = model_info.get(kind, {})
        available = not required or bool(info.get("available"))
        result[profile] = {"family": kind, "available": available,
                           "reason": "" if available else info.get("error", "未配置冻结模型")}
    return result


def make_practice_bot(profile, *, seed, seat, model_paths):
    """Construct the supported policies from independent frozen exports."""
    kind = family(profile)
    if kind in {"nn", "rl", "acfr"} and kind not in model_paths:
        raise ValueError("Required frozen model is unavailable")
    if kind == "acfr":
        from bots.abstract_cfr_bot import AbstractCfrBot
        return AbstractCfrBot(profile[5:], seed=seed, model_path=model_paths[kind])
    from bots.common_bot_helper.registry import make_bot
    return make_bot(profile, seed=seed, model_path=model_paths[kind] if kind in {"nn", "rl"} else None)


def decision_status(bot, profile):
    explanation = getattr(bot, "last_decision", None)
    neural = getattr(explanation, "neural", None)
    if neural is not None:
        return neural.status
    kind = family(profile)
    if kind == "acfr":
        return f"{kind}:{bot.last_status}"
    if kind == "baseline":
        return "baseline"
    return f"{kind}:budget_limited" if getattr(explanation, "budget_limited", False) else kind
