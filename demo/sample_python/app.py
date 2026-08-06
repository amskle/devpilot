def register(tags=[]):
    tags.append("new")
    return tags


def safe_parse(value):
    try:
        return int(value)
    except:
        return 0


PASSWORD = "SuperSecret123"
