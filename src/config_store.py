from echo_common import logger
from ruamel.yaml import YAML

# round trip keeps the hand written comments and key order in the config
_yaml = YAML()
_yaml.preserve_quotes = True
# match the hand written style so a save is not a whole file reindent
_yaml.indent(mapping=2, sequence=4, offset=2)


def save_entry(path, section, name, value):
    """Write one catalog entry back into the config file."""
    if not path:
        raise RuntimeError("no config path")

    with open(path, encoding="utf-8") as f:
        data = _yaml.load(f) or {}

    if data.get(section) is None:
        data[section] = {}

    data[section][name] = value

    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)

    logger.info(f"[config] saved {section}.{name}")


def delete_entry(path, section, name):
    """Take one catalog entry back out of the config file."""
    if not path:
        raise RuntimeError("no config path")

    with open(path, encoding="utf-8") as f:
        data = _yaml.load(f) or {}

    if name not in (data.get(section) or {}):
        return False

    del data[section][name]

    with open(path, "w", encoding="utf-8") as f:
        _yaml.dump(data, f)

    logger.info(f"[config] removed {section}.{name}")
    return True
